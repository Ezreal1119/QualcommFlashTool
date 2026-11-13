"""Minimal, explicitly confirmed terminal flashing over the existing worker."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import selectors
import shlex
import signal
import subprocess
import sys
import time

from . import __version__
from .models import PROTOCOL_VERSION, DeviceInfo, FlashPlan
from .runtime import data_dir, root
from .storage import Store, atomic_json


class CLIError(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def text(value):
    """Keep untrusted paths and USB identifiers from inserting terminal controls."""
    return "".join(
        f"\\x{ord(c):02x}" if ord(c) < 32 or ord(c) == 127 else c for c in str(value)
    )


def error_message(value):
    return "\n  ".join(text(line) for line in str(value).splitlines())


class Console:
    def __init__(self, out=None, err=None):
        self.out = sys.stdout if out is None else out
        self.err = sys.stderr if err is None else err
        self.closed = False
        self.inline = False
        self.last_progress = 0.0
        self.stage = ""
        self.current_file = ""

    def _write(self, stream, value):
        try:
            encoding = getattr(stream, "encoding", None) or "utf-8"
            stream.write(
                value.encode(encoding, errors="backslashreplace").decode(encoding)
            )
            stream.flush()
        except (BrokenPipeError, OSError):
            self.closed = True

    def line(self, value="", *, error=False):
        if self.inline:
            self._write(self.out, "\n")
            self.inline = False
        self._write(self.err if error else self.out, str(value) + "\n")

    def status(self, value):
        if self.out.isatty():
            self._write(self.out, "\r\033[2K" + text(value))
            self.inline = True
        else:
            self.line(text(value))

    def event(self, kind, data):
        if kind == "stage":
            state = data["state"]
            self.stage = state
            detail = data.get("label") or ""
            if state == "flashing":
                filename = Path(data.get("file", "")).name
                detail = filename or detail
            self.current_file = detail if state == "flashing" else ""
            count = f" {data['index']}/{data['count']}" if "index" in data else ""
            label = state.replace("_", " ").capitalize() + count
            if detail:
                label += ": " + text(detail)
            if state in ("flashing", "patching"):
                self.status(label)
            else:
                self.line(label)
        elif kind == "progress" and time.monotonic() - self.last_progress >= 1:
            self.last_progress = time.monotonic()
            total = max(1, data["total"])
            self.status(
                f"  {100 * data['done'] / total:5.1f}% · {data['done'] / 1048576:.1f} / {total / 1048576:.1f} MB"
                + (f" · {self.current_file}" if self.current_file else "")
            )
        elif kind == "preflight":
            self.line("Device capacity and write-range checks passed.")


class SessionLock:
    """Share the installed GUI's existing lock without changing the GUI."""

    def __enter__(self):
        from PySide6.QtCore import QLockFile

        self.lock = QLockFile(str(data_dir() / "application.lock"))
        self.lock.setStaleLockTime(0)
        if not self.lock.tryLock(0):
            raise CLIError(
                "Another desktop or terminal session is running. Quit it before flashing."
            )
        return self

    def touch(self):
        os.utime(data_dir() / "application.lock", None)

    def __exit__(self, *args):
        self.lock.unlock()


@contextmanager
def interrupts(callback):
    previous = {}
    try:
        for name in ("SIGINT", "SIGTERM", "SIGHUP"):
            number = getattr(signal, name, None)
            if number is not None:
                previous[number] = signal.signal(number, callback)
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


@dataclass
class Reply:
    result: dict
    data: dict
    folder: Path


