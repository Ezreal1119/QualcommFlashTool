import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import struct
import uuid
import zlib
import pytest
from qft.package import make_plan


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / "固件 with spaces"
    folder.mkdir()
    (folder / "prog_firehose.mbn").write_bytes(b"loader")
    (folder / "boot.img").write_bytes(b"B" * 512)
    (folder / "rawprogram0.xml").write_text(
        '<data><program SECTOR_SIZE_IN_BYTES="512" file_sector_offset="0" filename="boot.img" label="boot" num_partition_sectors="1" physical_partition_number="0" start_sector="10" /></data>'
    )
    (folder / "patch0.xml").write_text("<data/>")
    return folder


@pytest.fixture
def plan(package):
    return make_plan(
        package,
        package / "prog_firehose.mbn",
        "emmc",
        [package / "rawprogram0.xml"],
        [package / "patch0.xml"],
    )


def gpt_disk(sectors=128, ss=512):
    disk = bytearray(sectors * ss)
    entries = bytes(512)
    guid = uuid.uuid4().bytes_le

    def header(current, backup, table):
        h = bytearray(ss)
        h[:8] = b"EFI PART"
        struct.pack_into(
            "<IIII4Q", h, 8, 0x10000, 92, 0, 0, current, backup, 3, sectors - 3
        )
        h[56:72] = guid
        struct.pack_into(
            "<QIII", h, 72, table, 4, 128, zlib.crc32(entries) & 0xFFFFFFFF
        )
        struct.pack_into("<I", h, 16, zlib.crc32(h[:92]) & 0xFFFFFFFF)
        return h

    disk[ss : 2 * ss] = header(1, sectors - 1, 2)
    disk[2 * ss : 3 * ss] = entries
    disk[(sectors - 2) * ss : (sectors - 1) * ss] = entries
    disk[(sectors - 1) * ss :] = header(sectors - 1, 1, sectors - 2)
    return disk


class FakeBackend:
    def __init__(self, fail="", digest_supported=True):
        self.disk = gpt_disk()
        self.capacities = {0: 128}
        self.calls = []
        self.fail = fail
        self.digest_supported = digest_supported
        self.after_program = None

    def connect(self):
        self.calls.append("connect")
        if self.fail == "connect":
            raise IOError("Disconnected")
        return self.capacities

    def program(self, op, start, chunks, progress):
        self.calls.append("program")
        if self.fail == "program":
            raise IOError("GPT write failed")
        payload = b"".join(chunks)
        self.disk[start * 512 : start * 512 + len(payload)] = payload
        progress(len(payload))
        if self.after_program:
            self.after_program()

    def patch(self, op, start, value):
        self.calls.append("patch")
        if self.fail == "patch":
            raise IOError("Patch NAK")
        a = start * 512 + op.byte_offset
        self.disk[a : a + op.size] = value.to_bytes(op.size, "little")

    def read(self, lun, start, count):
        self.calls.append("read")
        if self.fail == "read":
            raise IOError("USB disconnected")
        return bytes(self.disk[start * 512 : (start + count) * 512])

    def digest(self, lun, start, count):
        import hashlib

        if not self.digest_supported:
            return None
        if self.fail == "digest":
            return "0" * 64
        return hashlib.sha256(
            self.disk[start * 512 : (start + count) * 512]
        ).hexdigest()

    def set_bootable(self, lun):
        self.calls.append("bootable")

    def reset(self):
        self.calls.append("reset")
        if self.fail == "reset":
            raise IOError("Reset NAK")

    def close(self):
        self.calls.append("close")
