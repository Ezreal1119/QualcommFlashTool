import sys
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QProcess, QUrl
from PySide6.QtWidgets import QApplication
from qft.controller import Controller
from qft.expressions import ValidationError
from qft.package import discover


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def c(app, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    controller = Controller(demo=True)
    yield controller
    controller.timer.stop()
    if controller.proc:
        controller.proc.kill()
        controller.proc.waitForFinished(1000)


def test_selection_requires_explicit_confirmation(c, package, monkeypatch):
    calls = []
    notifications = []
    monkeypatch.setattr(
        c, "launch", lambda action, payload: calls.append((action, payload))
    )
    c.folderConfirmationRequested.connect(lambda: notifications.append(True))
    c.requestFolder(str(package))
    assert c.data["folderToConfirm"] == str(package) and notifications
    assert not calls
    c.cancelFolderSelection()
    assert not calls and not c.data["folderToConfirm"]
    c.requestFolder(str(package))
    c.confirmFolder()
    assert calls == [("prepare", {"source": str(package)})]


def test_drop_also_requires_confirmation(c, package, monkeypatch):
    calls = []
    monkeypatch.setattr(c, "launch", lambda *args: calls.append(args))
    c.dropFolder(QUrl.fromLocalFile(str(package)).toString())
    assert c.data["folderToConfirm"] == str(package) and not calls


def test_zip_selection_and_drop_never_scan(c, tmp_path, monkeypatch):
    path = tmp_path / "firmware.zip"
    path.write_bytes(b"not a directory")
    calls = []
    monkeypatch.setattr(c, "launch", lambda *args: calls.append(args))
    c.requestFolder(str(path))
    c.dropFolder(QUrl.fromLocalFile(str(path)).toString())
    c.loadFolder(str(path))
    assert not calls and not c.data["folderToConfirm"]


def test_entire_mount_rejected_before_walk(tmp_path, monkeypatch):
    monkeypatch.setattr("qft.package.os.path.ismount", lambda path: True)
    monkeypatch.setattr(
        "qft.package.os.scandir",
        lambda path: pytest.fail("Mount must not be traversed"),
    )
    with pytest.raises(ValidationError, match="entire disk"):
        discover(tmp_path)


def test_scan_cancelled_between_entries(tmp_path):
    for i in range(30):
        (tmp_path / f"{i}.txt").touch()
    calls = []

    def stopped():
        calls.append(True)
        return len(calls) > 5

    with pytest.raises(ValidationError, match="cancelled"):
        discover(tmp_path, stopped=stopped)
    assert len(calls) == 6


def test_entry_and_depth_limits_never_return_partial_results(package):
    with pytest.raises(ValidationError, match="too large"):
        discover(package, max_entries=2)
    nested = package / "one" / "two"
    nested.mkdir(parents=True)
    with pytest.raises(ValidationError, match="too deep"):
        discover(package, max_depth=1)


def test_time_limit(tmp_path, monkeypatch):
    (tmp_path / "test.xml").touch()
    clock = iter((0, 16))
    monkeypatch.setattr("qft.package.time.monotonic", lambda: next(clock))
    with pytest.raises(ValidationError, match="too large"):
        discover(tmp_path, timeout=15)


def test_hidden_and_symlink_directories_are_not_traversed(package, tmp_path):
    hidden = package / ".Spotlight-V100"
    hidden.mkdir()
    (hidden / "rawprogram1.xml").write_text("<data/>")
    external = tmp_path / "external"
    external.mkdir()
    (external / "prog_wrong.elf").write_bytes(b"wrong")
    (package / "link").symlink_to(external, target_is_directory=True)
    result = discover(package)
    assert len(result["raws"]) == len(result["loaders"]) == 1


def active_fake(c, monkeypatch, action):
    callbacks = []
    kills = []
    writes = []
    proc = SimpleNamespace(
        write=lambda data: writes.append(data),
        kill=lambda: kills.append(True),
        deleteLater=lambda: None,
    )
    c.proc = proc
    c.action = action
    c.task_dir = c.store.begin(action, {})
    c.update(busy=True)
    monkeypatch.setattr(
        "qft.controller.QTimer.singleShot",
        lambda ms, callback: callbacks.append(callback),
    )
    monkeypatch.setattr(c, "read_wire", lambda: None)
    monkeypatch.setattr(c, "read_logs", lambda: None)
    return proc, callbacks, kills, writes


def test_file_cancel_has_bounded_fallback_and_discards_late_results(c, monkeypatch):
    proc, callbacks, kills, writes = active_fake(c, monkeypatch, "prepare")
    c._auto_check = True
    c.stop()
    assert c.data["canChoosePackage"] and not c.data["closeBlocked"] and callbacks
    c.handle("plan", {"memory": "ufs", "programs": [{"bad": "stale"}]})
    assert not c.data["plan"]
    callbacks[0]()
    assert kills
    c.result = {"status": "ready", "message": "late result"}
    c.finished(-1, QProcess.CrashExit)
    assert (
        c.data["state"] == "interrupted"
        and not c.data["canFlash"]
        and not c._auto_check
    )


def test_old_cancel_timer_cannot_kill_replacement_worker(c, monkeypatch):
    old, callbacks, kills, _ = active_fake(c, monkeypatch, "prepare")
    c.stop()
    c.proc = SimpleNamespace(kill=lambda: pytest.fail("Replacement worker killed"))
    callbacks[0]()
    assert not kills
    c.proc = None


def test_reselect_waits_for_cancelled_worker_then_starts_new_folder(
    c, monkeypatch, package
):
    old, callbacks, kills, _ = active_fake(c, monkeypatch, "prepare")
    calls = []
    monkeypatch.setattr(
        c, "launch", lambda action, payload: calls.append((action, payload))
    )
    c.loadFolder(str(package))
    assert c._pending_source == str(package) and not calls
    c.finished(-1, QProcess.CrashExit)
    assert calls == [("prepare", {"source": str(package)})]
    callbacks[0]()
    assert not kills


@pytest.mark.parametrize("action", ["flash", "reset", "enter_edl", "one_click"])
def test_device_actions_keep_boundary_stop_and_close_protection(c, monkeypatch, action):
    proc, callbacks, kills, writes = active_fake(c, monkeypatch, action)
    c.stop()
    assert c.data["closeBlocked"] and not c.data["canChoosePackage"]
    assert writes and not callbacks and not kills
    c.proc = None


def test_close_during_scan_cancels_then_signals_quit(c, monkeypatch):
    proc, callbacks, kills, _ = active_fake(c, monkeypatch, "prepare")
    quit_events = []
    c.quitRequested.connect(lambda: quit_events.append(True))
    c.quitAfterStop()
    assert not quit_events and not c.data["canChoosePackage"]
    callbacks[0]()
    c.finished(-1, QProcess.CrashExit)
    assert quit_events == [True] and not c.proc


def test_real_unresponsive_worker_cancels_without_freezing(app, c):
    c.update(demo=False)
    c.action = "prepare"
    c.task_dir = c.store.begin("prepare", {})
    proc = QProcess(c)
    c.proc = proc
    proc.finished.connect(c.finished)
    proc.setProgram(sys.executable)
    proc.setArguments(["-c", "import time; time.sleep(60)"])
    proc.start()
    assert proc.waitForStarted(1000)
    c.started = c.activity = c.phase_started = time.monotonic()
    c.update(busy=True)
    start = time.monotonic()
    c.stop()
    while c.proc and time.monotonic() - start < 2:
        app.processEvents()
        time.sleep(0.005)
    assert c.proc is None and time.monotonic() - start < 2
    assert c.data["state"] == "interrupted" and c.data["canChoosePackage"]
