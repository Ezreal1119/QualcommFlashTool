"""Regression layouts matching the user's 4096-byte-sector Qualcomm package."""

from dataclasses import asdict, replace
import hashlib
import struct
import uuid
import zlib

import pytest
from xml.etree import ElementTree as ET
from qft.engine import execute
from qft.expressions import ValidationError
from qft.models import FlashPlan
from qft.package import make_plan, validate_ranges, layout_notes
from qft.images import image_range
from qft.patch_layout import linear

SS = 4096


def raw_entry(filename, label, count, start, offset=0, single=False):
    return ET.Element(
        "program",
        {
            "SECTOR_SIZE_IN_BYTES": str(SS),
            "filename": filename,
            "label": label,
            "file_sector_offset": str(offset),
            "num_partition_sectors": str(count),
            "physical_partition_number": "0",
            "start_sector": str(start),
            "partofsingleimage": str(single).lower(),
            "readbackverify": "false",
            "sparse": "false",
        },
    )


def build_plan(folder):
    return make_plan(
        folder,
        folder / "prog_firehose_qcm4290_ddr.elf",
        "ufs",
        [folder / "rawprogram0.xml"],
        [folder / "patch0.xml"],
    )


@pytest.fixture
def layout(tmp_path):
    folder = tmp_path / "Firmware"
    folder.mkdir()
    (folder / "prog_firehose_qcm4290_ddr.elf").write_bytes(b"test loader")
    (folder / "vbmeta_system.img").write_bytes(b"V" * SS)
    (folder / "zeros.bin").write_bytes(bytes(16896))
    root = ET.Element("data")
    root.append(raw_entry("vbmeta_system.img", "vbmeta_system_a", 16, 8))
    root.append(raw_entry("zeros.bin", "userdata", 0, 24))
    root.append(raw_entry("gpt_main0.bin", "PrimaryGPT", 6, 0, single=True))
    root.append(
        raw_entry("gpt_backup0.bin", "BackupGPT", 5, "NUM_DISK_SECTORS-5.", single=True)
    )
    ET.ElementTree(root).write(folder / "rawprogram0.xml")
    # 32 entries (one sector) in a four-sector array allocation, matching the
    # standard 6-sector primary / 5-sector backup GPT file arrangement.
    old_capacity = 128
    guid = uuid.uuid4().bytes_le
    table = bytearray(4 * SS)
    for index, (first, last) in enumerate(((8, 23), (24, old_capacity - 6))):
        off = index * 128
        table[off : off + 16] = uuid.uuid4().bytes_le
        struct.pack_into("<QQ", table, off + 32, first, last)

    def header(here, other, array):
        h = bytearray(SS)
        h[:8] = b"EFI PART"
        struct.pack_into(
            "<IIII4Q", h, 8, 0x10000, 92, 0, 0, here, other, 6, old_capacity - 6
        )
        h[56:72] = guid
        struct.pack_into(
            "<QIII", h, 72, array, 32, 128, zlib.crc32(table[:SS]) & 0xFFFFFFFF
        )
        struct.pack_into("<I", h, 16, zlib.crc32(h[:92]) & 0xFFFFFFFF)
        return h

    (folder / "gpt_main0.bin").write_bytes(
        bytes(SS) + header(1, old_capacity - 1, 2) + table
    )
    (folder / "gpt_backup0.bin").write_bytes(
        table + header(old_capacity - 1, 1, old_capacity - 5)
    )
    patches = ET.Element("patches")

    def add(target, start, byte, value, size=8):
        ET.SubElement(
            patches,
            "patch",
            {
                "SECTOR_SIZE_IN_BYTES": str(SS),
                "filename": target,
                "physical_partition_number": "0",
                "start_sector": str(start),
                "byte_offset": str(byte),
                "size_in_bytes": str(size),
                "value": str(value),
                "what": "GPT fixture patch",
            },
        )

    def pair(target, host_start, disk_start, byte, host_value, disk_value=None, size=8):
        add(target, host_start, byte, host_value, size)
        add(
            "DISK",
            disk_start,
            byte,
            disk_value if disk_value is not None else host_value,
            size,
        )

    pair("gpt_main0.bin", 2, 2, 168, "NUM_DISK_SECTORS-6.")
    pair("gpt_backup0.bin", 0, "NUM_DISK_SECTORS-5.", 168, "NUM_DISK_SECTORS-6.")
    pair("gpt_main0.bin", 1, 1, 48, "NUM_DISK_SECTORS-6.")
    pair("gpt_backup0.bin", 4, "NUM_DISK_SECTORS-1.", 48, "NUM_DISK_SECTORS-6.")
    pair("gpt_main0.bin", 1, 1, 32, "NUM_DISK_SECTORS-1.")
    pair("gpt_backup0.bin", 4, "NUM_DISK_SECTORS-1.", 24, "NUM_DISK_SECTORS-1.")
    pair("gpt_backup0.bin", 4, "NUM_DISK_SECTORS-1.", 72, "NUM_DISK_SECTORS-5.")
    pair("gpt_main0.bin", 1, 1, 88, "CRC32(2,4096)", size=4)
    pair(
        "gpt_backup0.bin",
        4,
        "NUM_DISK_SECTORS-1.",
        88,
        "CRC32(0,4096)",
        "CRC32(NUM_DISK_SECTORS-5.,4096)",
        4,
    )
    for target, hstart, dstart in [
        ("gpt_main0.bin", 1, "1"),
        ("gpt_backup0.bin", 4, "NUM_DISK_SECTORS-1."),
    ]:
        pair(target, hstart, dstart, 16, "0", size=4)
        pair(
            target, hstart, dstart, 16, f"CRC32({hstart},92)", f"CRC32({dstart},92)", 4
        )
    ET.ElementTree(patches).write(folder / "patch0.xml")
    return folder


