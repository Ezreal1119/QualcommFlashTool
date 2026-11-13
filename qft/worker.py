import contextlib
import json
import logging
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from .models import PROTOCOL_VERSION, DeviceInfo, FlashPlan
from .runtime import setup_vendor


def main():
    wire = sys.stdout
    lock = threading.Lock()
    stopped = threading.Event()

    def emit(kind, data):
        with lock:
            wire.write(
                json.dumps(
                    {"version": PROTOCOL_VERSION, "kind": kind, "data": data},
                    ensure_ascii=False,
                )
                + "\n"
            )
            wire.flush()

    try:
        request = json.loads(sys.stdin.readline())
        if request.get("version") != PROTOCOL_VERSION:
            raise ValueError("Worker protocol version mismatch")

        def reader():
            for line in sys.stdin:
                try:
                    if json.loads(line).get("action") == "stop":
                        stopped.set()
                except ValueError:
                    pass

        threading.Thread(target=reader, daemon=True).start()
        with contextlib.redirect_stdout(sys.stderr):
            logging.basicConfig(stream=sys.stderr, level=logging.INFO)
            action = request["action"]
            setup_vendor()
            if action == "environment":
                from .devices import environment

                from .devices import scan, connection_message

                emit("environment", {"checks": environment()})
                report = scan()
                emit("devices", report)
                result = {"status": "success", "message": connection_message(report)}
            elif action == "scan":
                from .devices import scan, environment, connection_message

                report = scan()
                emit("devices", report)
                emit("environment", {"checks": environment()})
                result = {"status": "success", "message": connection_message(report)}
            elif action == "prepare":
                from .firmware import import_firmware, automatic_configuration
                from .package import make_plan

                if not Path(request["source"]).is_dir():
                    raise ValueError(
                        "Only extracted firmware folders are accepted. ZIP import is disabled."
                    )
                folder = import_firmware(request["source"], emit, stopped.is_set)
                config = automatic_configuration(
                    folder,
                    stopped=stopped.is_set,
                    progress=lambda data: emit("scan_progress", data),
                )
                emit("package", config)
                if config["issues"]:
                    result = {
                        "status": "attention",
                        "message": "Some package choices need your review. Open Advanced configuration.",
                    }
                else:
                    plan = make_plan(
                        folder,
                        config["loader"],
                        config["memoryHint"],
                        config["selectedRaws"],
                        config["selectedPatches"],
                        stopped=stopped.is_set,
                    )
                    emit("plan", asdict(plan))
                    result = {
                        "status": "ready",
                        "message": "Package files checked. Checking the connected device next.",
                    }
            elif action == "discover":
                from .package import discover

                emit(
                    "package",
                    discover(
                        request["folder"],
                        stopped=stopped.is_set,
                        progress=lambda data: emit("scan_progress", data),
                    ),
                )
                result = {
                    "status": "success",
                    "message": "Package candidates loaded. Select XML variants and loader, then validate.",
                }
            elif action == "validate":
                from .package import make_plan

                memory = request["memory"]
                if request.get("memory_source") == "auto":
                    from .firmware import infer_memory

                    memory = infer_memory(request["raws"])
                    if not memory:
                        raise ValueError(
                            "Storage type cannot be inferred. Choose it in Advanced configuration."
                        )
                plan = make_plan(
                    request["folder"],
                    request["loader"],
                    memory,
                    request["raws"],
                    request["patches"],
                    stopped=stopped.is_set,
                )
                emit("plan", asdict(plan))
                result = {
                    "status": "ready",
                    "message": "Package files validated. Inspect the selected EDL device next.",
                }
            elif action == "one_click":
                from .workflow import execute_one_click

                result = asdict(
                    execute_one_click(
                        FlashPlan.from_dict(request["plan"]),
                        DeviceInfo(**request["device"]),
                        emit,
                        stopped.is_set,
                        reboot=request.get("reboot", True),
                        expected_capacities=request.get("capacities"),
                    )
                )
            elif action == "enter_edl":
                from .devices import enter_edl

                enter_edl(DeviceInfo(**request["device"]))
                result = {
                    "status": "success",
                    "message": "EDL requested. Refresh devices and select the EDL device; identity must be re-established.",
                }
            elif action in ("inspect", "flash", "reset"):
                from .backend import EDLBackend
                from .engine import execute

                plan = FlashPlan.from_dict(request["plan"])
                device = DeviceInfo(**request["device"])
                backend = EDLBackend(device, plan, emit)
                result = asdict(
                    execute(
                        plan,
                        backend,
                        emit,
                        stopped.is_set,
                        action,
                        request.get("reboot", True),
                        request.get("capacities"),
                    )
                )
            else:
                raise ValueError("Unsupported worker action")
        emit("result", result)
    except BaseException as exc:
        import traceback

        traceback.print_exc(file=sys.stderr)
        emit("result", {"status": "failed", "message": f"{type(exc).__name__}: {exc}"})
