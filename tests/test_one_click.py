from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication
from conftest import FakeBackend
from qft.controller import Controller
from qft.devices import connection_message, usb_location, scan
from qft.models import DeviceInfo
from qft.workflow import execute_one_click


ADB = DeviceInfo("adb:test", "adb", "DT50", "device", "test", usb_path="0-1.4.3")
EDL = DeviceInfo(
    "usb:0:6", "edl", "Qualcomm 9008", "ready", "chip-serial", 0, 6, usb_path="0-1.4.3"
)


def report(*devices, errors=()):
    return {"devices": [asdict(d) for d in devices], "errors": list(errors)}


class Clock:
    value = 0

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def workflow(plan, reports, **kwargs):
    clock = Clock()
    events = []
    enters = []
    created = []
    backend = FakeBackend()
    pending = list(reports)

    def scan_fn():
        result = pending[0]
        if len(pending) > 1:
            pending.pop(0)
        return result

    def factory(device, plan, emit):
        created.append(device)
        return backend

    result = execute_one_click(
        plan,
        kwargs.pop("source", ADB),
        lambda kind, data: events.append((kind, data)),
        kwargs.pop("stopped", lambda: False),
        scan_fn=scan_fn,
        enter_fn=lambda d: enters.append(d),
        backend_factory=factory,
        clock=clock.now,
        sleep=clock.sleep,
        timeout=2,
        **kwargs,
    )
    return result, backend, events, enters, created


def test_authorized_adb_to_verified_flash_is_one_session(plan):
    result, backend, events, enters, created = workflow(
        plan, [report(ADB), report(ADB), report(EDL)]
    )
    assert result.status == "success" and result.rebooted
    assert enters == [ADB] and created == [EDL]
    assert backend.calls.count("connect") == 1 and "program" in backend.calls
    states = [data["state"] for kind, data in events if kind == "stage"]
    assert (
        states.index("entering_edl")
        < states.index("waiting_edl")
        < states.index("flashing")
    )
    binding = next(data for kind, data in events if kind == "device_bound")
    assert (
        binding["device"]["usb_path"] == ADB.usb_path
        and binding["binding"] == "same_usb_port"
    )


def test_existing_edl_skips_adb_reboot(plan):
    result, backend, events, enters, created = workflow(plan, [report(EDL)], source=EDL)
    assert result.status == "success" and not enters and created == [EDL]


@pytest.mark.parametrize("status", ["unauthorized", "offline"])
def test_unavailable_adb_is_rejected_without_reboot_or_write(plan, status):
    bad = DeviceInfo(**{**asdict(ADB), "status": status})
    result, backend, _, enters, created = workflow(plan, [report(bad)])
    assert (
        result.status == "failed" and not enters and not created and not backend.calls
    )


def test_disconnected_target_is_reported_before_reboot(plan):
    result, backend, _, enters, created = workflow(plan, [report()])
    assert result.status == "failed" and "no longer connected" in result.message
    assert not enters and not created


def test_timeout_never_falls_through_to_flash(plan):
    result, backend, _, enters, created = workflow(plan, [report(ADB)])
    assert result.status == "failed" and "did not appear" in result.message
    assert len(enters) == 1 and not created and not backend.calls


def test_another_edl_before_transition_blocks_reboot(plan):
    result, _, _, enters, created = workflow(plan, [report(ADB, EDL)])
    assert result.status == "failed" and not enters and not created


@pytest.mark.parametrize("path", ["0-1.4.2", ""])
def test_edl_on_unconfirmed_port_is_never_flashed(plan, path):
    wrong = DeviceInfo(**{**asdict(EDL), "usb_path": path})
    result, backend, _, enters, created = workflow(plan, [report(ADB), report(wrong)])
    assert result.status == "failed" and "USB port" in result.message
    assert enters and not created and not backend.calls


def test_multiple_edl_candidates_are_not_guessed(plan):
    other = DeviceInfo(**{**asdict(EDL), "id": "usb:0:7", "address": 7})
    result, backend, _, _, created = workflow(plan, [report(ADB), report(EDL, other)])
    assert (
        result.status == "failed" and "Multiple EDL" in result.message and not created
    )


def test_unwired_adb_is_rejected_before_mode_change(plan):
    source = DeviceInfo(**{**asdict(ADB), "usb_path": ""})
    result, _, _, enters, created = workflow(plan, [report(source)], source=source)
    assert result.status == "failed" and not enters and not created


