from dataclasses import asdict, replace
from pathlib import Path
import json
import plistlib
import struct
from types import SimpleNamespace

import pytest

from loader_factory import build_loader
from qft.expressions import ValidationError
from qft.firmware import automatic_configuration
from qft.loaders import (
    DeviceIdentity,
    NotProgrammer,
    inspect_loader,
    match_loader,
    select_loader,
)
from qft.models import FlashPlan, DeviceInfo
from qft.package import make_plan, recheck
from qft import loader_session


def write_loader(tmp_path, name="renamed.elf", **kwargs):
    path = tmp_path / name
    path.write_bytes(build_loader(**kwargs))
    return path


def identity(info, **kwargs):
    return replace(
        DeviceIdentity(
            (info.hw_id << 32) | (info.oem_id << 16) | info.model_id,
            info.root_sha384,
            123,
        ),
        **kwargs,
    )


@pytest.mark.parametrize("bits,width", [(32, 32), (32, 48), (64, 32), (64, 48)])
def test_signed_elf_classes_and_hash_tables(tmp_path, bits, width):
    info = inspect_loader(write_loader(tmp_path, bits=bits, hash_width=width))
    assert info.bits == bits and not info.restrictions
    assert match_loader(info, identity(info))[0] == "match"


def test_renaming_never_changes_metadata_or_match(tmp_path):
    path = write_loader(tmp_path, "prog_firehose_WRONG_CPU_ddr.elf")
    a = inspect_loader(path)
    path = path.rename(tmp_path / "中文 名字.mbn")
    b = inspect_loader(path)
    assert replace(a, path=b.path) == b
    assert match_loader(a, identity(b))[0] == "match"


@pytest.mark.parametrize(
    "offset,reason",
    [
        (8192, "segment"),
        (4264, "signature"),
        (4530, "signature"),
        (4790, "signature"),
        (64 + 16, "bounds|segment|header"),
    ],
)
def test_tampering_never_authenticates(tmp_path, offset, reason):
    path = write_loader(tmp_path)
    data = bytearray(path.read_bytes())
    data[offset] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValidationError, match=reason):
        inspect_loader(path)


@pytest.mark.parametrize(
    "data", [b"loader", bytes(64), b"\x7fELF" + bytes(60), build_loader()[:4500]]
)
def test_invalid_truncated_and_legacy_images_rejected(tmp_path, data):
    path = tmp_path / "prog_firehose.elf"
    path.write_bytes(data)
    with pytest.raises(ValidationError):
        inspect_loader(path)


@pytest.mark.parametrize(
    "offset,value", [(4100, 7), (4104, 256), (4140, 124), (64 + 56 + 4, 0x02300000)]
)
def test_unsupported_signing_layouts_and_paging(tmp_path, offset, value):
    path = write_loader(tmp_path)
    data = bytearray(path.read_bytes())
    struct.pack_into("<I", data, offset, value)
    path.write_bytes(data)
    with pytest.raises(ValidationError, match="Unsupported"):
        inspect_loader(path)


def test_normal_firmware_image_is_not_a_programmer(tmp_path):
    with pytest.raises(NotProgrammer, match="not a device programmer"):
        inspect_loader(write_loader(tmp_path, sw=5))


def test_discovery_uses_contents_and_preserves_reports(package):
    old = package / "prog_firehose.mbn"
    old.rename(package / "unhelpful-name.elf")
    write_loader(package, "prog_firehose_xbl.elf", sw=5)
    config = automatic_configuration(package)
    assert not config["issues"]
    assert len(config["loaderCandidates"]) == 1
    assert config["loader"].endswith("unhelpful-name.elf")
    assert any(r["status"] == "excluded" for r in config["loaderReports"])


@pytest.mark.parametrize(
    "changes",
    [
        {"hwid": 0x9999999900000000},
        {"hwid": 0x1234567800010000},
        {"hwid": 0x1234567800000001},
        {"pkhash": "0" * 95 + "1"},
        {"serial": 999},
    ],
)
def test_signed_identity_conflicts(tmp_path, changes):
    info = inspect_loader(write_loader(tmp_path, serial=123))
    assert match_loader(info, identity(info, **changes))[0] == "conflict"


