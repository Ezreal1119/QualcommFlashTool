from types import SimpleNamespace

import pytest
import usb.core

from qft.backend import EDLBackend
from qft.transport import CheckedUSB


class Input:
    wMaxPacketSize = 512

    def __init__(self, packets):
        self.packets = list(packets)
        self.requests = []

    def read(self, length, timeout):
        self.requests.append((length, timeout))
        value = self.packets.pop(0)
        if isinstance(value, Exception):
            raise value
        assert len(value) <= length
        return value


def connection(packets):
    incoming = Input(packets)
    events = []
    cdc = SimpleNamespace(EP_IN=incoming, maxsize=512, timeout=1000)
    return CheckedUSB(cdc, lambda: events.append(True)), incoming, events


def test_small_sahara_read_retains_rest_of_firehose_packet():
    packet = b'<?xml version="1.0"?><data><log value="Firehose started"/></data>'
    transport, incoming, activity = connection([packet])
    assert transport.read(48) == packet[:48]
    assert transport.read(512) == packet[48:]
    assert len(incoming.requests) == 1 and incoming.requests[0][0] == 512
    assert len(activity) == 1


def test_idle_read_has_a_bounded_timeout_without_retry():
    transport, incoming, _ = connection([usb.core.USBTimeoutError("timed out")])
    assert transport.read(timeout=150) == b""
    assert incoming.requests == [(512, 150)]


def test_default_and_short_timeouts_remain_bounded():
    transport, incoming, _ = connection([b"hello", b"hello"])
    assert transport.read(timeout=None) == b"hello"
    assert transport.read(timeout=1) == b"hello"
    assert incoming.requests == [(512, 1000), (512, 50)]


def test_disconnect_is_an_error_instead_of_an_idle_read():
    transport, _, _ = connection([usb.core.USBError("No device", errno=19)])
    with pytest.raises(IOError, match="USB read failed"):
        transport.read()


@pytest.mark.parametrize("failure", [0, usb.core.USBTimeoutError("timed out")])
def test_failed_or_short_write_is_never_retried(failure):
    calls = []

    def write(data, timeout):
        calls.append(bytes(data))
        if isinstance(failure, Exception):
            raise failure
        return failure

    transport = CheckedUSB(
        SimpleNamespace(EP_OUT=SimpleNamespace(write=write)), lambda: None
    )
    with pytest.raises(IOError, match="no retry"):
        transport.write(b"firmware")
    assert calls == [b"firmware"]


def test_loader_greeting_reads_complete_function_list_past_four_packets():
    packets = [f'<data><log value="function{i}"/></data>'.encode() for i in range(19)]
    packets += [
        b'<data><log value="getsha256digest"/></data>',
        b"End of supported functions 15",
    ]
    transport, incoming, _ = connection(
        [*packets, usb.core.USBTimeoutError("timed out")]
    )
    backend = EDLBackend(None, None, lambda *args: None)
    backend.cdc = SimpleNamespace(read=transport.read)
    assert backend._drain_greeting() == b"".join(packets)
    assert len(incoming.requests) == len(packets) + 1


def test_unbounded_startup_stream_cannot_reach_configure(monkeypatch):
    backend = EDLBackend(None, None, lambda *args: None)
    backend.cdc = SimpleNamespace(read=lambda **kwargs: b"log")
    times = iter([0, 1, 2, 3, 6])
    monkeypatch.setattr("qft.backend.time.monotonic", lambda: next(times))
    with pytest.raises(TimeoutError, match="startup messages did not settle"):
        backend._drain_greeting()


def test_connection_errors_name_the_failed_command():
    backend = EDLBackend(None, None, lambda *args: None)

    def send(data):
        raise usb.core.USBTimeoutError("timed out")

    backend.fh = SimpleNamespace(xmlsend=send)
    with pytest.raises(IOError, match="Firehose configure failed"):
        backend._connect_command("configure", {})


def test_startup_pause_does_not_truncate_a_declared_function_list():
    packets = [
        b"Supported Functions (15):",
        usb.core.USBTimeoutError("timed out"),
        b"getsha256digest",
        b"End of supported functions 15",
        usb.core.USBTimeoutError("timed out"),
    ]
    transport, _, _ = connection(packets)
    backend = EDLBackend(None, None, lambda *args: None)
    backend.cdc = SimpleNamespace(read=transport.read)
    assert b"getsha256digest" in backend._drain_greeting()


def reset_backend(monkeypatch, reads, reports):
    from qft.models import DeviceInfo

    events, commands = [], []
    backend = EDLBackend(
        DeviceInfo("usb:0:5", "edl", "Test", "ready", bus=0, address=5, usb_path="0-1"),
        None,
        lambda kind, data: events.append((kind, data)),
    )
    pending_reads, pending_reports = iter(reads), iter(reports)

    def read(**kwargs):
        value = next(pending_reads)
        if isinstance(value, Exception):
            raise value
        return value

    def send(data):
        commands.append(data)
        return SimpleNamespace(resp={"value": "ACK"}, data={}, log=[], error="")

    backend.cdc = SimpleNamespace(read=read)
    backend.fh = SimpleNamespace(xmlsend=send)
    monkeypatch.setattr("qft.backend.scan", lambda: next(pending_reports))
    monkeypatch.setattr("qft.backend.time.sleep", lambda _: None)
    return backend, commands, events


def test_reset_drains_post_ack_logs_and_observes_usb_disappearance(monkeypatch):
    connected = {"devices": [{"mode": "edl", "usb_path": "0-1"}], "errors": []}
    gone = {"devices": [], "errors": []}
    backend, commands, events = reset_backend(
        monkeypatch,
        [b"Will issue reset", b"bsp_target_reset() 0", IOError("USB disconnected")],
        [connected, connected, gone],
    )
    backend.reset()
    assert len(commands) == 1
    assert any("bsp_target_reset" in data.get("text", "") for _, data in events)
    assert events[-1] == ("connection", {"step": "reset_disconnected"})


def test_reset_ack_alone_cannot_report_reboot_success(monkeypatch):
    connected = {"devices": [{"mode": "edl", "usb_path": "0-1"}], "errors": []}
    backend, commands, _ = reset_backend(
        monkeypatch, [b"", b""], [connected, connected]
    )
    times = iter([0, 0, 8, 16])
    monkeypatch.setattr("qft.backend.time.monotonic", lambda: next(times))
    with pytest.raises(IOError, match="EDL did not disconnect"):
        backend.reset()
    assert len(commands) == 1


def test_usb_scan_failure_does_not_look_like_successful_reset(monkeypatch):
    unavailable = {"devices": [], "errors": ["USB: unavailable"]}
    backend, commands, _ = reset_backend(monkeypatch, [b""], [unavailable])
    times = iter([0, 0, 16])
    monkeypatch.setattr("qft.backend.time.monotonic", lambda: next(times))
    with pytest.raises(IOError, match="EDL did not disconnect"):
        backend.reset()
    assert len(commands) == 1