class WorkerRunner:
    def __init__(
        self,
        store,
        console,
        *,
        heartbeat=lambda: None,
        command=None,
        connect_timeout=30,
        idle_timeout=120,
        file_timeout=600,
        exit_timeout=5,
    ):
        self.store = store
        self.console = console
        self.heartbeat = heartbeat
        self.command = command or [sys.executable, "-u", "-m", "qft.app", "--worker"]
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.file_timeout = file_timeout
        self.exit_timeout = exit_timeout
        self.last_folder = None

    def run(self, action, payload):
        request = {"version": PROTOCOL_VERSION, "action": action, **payload}
        folder = self.store.begin("cli_" + action, request)
        self.last_folder = folder
        atomic_json(folder / "request.json", request)
        metadata, result = {}, None
        process = None
        stop_requested = False
        stop_sent = False
        stage = "checking"
        phase_started = activity = time.monotonic()
        result_at = None
        buffer = b""
        selector = selectors.DefaultSelector()

        def stop(*args):
            nonlocal stop_requested
            if not stop_requested:
                stop_requested = True
                self.console.line(
                    "Stop requested. Waiting for the current operation's safe boundary…",
                    error=True,
                )

        def fail(message):
            return {"status": "interrupted", "message": message, "rebooted": False}

        try:
            with (
                interrupts(stop),
                (folder / "raw.log").open("ab", buffering=0) as raw,
                (folder / "events.jsonl").open("ab", buffering=0) as events,
            ):
                process = subprocess.Popen(
                    self.command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=folder,
                    env={
                        **os.environ,
                        "PYTHONPATH": str(root()),
                        "PYTHONUNBUFFERED": "1",
                    },
                    # Terminal Ctrl+C must reach this supervisor, not interrupt USB
                    # writes in the worker. Cancellation travels over JSON Lines.
                    start_new_session=True,
                    bufsize=0,
                )
                outgoing = bytearray(
                    (json.dumps(request, ensure_ascii=False) + "\n").encode()
                )
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, "input")
                writing = True
                selector.register(process.stdout, selectors.EVENT_READ, "events")
                selector.register(process.stderr, selectors.EVENT_READ, "log")
                while selector.get_map():
                    now = time.monotonic()
                    self.heartbeat()
                    if self.console.closed:
                        stop()
                    if (
                        stop_requested
                        and not stop_sent
                        and result is None
                        and process.poll() is None
                    ):
                        outgoing.extend(b'{"action":"stop"}\n')
                        if not writing:
                            selector.register(
                                process.stdin, selectors.EVENT_WRITE, "input"
                            )
                            writing = True
                        stop_sent = True
                    if result_at is not None and now - result_at > self.exit_timeout:
                        if result.get("status") in ("ready", "success"):
                            result = {
                                "status": "failed",
                                "message": "Worker failed to exit after its result; success was discarded.",
                            }
                        if process.poll() is None:
                            process.kill()
                        break
                    if result is None:
                        limit = (
                            self.connect_timeout
                            if stage == "connecting"
                            else 75
                            if stage == "waiting_edl"
                            else self.file_timeout
                            if action == "prepare"
                            else self.idle_timeout
                        )
                        elapsed = (
                            now - phase_started
                            if stage in ("connecting", "waiting_edl")
                            else now - activity
                        )
                        if elapsed > limit:
                            result = fail(
                                f"{stage.capitalize()} timed out. Worker terminated; inspect the device before retrying."
                            )
                            result_at = now
                            process.kill()
                    for key, _ in selector.select(timeout=0.1):
                        if key.data == "input":
                            try:
                                count = os.write(key.fd, outgoing[:65536])
                            except BlockingIOError:
                                continue
                            except OSError as exc:
                                selector.unregister(key.fileobj)
                                writing = False
                                outgoing.clear()
                                if result is None:
                                    raise CLIError(
                                        f"Worker command channel failed: {exc}"
                                    ) from exc
                                continue
                            del outgoing[:count]
                            if not outgoing:
                                selector.unregister(key.fileobj)
                                writing = False
                            continue
                        block = os.read(key.fd, 65536)
                        if not block:
                            selector.unregister(key.fileobj)
                            continue
                        if key.data == "log":
                            raw.write(block)
                            continue
                        buffer += block
                        if len(buffer) > 16 * 1024 * 1024:
                            raise CLIError(
                                "Worker event exceeded the protocol size limit"
                            )
                        while b"\n" in buffer:
                            line, buffer = buffer.split(b"\n", 1)
                            if not line.strip():
                                continue
                            event = json.loads(line)
                            if event.get(
                                "version"
                            ) != PROTOCOL_VERSION or not isinstance(
                                event.get("data"), dict
                            ):
                                raise CLIError("Invalid worker protocol")
                            events.write(line + b"\n")
                            kind, data = event["kind"], event["data"]
                            now = time.monotonic()
                            if kind in (
                                "activity",
                                "progress",
                                "scan_progress",
                                "import_progress",
                            ):
                                activity = now
                            if kind == "stage":
                                stage = data["state"]
                                phase_started = activity = now
                            if kind in (
                                "package",
                                "plan",
                                "devices",
                                "environment",
                                "preflight",
                                "device_bound",
                            ):
                                metadata[kind] = data
                                if kind in ("plan", "preflight", "device_bound"):
                                    atomic_json(folder / (kind + ".json"), data)
                            if kind == "log":
                                raw.write(data.get("text", "").encode())
                            if kind == "result":
                                if data.get("status") not in {
                                    "ready",
                                    "success",
                                    "failed",
                                    "interrupted",
                                    "incomplete",
                                    "reboot_failed",
                                    "attention",
                                } or not isinstance(data.get("message"), str):
                                    raise CLIError("Invalid worker result")
                                if result is not None:
                                    # A timeout must not be overwritten by a late success.
                                    if process.poll() is None:
                                        process.kill()
                                    continue
                                result = data
                                result_at = now
                            self.console.event(kind, data)
                process.wait(timeout=5)
                if buffer.strip():
                    raise CLIError("Worker exited with an incomplete event")
                if result is None:
                    result = fail(
                        f"Worker exited ({process.returncode}) without a result. Device state may be incomplete."
                    )
                elif process.returncode != 0 and result.get("status") in (
                    "ready",
                    "success",
                ):
                    result = {
                        "status": "failed",
                        "message": f"Worker exited abnormally ({process.returncode}); its success response was discarded.",
                    }
                if stop_requested and action in ("prepare", "scan"):
                    result = fail("Cancelled before flashing.")
        except Exception as exc:
            result = {"status": "failed", "message": f"{type(exc).__name__}: {exc}"}
        finally:
            selector.close()
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream:
                        try:
                            stream.close()
                        except OSError:
                            pass
            result = result or fail(
                "Session interrupted. Inspect the device before retrying."
            )
            self.store.finish(folder, result)
        return Reply(result, metadata, folder)