class MemoryBackend:
    def __init__(self, capacity=256):
        self.capacity = capacity
        self.disk = bytearray(b"\xa5" * (capacity * SS))
        self.calls = []
        self.reset_sent = False

    def connect(self):
        return {0: self.capacity}

    def program(self, op, start, chunks, progress):
        data = b"".join(chunks)
        assert len(data) == op.sectors * SS
        self.calls.append(("program", op.label, start, op.sectors))
        self.disk[start * SS : start * SS + len(data)] = data
        progress(len(data))

    def patch(self, op, start, value):
        assert op.target == "DISK"
        self.calls.append(("patch", start))
        begin = start * SS + op.byte_offset
        self.disk[begin : begin + op.size] = value.to_bytes(op.size, "little")

    def read(self, lun, start, count):
        assert lun == 0 and 0 <= start < start + count <= self.capacity
        return bytes(self.disk[start * SS : (start + count) * SS])

    def digest(self, lun, start, count):
        return hashlib.sha256(self.read(lun, start, count)).hexdigest()

    def reset(self):
        self.reset_sent = True

    def close(self):
        pass


def test_short_images_zero_count_and_file_patch_mirrors(layout):
    plan = build_plan(layout)
    assert plan.programs[0].sectors == 1 and plan.programs[0].declared_sectors == 16
    assert plan.programs[1].sectors == 5 and plan.programs[1].declared_sectors == 0
    assert len(plan.patches) == len(plan.file_patches) == 13
    assert all(p.target == "DISK" for p in plan.patches)
    assert len(layout_notes(plan)) == 3
    assert FlashPlan.from_dict(asdict(plan)) == plan


@pytest.mark.parametrize("capacity", [256, 512])
def test_complete_gpt_sequence_and_critical_digest(layout, capacity):
    plan = build_plan(layout)
    originals = {p: p.read_bytes() for p in layout.iterdir()}
    backend = MemoryBackend(capacity)
    result = execute(plan, backend, lambda *a: None, lambda: False)
    assert result.status == "success", result.message
    assert backend.reset_sent and any(
        "vbmeta_system_a" in item for item in result.verified
    )
    # Never fill the rest of the 16-sector XML allocation or all of userdata.
    assert backend.disk[9 * SS : 24 * SS] == b"\xa5" * (15 * SS)
    assert backend.disk[29 * SS : 30 * SS] == b"\xa5" * SS
    assert backend.disk[24 * SS : 29 * SS] == bytes(5 * SS)
    assert all(path.read_bytes() == data for path, data in originals.items())