@pytest.mark.parametrize(
    "changes", [{"hwid": None}, {"serial": None}, {"pkhash": ""}, {"pkhash": "0" * 96}]
)
def test_missing_device_identity_is_unknown(tmp_path, changes):
    info = inspect_loader(write_loader(tmp_path))
    assert match_loader(info, identity(info, **changes))[0] == "unknown"


def test_full_hash_required_not_prefix(tmp_path):
    info = inspect_loader(write_loader(tmp_path))
    original = info.root_sha384
    assert match_loader(info, identity(info, pkhash=original[:16]))[0] == "unknown"
    other = original[:-1] + ("0" if original[-1] != "0" else "1")
    assert match_loader(info, identity(info, pkhash=other))[0] == "conflict"
    assert match_loader(info, identity(info, pkhash=info.root_sha256))[0] == "match"


def test_soc_version_never_guessed_from_hwid(tmp_path):
    info = inspect_loader(write_loader(tmp_path, hw=0, flags=2, soc=0x9002))
    device = identity(info, hwid=0x0014D0E100000000)
    assert match_loader(info, device)[0] == "unknown"
    assert match_loader(info, replace(device, soc_vers=(0x7200,)))[0] == "conflict"
    assert match_loader(info, replace(device, soc_vers=(0x9002,)))[0] == "match"


def test_recorded_dt50_evidence_is_limited_to_exact_chip_and_file(tmp_path):
    from qft.loaders import _DT50_EVIDENCE

    sha256, hwid, root, serial = _DT50_EVIDENCE
    info = replace(
        inspect_loader(write_loader(tmp_path, hw=0, flags=2, soc=0x9002)),
        sha256=sha256,
        root_sha384=root,
    )
    device = DeviceIdentity(hwid, root, serial)
    assert match_loader(info, device)[0] == "match"
    assert match_loader(replace(info, sha256="f" * 64), device)[0] == "unknown"
    assert match_loader(info, replace(device, serial=serial + 1))[0] == "unknown"
    assert match_loader(info, replace(device, hwid=hwid + (1 << 32)))[0] == "unknown"
    assert (
        match_loader(replace(info, restrictions=("unknown flag",)), device)[0]
        == "unknown"
    )


def test_root_certificate_signature_tampering_is_rejected(tmp_path):
    path = write_loader(tmp_path)
    data = bytearray(path.read_bytes())
    image_size = struct.unpack_from("<I", data, 4096 + 16)[0]
    data[4096 + 48 + 120 + image_size - 1] ^= 1
    path.write_bytes(data)
    with pytest.raises(ValidationError, match="signature"):
        inspect_loader(path)


@pytest.mark.parametrize("flags", [1, 4, 8, 0x100, 0x80000000])
def test_unimplemented_restrictions_are_never_wildcards(tmp_path, flags):
    info = inspect_loader(write_loader(tmp_path, flags=flags))
    assert info.restrictions and match_loader(info, identity(info))[0] == "unknown"


def plan_for(package, paths):
    return make_plan(
        package,
        paths[0],
        "emmc",
        [package / "rawprogram0.xml"],
        [package / "patch0.xml"],
        loader_candidates=paths,
    )


def test_unique_hardware_match_among_multiple_candidates(package):
    first = package / "prog_firehose.mbn"
    second = write_loader(package, "different.elf", hw=0x98765432)
    config = automatic_configuration(package)
    assert not config["issues"] and len(config["loaderCandidates"]) == 2
    plan = plan_for(package, [str(first), str(second)])
    info = inspect_loader(second)
    events = []
    assert select_loader(
        plan, identity(info), lambda k, d: events.append((k, d))
    ).path == str(second)
    assert next(d for k, d in events if k == "loader_selection")["selected"] == str(
        second
    )
    assert FlashPlan.from_dict(json.loads(json.dumps(asdict(plan)))) == plan
    # Every candidate is frozen, including the one not selected in this example.
    first.write_bytes(build_loader(text=b"changed"))
    with pytest.raises(ValidationError, match="changed"):
        recheck(plan)