def ensure_runtime(console):
    manifest = json.loads((root() / "resources/runtime-manifest.json").read_text())
    adb = root() / "resources/bin/adb"
    ready = (
        adb.is_file()
        and os.access(adb, os.X_OK)
        and hashlib.sha256(adb.read_bytes()).hexdigest() == manifest["adb_sha256"]
        and (root() / "resources/lib/libusb-1.0.dylib").is_file()
    )
    if ready:
        return
    helper = root() / "scripts/prepare_resources.sh"
    if not helper.is_file():
        raise CLIError(
            "Runtime resources are missing. Run the terminal version from the source checkout."
        )
    log = data_dir() / "cli-bootstrap.log"
    console.line("Preparing project ADB/libusb…")
    with log.open("wb") as stream:
        result = subprocess.run([str(helper)], stdout=stream, stderr=subprocess.STDOUT)
    if result.returncode:
        raise CLIError(f"Runtime preparation failed. See {log}")


def checked(reply, allowed):
    if reply.result.get("status") not in allowed:
        raise CLIError(
            reply.result.get("message", "Worker failed without an explanation"),
            130 if reply.result.get("status") == "interrupted" else 1,
        )
    return reply


def folder_path(value, *, interactive=False):
    path = Path(value).expanduser()
    if interactive and not path.is_dir():
        try:
            values = shlex.split(value)
        except ValueError:
            values = []
        if len(values) == 1:
            path = Path(values[0]).expanduser()
    if not path.is_dir():
        raise CLIError(
            "Choose an existing extracted firmware folder; ZIP files are not accepted."
        )
    return path.resolve(strict=True)


def select_device(report):
    if report.get("errors"):
        raise CLIError("Device detection failed: " + "; ".join(report["errors"]))
    devices = report.get("devices", [])
    if not devices:
        raise CLIError(
            "No device connected. Connect USB and authorize ADB, or connect one EDL device."
        )
    if len(devices) != 1:
        raise CLIError(
            "Multiple devices detected. Connect only the intended target and try again."
        )
    device = DeviceInfo(**devices[0])
    if device.mode == "adb":
        if device.status == "unauthorized":
            raise CLIError(
                "ADB is unauthorized. Unlock the device and allow USB debugging."
            )
        if device.status != "device":
            raise CLIError("ADB device is offline or unavailable. Reconnect USB.")
        if not device.usb_path:
            raise CLIError(
                "Cannot establish the ADB device's USB port. Use a wired USB connection."
            )
    elif device.mode != "edl":
        raise CLIError("Unsupported device mode")
    return device