def test_xml_allocation_still_must_fit_device(layout):
    plan = build_plan(layout)
    bad = replace(plan.programs[0], start="245")
    errors, _ = validate_ranges(replace(plan, programs=(bad,)), {0: 256})
    assert errors


def test_nonzero_file_offset_and_final_sector_padding(layout):
    path = layout / "vbmeta_system.img"
    path.write_bytes(b"A" * SS + b"B" * 511)
    xml = ET.parse(layout / "rawprogram0.xml")
    e = xml.getroot()[0]
    e.set("file_sector_offset", "1")
    xml.write(layout / "rawprogram0.xml")
    op = build_plan(layout).programs[0]
    assert op.sectors == 1
    assert b"".join(
        image_range(op.path, op.file_offset, op.sectors * SS)
    ) == b"B" * 511 + bytes(SS - 511)


def test_explicit_xml_limit_caps_file_data(layout):
    path = layout / "vbmeta_system.img"
    path.write_bytes(b"A" * (2 * SS))
    xml = ET.parse(layout / "rawprogram0.xml")
    xml.getroot()[0].set("num_partition_sectors", "1")
    xml.write(layout / "rawprogram0.xml")
    assert build_plan(layout).programs[0].sectors == 1


@pytest.mark.parametrize(
    "change", ["missing", "crc_source", "address", "order", "outside"]
)
def test_unproven_file_patch_never_reaches_device(layout, change):
    path = layout / "patch0.xml"
    xml = ET.parse(path)
    root = xml.getroot()
    if change == "missing":
        root.remove(root[1])
    elif change == "crc_source":
        for e in root:
            if (
                e.get("filename") == "DISK"
                and e.get("value") == "CRC32(NUM_DISK_SECTORS-5.,4096)"
            ):
                e.set("value", "CRC32(0,4096)")
    elif change == "address":
        root[1].set("start_sector", "3")
    elif change == "order":
        item = root[1]
        root.remove(item)
        root.append(item)
    else:
        root[0].set("start_sector", "99")
    xml.write(path)
    with pytest.raises(ValidationError):
        build_plan(layout)


def test_empty_image_does_not_become_a_zero_fill(layout):
    (layout / "vbmeta_system.img").write_bytes(b"")
    with pytest.raises(ValidationError):
        build_plan(layout)


@pytest.mark.parametrize(
    "expr,want",
    [
        ("NUM_DISK_SECTORS-5.", (1, -5)),
        ("(4096*NUM_DISK_SECTORS-20480.)/4096", (1, -5)),
        ("2+3", (0, 5)),
    ],
)
def test_exact_symbolic_normalization(expr, want):
    assert linear(expr) == want


@pytest.mark.parametrize(
    "expr", ["NUM_DISK_SECTORS*NUM_DISK_SECTORS", "NUM_DISK_SECTORS/2", "CRC32(1,92)"]
)
def test_unsupported_symbolic_equivalence_is_rejected(expr):
    with pytest.raises(ValidationError):
        linear(expr)


def test_disk_only_arithmetic_does_not_require_affine_mirror_proof(layout):
    path = layout / "patch0.xml"
    xml = ET.parse(path)
    root = xml.getroot()
    for e in list(root):
        if e.get("filename") != "DISK":
            root.remove(e)
    root[0].set("value", "NUM_DISK_SECTORS*NUM_DISK_SECTORS")
    xml.write(path)
    plan = build_plan(layout)
    assert (
        not plan.file_patches
        and plan.patches[0].value == "NUM_DISK_SECTORS*NUM_DISK_SECTORS"
    )
