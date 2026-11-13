from dataclasses import asdict
from io import StringIO
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import time

import pytest

from qft import cli
from qft.storage import Store


ADB = {
    "id": "adb:test",
    "name": "DT50",
    "mode": "adb",
    "status": "device",
    "serial": "test",
    "usb_path": "0-1",
}


@pytest.fixture
def console():
    return cli.Console(StringIO(), StringIO())


class FakeRunner:
    def __init__(
        self, plan, folder, *, devices=None, errors=None, result=None, issues=None
    ):
        self.plan = asdict(plan)
        self.folder = folder
        folder.mkdir()
        (folder / "plan.json").write_text(json.dumps(self.plan))
        self.devices = [ADB] if devices is None else devices
        self.errors = errors or []
        self.issues = issues or []
        self.result = result or {
            "status": "success",
            "message": "Verified and rebooted",
            "rebooted": True,
            "verified": ["GPT CRC", "boot digest"],
            "unverified": [],
        }
        self.calls = []
        self.last_folder = folder

    def run(self, action, payload):
        self.calls.append((action, payload))
        if action == "prepare":
            return cli.Reply(
                {
                    "status": "attention" if self.issues else "ready",
                    "message": "Check configuration",
                },
                {"plan": self.plan, "package": {"issues": self.issues}},
                self.folder,
            )
        if action == "scan":
            return cli.Reply(
                {"status": "success", "message": "Devices scanned"},
                {
                    "devices": {"devices": self.devices, "errors": self.errors},
                    "environment": {
                        "checks": [
                            {
                                "name": "Hardware validation",
                                "ok": False,
                                "detail": "Unverified",
                            }
                        ]
                    },
                },
                self.folder,
            )
        assert action == "one_click"
        return cli.Reply(self.result, {}, self.folder)


def session(plan, tmp_path, console, answer="FLASH", **kwargs):
    runner = FakeRunner(plan, tmp_path / "record", **kwargs)
    code = cli.flash_session(Path(plan.folder), runner, console, lambda _: answer)
    return code, runner


@pytest.mark.parametrize("answer", ["", "yes", "flash", "N"])
def test_no_exact_confirmation_never_dispatches_flash(plan, tmp_path, console, answer):
    code, runner = session(plan, tmp_path, console, answer=answer)
    assert code == 2 and [a for a, _ in runner.calls] == ["prepare", "scan"]
    assert "Nothing was flashed" in console.out.getvalue()


def test_eof_at_confirmation_cancels(plan, tmp_path, console):
    runner = FakeRunner(plan, tmp_path / "record")

    def eof(_):
        raise EOFError

    assert cli.flash_session(Path(plan.folder), runner, console, eof) == 2
    assert all(action != "one_click" for action, _ in runner.calls)


def test_confirmation_dispatches_original_plan_once_with_reboot(
    plan, tmp_path, console
):
    code, runner = session(plan, tmp_path, console)
    assert code == 0
    commands = [p for a, p in runner.calls if a == "one_click"]
    assert commands == [
        {"plan": asdict(plan), "device": asdict(cli.DeviceInfo(**ADB)), "reboot": True}
    ]
    assert "package hint" in console.out.getvalue()
    assert "full files and sector ranges" in console.out.getvalue()


@pytest.mark.parametrize(
    "devices,message",
    [
        ([], "No device"),
        ([ADB, {**ADB, "id": "adb:second", "serial": "second"}], "Multiple devices"),
        ([{**ADB, "status": "unauthorized"}], "unauthorized"),
        ([{**ADB, "status": "offline"}], "offline"),
        ([{**ADB, "usb_path": ""}], "USB port"),
    ],
)
def test_invalid_device_never_reaches_confirmation(
    plan, tmp_path, console, devices, message
):
    runner = FakeRunner(plan, tmp_path / "record", devices=devices)

    def must_not_prompt(_):
        pytest.fail("Invalid device reached confirmation")

    with pytest.raises(cli.CLIError, match=message):
        cli.flash_session(Path(plan.folder), runner, console, must_not_prompt)
    assert all(action != "one_click" for action, _ in runner.calls)


