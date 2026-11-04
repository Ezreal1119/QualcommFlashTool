from dataclasses import replace
import pytest
from conftest import FakeBackend
from qft.engine import execute
from qft.models import Patch


def run(plan, backend, **kwargs):
    return execute(plan, backend, lambda *args: None, lambda: False, **kwargs)


def test_success_only_after_ack_and_verification(plan):
    backend = FakeBackend()
    result = run(plan, backend)
    assert result.status == "success" and result.rebooted
    assert (
        backend.calls.index("program")
        < backend.calls.index("read")
        < backend.calls.index("reset")
    )


@pytest.mark.parametrize("failure", ["connect", "program", "read", "digest"])
def test_failure_never_resets(plan, failure):
    backend = FakeBackend(failure)
    result = run(plan, backend)
    assert result.status == "failed"
    assert "reset" not in backend.calls
    assert backend.calls[-1] == "close"


def test_gpt_error_keyword_is_not_success(plan):
    backend = FakeBackend("program")
    result = run(plan, backend)
    assert "GPT write failed" in result.message and result.status == "failed"


def test_inspection_never_writes_or_resets(plan):
    backend = FakeBackend()
    result = run(plan, backend, action="inspect")
    assert result.status == "ready" and backend.calls == ["connect", "close"]


def test_missing_digest_marks_incomplete_no_reset(plan):
    backend = FakeBackend(digest_supported=False)
    result = run(plan, backend)
    assert (
        result.status == "incomplete"
        and result.unverified
        and "reset" not in backend.calls
    )


def test_reset_failure_is_distinct(plan):
    result = run(plan, FakeBackend("reset"))
    assert result.status == "reboot_failed" and result.verified


def test_cancel_at_boundary(plan):
    backend = FakeBackend()
    stopped = [False]
    backend.after_program = lambda: stopped.__setitem__(0, True)
    result = execute(plan, backend, lambda *args: None, lambda: stopped[0])
    assert result.status == "interrupted" and "reset" not in backend.calls


def test_patch_failure_stops(plan):
    p = Patch(0, "10", 0, 4, "42", 512)
    backend = FakeBackend("patch")
    result = run(replace(plan, patches=(p,)), backend)
    assert result.status == "failed" and "reset" not in backend.calls


def test_patch_included_in_final_digest(plan):
    p = Patch(0, "10", 0, 4, "42", 512)
    backend = FakeBackend()
    result = run(replace(plan, patches=(p,)), backend)
    assert result.status == "success"


def test_capacity_change_rejected_before_write(plan):
    backend = FakeBackend()
    result = run(plan, backend, expected_capacities={"0": 256})
    assert result.status == "failed" and "program" not in backend.calls


def test_corrupt_gpt_rejected(plan):
    backend = FakeBackend()
    backend.disk[512 + 16] ^= 1
    result = run(plan, backend)
    assert (
        result.status == "failed"
        and "CRC" in result.message
        and "reset" not in backend.calls
    )


def test_no_autoreboot_when_disabled(plan):
    backend = FakeBackend()
    result = run(plan, backend, reboot=False)
    assert (
        result.status == "success"
        and not result.rebooted
        and "reset" not in backend.calls
    )


def test_changed_file_stops_before_device_connection(plan):
    from pathlib import Path

    Path(plan.loader).write_bytes(b"changed")
    backend = FakeBackend()
    result = run(plan, backend)
    assert result.status == "failed" and backend.calls == ["close"]


def test_unknown_action_never_connects(plan):
    backend = FakeBackend()
    result = run(plan, backend, action="typo")
    assert result.status == "failed" and backend.calls == ["close"]