def show_summary(console, plan, device, plan_file):
    from .package import layout_notes

    console.line("\nReview before flashing")
    console.line(
        f"  Device  : {text(device.name)} · {device.mode.upper()} · {text(device.serial or device.id)}"
    )
    console.line(f"  USB     : {text(device.usb_path or device.id)}")
    console.line(f"  Folder  : {text(plan.folder)}")
    console.line(f"  Loader  : {text(plan.loader)}")
    console.line(
        f"  Storage : {plan.memory.upper()} (package hint; device checks follow confirmation)"
    )
    total = sum(op.sectors * op.sector_size for op in plan.programs)
    console.line(
        f"  Writes  : {len(plan.programs)} · {total / 1073741824:.2f} GB · LUNs {', '.join(str(n) for n in sorted({op.lun for op in (*plan.programs, *plan.patches)}))}"
    )
    console.line(f"  Patches : {len(plan.patches)}")
    console.line(
        "  XML     : "
        + ", ".join(text(Path(p).name) for p in (*plan.raw_files, *plan.patch_files))
    )
    console.line(f"  Plan    : {text(plan_file)} (full files and sector ranges)")
    for note in layout_notes(plan):
        console.line("  Note    : " + text(note))
    console.line(
        "\nThis overwrites device storage. Confirm this firmware is intended for this model."
    )
    console.line(
        "ADB enters EDL automatically. Reboot follows successful verification only."
    )


def flash_session(folder, runner, console, read):
    console.line("Checking firmware files…")
    prepared = runner.run("prepare", {"source": str(folder)})
    if prepared.data.get("package", {}).get("issues"):
        raise CLIError(
            "Firmware selection needs attention:\n"
            + "\n".join(prepared.data["package"]["issues"])
        )
    checked(prepared, {"ready"})
    plan_data = prepared.data.get("plan")
    if not plan_data:
        raise CLIError("No validated execution plan was returned")
    plan = FlashPlan.from_dict(plan_data)
    console.line("Detecting device…")
    scanned = checked(runner.run("scan", {}), {"success"})
    environment = scanned.data.get("environment", {}).get("checks", [])
    failures = [
        c for c in environment if c["name"] != "Hardware validation" and not c["ok"]
    ]
    if failures:
        raise CLIError("; ".join(c["detail"] for c in failures))
    device = select_device(scanned.data.get("devices", {}))
    show_summary(console, plan, device, prepared.folder / "plan.json")
    try:
        confirmation = read("\nType FLASH to confirm, or Enter to cancel: ")
    except EOFError:
        confirmation = ""
    if confirmation.strip() != "FLASH":
        console.line("Cancelled. Nothing was flashed.")
        return 2
    from dataclasses import asdict

    completed = runner.run(
        "one_click", {"plan": plan_data, "device": asdict(device), "reboot": True}
    )
    result = completed.result
    status = result.get("status")
    if status == "success" and result.get("rebooted") and not result.get("unverified"):
        console.line(f"\nSUCCESS: {result.get('message', 'Flash completed.')}")
        console.line(
            f"Verification: {len(result.get('verified', []))} checks passed. Reboot requested."
        )
        console.line(f"Logs: {completed.folder}")
        return 0
    console.line(
        "ERROR: "
        + error_message(result.get("message", "Flash did not complete successfully.")),
        error=True,
    )
    for item in result.get("unverified", []):
        console.line("Not verified: " + text(item), error=True)
    console.line(f"Logs: {completed.folder}", error=True)
    return (
        130
        if status == "interrupted"
        else 3
        if status in ("incomplete", "reboot_failed", "success")
        else 1
    )


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="qft-flash",
        description="Select firmware, review one device, type FLASH, then flash/verify/reboot.",
    )
    parser.add_argument(
        "folder", nargs="?", help="Extracted firmware directory; prompts when omitted"
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    args = parser.parse_args(argv)
    console = Console()
    runner = None
    try:
        value = (
            args.folder
            if args.folder is not None
            else input("Firmware folder: ").strip()
        )
        if not value:
            raise CLIError("No firmware folder selected")
        folder = folder_path(value, interactive=args.folder is None)
        with SessionLock() as lock:
            ensure_runtime(console)
            runner = WorkerRunner(Store(), console, heartbeat=lock.touch)
            return flash_session(folder, runner, console, input)
    except KeyboardInterrupt:
        console.line("\nInterrupted.", error=True)
        return 130
    except EOFError:
        console.line("Cancelled. No firmware folder selected.")
        return 2
    except Exception as exc:
        console.line("ERROR: " + error_message(exc), error=True)
        if runner and runner.last_folder:
            console.line(f"Logs: {runner.last_folder}", error=True)
        return getattr(exc, "code", 1)


if __name__ == "__main__":
    raise SystemExit(main())