def test_scan_error_and_ambiguous_xml_stop_without_flash(plan, tmp_path, console):
    runner = FakeRunner(
        plan, tmp_path / "record", issues=["Missing patch pair for rawprogram0.xml"]
    )
    with pytest.raises(cli.CLIError, match="Missing patch pair"):
        cli.flash_session(Path(plan.folder), runner, console, lambda _: "FLASH")
    assert [a for a, _ in runner.calls] == ["prepare"]
    runner.issues = []
    runner.errors = ["USB: access denied"]
    with pytest.raises(cli.CLIError, match="access denied"):
        cli.flash_session(Path(plan.folder), runner, console, lambda _: "FLASH")
    assert all(a != "one_click" for a, _ in runner.calls)


@pytest.mark.parametrize(
    "status,code",
    [
        ("failed", 1),
        ("interrupted", 130),
        ("incomplete", 3),
        ("reboot_failed", 3),
        ("success", 3),
    ],
)
def test_failed_or_unverified_flash_is_nonzero_without_further_commands(
    plan, tmp_path, console, status, code
):
    result = {
        "status": status,
        "message": "GPT operation failed",
        "rebooted": False,
        "unverified": ["boot digest unavailable"],
    }
    actual, runner = session(plan, tmp_path, console, result=result)
    assert actual == code
    assert [a for a, _ in runner.calls] == ["prepare", "scan", "one_click"]
    assert "GPT operation failed" in console.err.getvalue()
    assert "SUCCESS" not in console.out.getvalue()


def test_interactive_paths_accept_quotes_and_escaped_spaces(package):
    assert (
        cli.folder_path(shlex.quote(str(package)), interactive=True)
        == package.resolve()
    )
    assert (
        cli.folder_path(str(package).replace(" ", "\\ "), interactive=True)
        == package.resolve()
    )
    assert cli.folder_path(str(package)) == package.resolve()


def test_invalid_folder_fails_before_lock_or_runtime(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        cli, "ensure_runtime", lambda _: pytest.fail("Invalid folder prepared runtime")
    )
    assert cli.main([str(tmp_path / "missing")]) == 1
    assert "existing extracted firmware" in capsys.readouterr().err


def test_import_and_help_do_not_load_gui():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            'import qft.cli,sys; assert "PySide6.QtWidgets" not in sys.modules; assert "qft.controller" not in sys.modules; qft.cli.main(["--help"])',
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert "qft-flash" in result.stdout


def test_lock_cooperates_with_existing_desktop_lock(tmp_path, monkeypatch):
    from PySide6.QtCore import QLockFile

    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path))
    desktop = QLockFile(str(tmp_path / "application.lock"))
    assert desktop.tryLock(0)
    try:
        with pytest.raises(cli.CLIError, match="Another desktop or terminal"):
            with cli.SessionLock():
                pytest.fail("Desktop lock was ignored")
    finally:
        desktop.unlock()
    with cli.SessionLock() as session_lock:
        session_lock.touch()
    assert not (tmp_path / "application.lock").exists()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "app-data"))
    return Store()


def simulated_worker(store, console, body, **kwargs):
    prefix = """import json,sys,time,os
request=json.loads(sys.stdin.readline())
def emit(kind,data):
    print(json.dumps({"version":1,"kind":kind,"data":data}),flush=True)
"""
    return cli.WorkerRunner(
        store, console, command=[sys.executable, "-u", "-c", prefix + body], **kwargs
    )


def test_real_worker_offline_validation_and_missing_image_are_recorded(
    store, console, package
):
    runner = cli.WorkerRunner(store, console)
    valid = runner.run("prepare", {"source": str(package)})
    assert valid.result["status"] == "ready" and valid.data["plan"]["files"]
    assert (valid.folder / "plan.json").is_file()
    (package / "boot.img").unlink()
    failed = runner.run("prepare", {"source": str(package)})
    assert failed.result["status"] == "failed"
    record = json.loads((failed.folder / "record.json").read_text())
    assert record["status"] == "failed" and record["action"] == "cli_prepare"
    assert (failed.folder / "raw.log").is_file()


