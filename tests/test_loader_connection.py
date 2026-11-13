"""Run the real adapter connection gate with a simulated Sahara/USB endpoint."""

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from loader_factory import build_loader
from qft.backend import EDLBackend
from qft.expressions import ValidationError
from qft.loaders import inspect_loader
from qft.models import DeviceInfo
from qft.runtime import setup_vendor


@pytest.fixture
def connection(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "appdata"))
    setup_vendor()
    import edlclient.Library.sahara as sah_module
    import edlclient.Library.Connection.usblib as usb_module
    import edlclient.Library.firehose as fh_module
    from edlclient.Library.sahara_defs import cmd_t

    info = inspect_loader(plan.loader)
    state = SimpleNamespace(
        mode="sahara",
        hwid=info.hw_id << 32,
        pkhash=info.root_sha384,
        serial=123,
        uploads=[],
        commands=[],
        connects=0,
        after_identify=lambda: None,
        during_upload=lambda: None,
        token={"session": 1234},
    )
    device = DeviceInfo("usb:1:5", "edl", "test", "ready", bus=1, address=5)
    events = []
    backend = EDLBackend(device, plan, lambda k, d: events.append((k, d)))
    monkeypatch.setattr("qft.backend.scan", lambda: {"devices": [asdict(device)]})
    monkeypatch.setattr("qft.backend.loader_session.usb_session", lambda _: state.token)
    monkeypatch.setattr(
        usb_module, "usb_class", lambda **kw: SimpleNamespace(connect=lambda: True)
    )

    class Sahara:
        bit64 = True
        serial = 0

        def __init__(self, *args):
            self.programmer = ""

        def connect(self):
            state.connects += 1
            return {
                "mode": state.mode,
                "cmd": cmd_t.SAHARA_HELLO_REQ,
                "data": SimpleNamespace(version=2, mode=0),
            }

        def cmd_info(self, version):
            assert self.programmer  # Upstream auto-selection must be disabled.
            self.hwid, self.pkhash, self.serial = state.hwid, state.pkhash, state.serial
            state.after_identify()
            return True

        def upload_loader(self, version):
            path = Path(self.programmer)
            assert path != Path(plan.loader)
            state.during_upload()
            state.uploads.append((path, path.read_bytes()))
            return "firehose"

    monkeypatch.setattr(sah_module, "sahara", Sahara)

    def factory(*args):
        return SimpleNamespace(cfg=args[2])

    factory.cfg = SimpleNamespace
    monkeypatch.setattr(fh_module, "firehose", factory)
    monkeypatch.setattr(backend, "_drain_greeting", lambda: b"")

    def command(name, attrs):
        state.commands.append(name)
        return SimpleNamespace(
            resp={
                "value": "ACK",
                "mem_type": "emmc",
                "block_size": 512,
                "total_blocks": 128,
            },
            data={},
            log=[],
            error="",
        )

    monkeypatch.setattr(backend, "_connect_command", command)
    return backend, state, events, info


def test_adapter_uploads_only_verified_snapshot(connection, plan):
    backend, state, events, info = connection
    original = Path(plan.loader).read_bytes()
    # A source edit after snapshot creation cannot alter the USB payload.
    state.during_upload = lambda: Path(plan.loader).write_bytes(b"edited")
    assert backend.connect() == {0: 128}
    assert state.uploads[0][1] == original
    assert not state.uploads[0][0].exists()  # Temporary snapshot was reclaimed.
    assert state.commands == ["nop", "configure", "getstorageinfo"]
    assert (
        next(d for k, d in events if k == "loader_selection")["selected"] == plan.loader
    )


@pytest.mark.parametrize("change", ["root", "hwid", "unknown", "serial"])
def test_identity_failure_never_uploads_or_configures(connection, change):
    backend, state, events, info = connection
    if change == "root":
        state.pkhash = "f" * 96
    elif change == "hwid":
        state.hwid ^= 1 << 32
    elif change == "unknown":
        state.hwid = None
    else:
        state.serial = None
    with pytest.raises(ValidationError, match="No loader was uploaded"):
        backend.connect()
    assert not state.uploads and not state.commands
    assert next(d for k, d in events if k == "loader_selection")["selected"] == ""


def test_changed_valid_file_is_rejected_before_upload(connection, plan):
    backend, state, events, info = connection
    state.after_identify = lambda: Path(plan.loader).write_bytes(
        build_loader(text=b"changed")
    )
    with pytest.raises(ValidationError, match="changed"):
        backend.connect()
    assert not state.uploads and not state.commands


def test_unknown_ram_programmer_is_not_silently_reused(connection):
    backend, state, events, info = connection
    state.mode = "firehose"
    with pytest.raises(ValidationError, match="existing RAM programmer"):
        backend.connect()
    assert not state.uploads and not state.commands


def test_inspect_then_flash_reuses_only_own_same_usb_session(connection):
    backend, state, events, info = connection
    assert backend.connect() == {0: 128}
    assert len(state.uploads) == 1
    state.mode = "firehose"
    state.commands.clear()
    assert backend.connect() == {0: 128}
    assert len(state.uploads) == 1
    assert state.commands == ["nop", "configure", "getstorageinfo"]
    state.token = {"session": 1235}
    state.commands.clear()
    with pytest.raises(ValidationError, match="existing RAM programmer"):
        backend.connect()
    assert not state.commands and len(state.uploads) == 1


def test_no_os_session_evidence_allows_fresh_upload_but_not_reuse(connection):
    backend, state, events, info = connection
    state.token = None
    assert backend.connect() == {0: 128}
    state.mode = "firehose"
    state.commands.clear()
    with pytest.raises(ValidationError, match="existing RAM programmer"):
        backend.connect()
    assert len(state.uploads) == 1 and not state.commands