def test_ambiguous_compatible_programmers_do_not_upload(package):
    paths = [
        str(package / "prog_firehose.mbn"),
        str(write_loader(package, text=b"other")),
    ]
    plan = plan_for(package, paths)
    with pytest.raises(ValidationError, match="Cannot confirm one compatible loader"):
        select_loader(plan, identity(inspect_loader(paths[0])))


def test_identical_copies_are_one_programmer(package):
    first = str(package / "prog_firehose.mbn")
    second = str(write_loader(package))
    result = select_loader(
        plan_for(package, [first, second]), identity(inspect_loader(first))
    )
    assert result.sha256 == inspect_loader(second).sha256


def test_unknown_candidate_blocks_apparent_unique_match(package):
    first = str(package / "prog_firehose.mbn")
    second = str(write_loader(package, flags=2, soc=0x9002))
    with pytest.raises(ValidationError, match="SoC version"):
        select_loader(
            plan_for(package, [first, second]), identity(inspect_loader(first))
        )


def test_changed_loader_rejected_even_if_new_signature_valid(plan):
    Path(plan.loader).write_bytes(build_loader(text=b"new valid programmer"))
    with pytest.raises(ValidationError, match="changed"):
        select_loader(plan, identity(inspect_loader(plan.loader)))


def test_manual_invalid_loader_rejected_offline(package):
    path = package / "prog_firehose.mbn"
    path.write_bytes(b"fake")
    with pytest.raises(ValidationError, match="Unsupported loader"):
        plan_for(package, [str(path)])


def test_verified_ram_session_is_bound_and_short_lived(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    info = inspect_loader(plan.loader)
    token = {"session": 123, "location": 0x01340000, "address": 5}
    now = 1000.0
    monkeypatch.setattr(loader_session.time, "time", lambda: now)
    loader_session.remember(token, identity(info), info)
    assert loader_session.resume(token, plan, lambda *a: None)[1] == info
    for invalid in (None, {**token, "session": 124}, {**token, "address": 6}):
        with pytest.raises(ValidationError, match="existing RAM programmer"):
            loader_session.resume(invalid, plan, lambda *a: None)
    now += loader_session.TTL + 1
    with pytest.raises(ValidationError, match="existing RAM programmer"):
        loader_session.resume(token, plan, lambda *a: None)


def test_unknown_ram_programmer_cannot_use_same_bus_and_address(
    plan, tmp_path, monkeypatch
):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    with pytest.raises(ValidationError, match="Re-enter normal EDL"):
        loader_session.resume({"address": 5, "session": 99}, plan, lambda *a: None)


def test_ram_receipt_cannot_authorize_another_programmer(plan, tmp_path, monkeypatch):
    monkeypatch.setenv("QFT_DATA_DIR", str(tmp_path / "data"))
    info = inspect_loader(plan.loader)
    loader_session.remember(
        {"session": 1}, identity(info), replace(info, sha256="f" * 64)
    )
    with pytest.raises(ValidationError, match="existing RAM programmer"):
        loader_session.resume({"session": 1}, plan, lambda *a: None)


def test_os_session_requires_unique_vid_pid_address_and_bus(monkeypatch):
    monkeypatch.setattr(loader_session.sys, "platform", "darwin")
    node = {
        "idVendor": 0x05C6,
        "idProduct": 0x9008,
        "USB Address": 5,
        "locationID": 0x01340000,
        "sessionID": 9876,
    }
    nodes = [{"IORegistryEntryChildren": [node]}]
    monkeypatch.setattr(
        loader_session.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=plistlib.dumps(nodes)),
    )
    device = DeviceInfo("usb:1:5", "edl", "test", "ready", bus=1, address=5)
    assert loader_session.usb_session(device)["session"] == 9876
    assert loader_session.usb_session(replace(device, address=6)) is None
    nodes[0]["IORegistryEntryChildren"].append(node.copy())
    assert loader_session.usb_session(device) is None
