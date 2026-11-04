from types import SimpleNamespace
import pytest
from qft.backend import EDLBackend
from qft.models import DeviceInfo
from qft.devices import enter_edl
from qft.controller import Controller
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def backend(plan, rsp):
    b = EDLBackend(DeviceInfo("usb:1:2", "edl", "Test", "ready"), plan, lambda *a: None)
    b.fh = SimpleNamespace(
        cfg=SimpleNamespace(MaxPayloadSizeToTargetInBytes=512),
        xmlsend=lambda _: rsp,
        xml=SimpleNamespace(
            getresponse=lambda _: {"value": "ACK"}, getlog=lambda _: ["NAK"]
        ),
        wait_for_data=lambda: b"ACK",
    )
    b.cdc = SimpleNamespace(write=lambda _: True)
    return b


def test_initial_nak_blocks_data_transfer(plan):
    b = backend(
        plan,
        SimpleNamespace(resp=False, data={"value": "NAK"}, error="GPT write failed"),
    )
    sent = []
    b.cdc.write = lambda data: sent.append(data)
    with pytest.raises(IOError, match="rejected"):
        b.program(plan.programs[0], 10, [b"A" * 512], lambda n: None)
    assert sent == []


def test_final_nak_is_failure(plan):
    b = backend(plan, SimpleNamespace(resp=True, data={"value": "ACK"}, error=""))
    b.fh.xml.getresponse = lambda _: {"value": "NAK"}
    # Initial ACK is provided as a dictionary, independent of final ACK parsing.
    with pytest.raises(IOError, match="final ACK"):
        b.program(plan.programs[0], 10, [b"A" * 512], lambda n: None)


def test_patch_nak_is_failure(plan):
    b = backend(
        plan, SimpleNamespace(resp={"value": "NAK"}, data={}, error="patch failed")
    )
    from qft.models import Patch

    with pytest.raises(IOError):
        b.patch(Patch(0, "10", 0, 4, "1", 512), 10, 1)


@pytest.mark.parametrize("status", ["unauthorized", "offline"])
def test_enter_edl_requires_authorization(status):
    with pytest.raises(ValueError):
        enter_edl(DeviceInfo("adb:1", "adb", "Test", status, "serial"))


def test_ui_freezes_settings_and_plan_during_task(app, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path))
    c = Controller(demo=True)
    c.update(busy=True, plan={"marker": 1})
    c.option("memory", "emmc")
    c.selectLoader("/different.mbn")
    c.toggleFile("raw", "x", True)
    c.selectDevice(-1)
    assert c.data["memory"] == "ufs" and c.data["plan"] == {"marker": 1}
    assert c.data["deviceIndex"] == 0
    c.timer.stop()


def test_watchdog_kills_stalled_worker_without_reset(app, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path))
    c = Controller(demo=True)
    killed = []
    c.proc = SimpleNamespace(kill=lambda: killed.append(True))
    c.action = "flash"
    c.started = 0
    c.activity = 0
    c.phase_started = 0
    c.update(state="flashing")
    monkeypatch.setattr("qft.controller.time.monotonic", lambda: 121)
    c.tick()
    assert (
        killed
        and c.result["status"] == "interrupted"
        and "No reset" in c.result["message"]
    )
    c.proc = None
    c.timer.stop()


def test_active_progress_extends_idle_deadline(app, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path))
    c = Controller(demo=True)
    c.update(state="flashing")
    killed = []
    c.proc = SimpleNamespace(kill=lambda: killed.append(True))
    c.action = "flash"
    c.started = 0
    c.phase_started = 0
    monkeypatch.setattr("qft.controller.time.monotonic", lambda: 600)
    c.handle("progress", {"done": 512, "total": 1024})
    c.tick()
    assert not killed and c.data["progress"] == 0.5
    c.proc = None
    c.timer.stop()


def test_controller_worker_roundtrip(app, tmp_path, monkeypatch, package):
    import time

    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    c = Controller()

    def wait():
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            app.processEvents()
            if c.proc is None and not c.data["busy"]:
                return
            time.sleep(0.01)
        c.close()
        pytest.fail("Worker did not finish")

    app.processEvents()
    wait()
    assert c.data["environment"]
    c.loadFolder(str(package))
    wait()
    assert c.data["loader"] == str(package / "prog_firehose.mbn")
    c.option("memory", "emmc")
    c.validate()
    wait()
    assert (
        c.data["state"] == "ready" and c.data["plan"]["programs"][0]["label"] == "boot"
    )
    assert len(c.store.history()) == 3
    c.timer.stop()


@pytest.mark.parametrize(
    "fields",
    [
        {"total_blocks": 128},
        {"total_blocks": 128, "block_size": 4096},
        {"total_blocks": 128, "block_size": 512, "physical_partition_number": 1},
    ],
)
def test_unknown_or_conflicting_geometry_is_rejected(fields):
    with pytest.raises(ValueError):
        EDLBackend.geometry(fields, 0, 512)


def test_explicit_geometry_is_required():
    assert (
        EDLBackend.geometry(
            {"total_blocks": 128, "block_size": 512, "page_size": 512}, 0, 512
        )
        == 128
    )


def test_qml_loads_pages_languages_and_themes(tmp_path):
    import os
    import subprocess
    import sys

    env = {
        **os.environ,
        "QT_QPA_PLATFORM": "offscreen",
        "QFT_DATA_DIR": str(tmp_path / "ui"),
    }
    result = subprocess.run(
        [sys.executable, "scripts/check_ui.py"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "16 UI configurations and large-list rendering: OK" in result.stdout