def test_changed_inputs_stop_before_any_device_command(plan):
    Path(plan.loader).write_bytes(b"changed")
    result, _, _, enters, created = workflow(plan, [report(ADB)])
    assert result.status == "failed" and not enters and not created


def test_cancel_while_waiting_never_constructs_flash_backend(plan):
    checks = [0]

    def stopped():
        checks[0] += 1
        return checks[0] >= 6

    result, backend, _, _, created = workflow(plan, [report(ADB)], stopped=stopped)
    assert result.status == "interrupted" and not created and not backend.calls


def test_adb_must_become_unavailable_before_binding_edl(plan):
    result, _, events, _, created = workflow(
        plan, [report(ADB), report(ADB, EDL), report(EDL)]
    )
    assert result.status == "success" and created == [EDL]
    assert sum(kind == "activity" for kind, data in events) >= 1


@pytest.mark.parametrize(
    "devices,expected",
    [
        ((), "No device connected"),
        ((DeviceInfo(**{**asdict(ADB), "status": "unauthorized"}),), "authorization"),
        ((DeviceInfo(**{**asdict(ADB), "status": "offline"}),), "offline"),
        ((ADB,), "Connected: DT50"),
    ],
)
def test_device_detection_messages(devices, expected):
    assert expected in connection_message(report(*devices))


def test_scan_keeps_adb_and_edl_physical_usb_topology(monkeypatch):
    monkeypatch.setattr("qft.devices.adb_path", lambda: "/fake/adb")
    monkeypatch.setattr("qft.devices.usb_backend", lambda: object())
    monkeypatch.setattr(
        "qft.devices.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="List of devices attached\ntest device usb:0-1.4.3 model:DT50\n",
            stderr="",
        ),
    )
    device = SimpleNamespace(
        bus=0, address=6, port_numbers=(1, 4, 3), serial_number="chip-serial"
    )
    monkeypatch.setattr("usb.core.find", lambda **kwargs: [device])
    found = scan()["devices"]
    assert found[0]["usb_path"] == found[1]["usb_path"] == "0-1.4.3"
    assert usb_location(SimpleNamespace(bus=0, port_numbers=())) == ""


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_confirmed_start_dispatches_one_workflow_and_preserves_preflight_gate(
    app, tmp_path, monkeypatch, plan
):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    c = Controller(demo=True)
    calls = []
    c.update(
        demo=False,
        devices=[asdict(ADB)],
        deviceIndex=0,
        plan=asdict(plan),
        packageStatus="passed",
        reboot=False,
    )
    assert c.data["canStart"] and not c.data["canFlash"]
    monkeypatch.setattr(
        c, "launch", lambda action, payload: calls.append((action, payload))
    )
    c.startFlash()
    assert (
        len(calls) == 1
        and calls[0][0] == "one_click"
        and calls[0][1]["device"]["serial"] == "test"
        and calls[0][1]["reboot"] is True
    )
    c.action = "one_click"
    c.update(busy=True)
    assert c.data["closeBlocked"] and not c.data["canChoosePackage"]
    c.timer.stop()


@pytest.mark.parametrize("status", ["unauthorized", "offline"])
def test_ui_blocks_start_and_explains_adb_state(
    app, tmp_path, monkeypatch, plan, status
):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    c = Controller(demo=True)
    c.update(
        devices=[{**asdict(ADB), "status": status}], deviceIndex=0, plan=asdict(plan)
    )
    assert not c.data["canStart"] and c.data["deviceNotice"] == status
    c.update(devices=[], deviceIndex=-1)
    assert c.data["deviceNotice"] == "missing" and not c.data["canStart"]
    c.timer.stop()


@pytest.mark.parametrize("action", ["environment", "scan"])
def test_readonly_scan_is_not_displayed_as_completed_flash(
    app, tmp_path, monkeypatch, action
):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    c = Controller(demo=True)
    c.action = action
    c.proc = SimpleNamespace(deleteLater=lambda: None)
    c.task_dir = c.store.begin(action, {})
    c.result = {"status": "success", "message": "No device connected."}
    monkeypatch.setattr(c, "read_wire", lambda: None)
    monkeypatch.setattr(c, "read_logs", lambda: None)
    c.update(busy=True, devices=[], deviceIndex=-1, plan={})
    c.finished(0, None)
    assert (
        c.data["state"] == "idle"
        and c.data["deviceNotice"] == "missing"
        and not c.data["canStart"]
    )
    c.timer.stop()