def test_success_result_cannot_hide_abnormal_worker_exit(store, console):
    runner = simulated_worker(
        store,
        console,
        'emit("result",{"status":"success","message":"GPT okay"}); sys.exit(9)',
    )
    reply = runner.run("one_click", {})
    assert reply.result["status"] == "failed" and "discarded" in reply.result["message"]


@pytest.mark.parametrize(
    "body",
    [
        'print("{broken",flush=True)',
        'print(json.dumps({"version":99,"kind":"result","data":{}}),flush=True)',
        'emit("result",{"status":"unexpected"})',
    ],
)
def test_invalid_worker_protocol_is_a_recorded_failure(store, console, body):
    reply = simulated_worker(store, console, body).run("one_click", {})
    assert reply.result["status"] == "failed"
    assert json.loads((reply.folder / "record.json").read_text())["status"] == "failed"


def test_worker_exit_without_result_is_interrupted(store, console):
    reply = simulated_worker(store, console, "sys.exit(0)").run("one_click", {})
    assert (
        reply.result["status"] == "interrupted"
        and "without a result" in reply.result["message"]
    )


def test_connection_activity_cannot_extend_connection_deadline(store, console):
    body = """emit("stage",{"state":"connecting"})
for i in range(100):
    emit("activity",{})
    time.sleep(.02)
"""
    runner = simulated_worker(
        store, console, body, connect_timeout=0.15, idle_timeout=1
    )
    reply = runner.run("one_click", {})
    assert (
        reply.result["status"] == "interrupted"
        and "timed out" in reply.result["message"]
    )


def test_continuous_write_progress_has_no_fixed_total_timeout(store, console):
    body = """emit("stage",{"state":"flashing"})
for i in range(8):
    emit("progress",{"done":i+1,"total":8})
    time.sleep(.06)
emit("result",{"status":"success","message":"done","rebooted":True})
"""
    runner = simulated_worker(store, console, body, idle_timeout=0.2)
    started = time.monotonic()
    reply = runner.run("one_click", {})
    assert time.monotonic() - started > 0.2
    assert reply.result["status"] == "success"


def test_result_then_hung_process_is_not_success(store, console):
    runner = simulated_worker(
        store,
        console,
        'emit("result",{"status":"success","message":"done"});time.sleep(10)',
        exit_timeout=0.15,
    )
    reply = runner.run("one_click", {})
    assert (
        reply.result["status"] == "failed"
        and "failed to exit" in reply.result["message"]
    )


def test_ctrl_c_sends_safe_stop_to_isolated_worker_and_restores_signals(
    store, console, monkeypatch
):
    previous = signal.getsignal(signal.SIGINT)
    original = console.event

    def event(kind, data):
        original(kind, data)
        if kind == "stage":
            os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(console, "event", event)
    body = """assert os.getpgrp()!=request["parent_group"]
emit("stage",{"state":"flashing"})
stop=json.loads(sys.stdin.readline())
assert stop["action"]=="stop"
time.sleep(.05)
emit("result",{"status":"interrupted","message":"Stopped at safe boundary","rebooted":False})
"""
    runner = simulated_worker(store, console, body, idle_timeout=2)
    reply = runner.run("one_click", {"parent_group": os.getpgrp()})
    assert (
        reply.result["status"] == "interrupted"
        and "safe boundary" in reply.result["message"]
    )
    assert signal.getsignal(signal.SIGINT) == previous
    assert "Stop requested" in console.err.getvalue()


def test_worker_that_never_reads_large_request_is_still_timed_out(store, console):
    runner = cli.WorkerRunner(
        store,
        console,
        command=[sys.executable, "-c", "import time;time.sleep(10)"],
        idle_timeout=0.15,
    )
    started = time.monotonic()
    reply = runner.run("one_click", {"large_plan": "A" * 1048576})
    assert time.monotonic() - started < 3
    assert reply.result["status"] == "interrupted"
    assert "timed out" in reply.result["message"]
