"""Streaming raw / Android sparse images, with strict structural validation."""

import struct
import zlib
from pathlib import Path
from .expressions import ValidationError

MAGIC = 0xED26FF3A
CHUNK = 1024 * 1024


def image_info(path):
    with open(path, "rb") as f:
        header = f.read(28)
    if len(header) < 4 or struct.unpack("<I", header[:4])[0] != MAGIC:
        return Path(path).stat().st_size, False
    if len(header) != 28:
        raise ValidationError("Truncated sparse header")
    magic, major, minor, fh, ch, block, blocks, chunks, checksum = struct.unpack(
        "<I4H4I", header
    )
    if (
        major != 1
        or fh < 28
        or ch < 12
        or block == 0
        or block % 4
        or block > 1024 * 1024
        or blocks == 0
        or chunks > 10_000_000
    ):
        raise ValidationError("Unsupported sparse image header")
    # Walk metadata without expanding potentially very large DONT_CARE chunks.
    count = 0
    with open(path, "rb") as f:
        f.seek(fh)
        for _ in range(chunks):
            h = f.read(ch)
            if len(h) != ch:
                raise ValidationError("Truncated sparse chunk header")
            kind, _, n, total = struct.unpack("<2H2I", h[:12])
            size = n * block
            required = {0xCAC1: size, 0xCAC2: 4, 0xCAC3: 0, 0xCAC4: 4}.get(kind)
            if (
                required is None
                or total - ch != required
                or (kind == 0xCAC4 and n != 0)
            ):
                raise ValidationError("Invalid sparse chunk size or type")
            if f.tell() + required > Path(path).stat().st_size:
                raise ValidationError("Truncated sparse chunk data")
            f.seek(required, 1)
            count += n
        if count != blocks or f.tell() != Path(path).stat().st_size:
            raise ValidationError("Sparse image block count or trailing data mismatch")
    return blocks * block, True


def image_chunks(path):
    size, sparse = image_info(path)
    with open(path, "rb") as f:
        if not sparse:
            while data := f.read(CHUNK):
                yield data
            return
        header = f.read(28)
        _, _, _, fh, ch, block, blocks, chunks, checksum = struct.unpack(
            "<I4H4I", header
        )
        f.seek(fh)
        rolling = 0
        for _ in range(chunks):
            kind, _, n, total = struct.unpack("<2H2I", f.read(ch)[:12])
            remaining = n * block
            fill = f.read(4) if kind in (0xCAC2, 0xCAC4) else b""
            if kind == 0xCAC4:
                if struct.unpack("<I", fill)[0] != rolling & 0xFFFFFFFF:
                    raise ValidationError("Sparse CRC chunk mismatch")
                continue
            while remaining:
                length = min(CHUNK, remaining)
                data = (
                    f.read(length)
                    if kind == 0xCAC1
                    else ((fill * (length // 4)) if kind == 0xCAC2 else bytes(length))
                )
                if len(data) != length:
                    raise ValidationError("Image changed or was truncated")
                rolling = zlib.crc32(data, rolling)
                remaining -= length
                yield data
        if checksum and checksum != rolling & 0xFFFFFFFF:
            raise ValidationError("Sparse image checksum mismatch")


def image_range(path, offset, length):
    """Read expanded image range; only final sector padding is implicit zero."""
    size, sparse = image_info(path)
    if offset < 0 or offset >= size or length <= 0:
        raise ValidationError("Image offset/length is out of range")
    if not sparse:
        with open(path, "rb") as f:
            f.seek(offset)
            remaining = min(length, size - offset)
            while remaining:
                data = f.read(min(CHUNK, remaining))
                if not data:
                    raise ValidationError("Image changed while reading")
                remaining -= len(data)
                yield data
    else:
        position = 0
        stop = offset + min(length, size - offset)
        for data in image_chunks(path):
            end = position + len(data)
            if end > offset and position < stop:
                yield data[max(0, offset - position) : min(len(data), stop - position)]
            position = end
            # Consume all chunks to validate sparse checksums as well.
    pad = max(0, length - (size - offset))
    if pad:
        if pad >= 4096:
            raise ValidationError("Image range exceeds final sector padding")
        yield bytes(pad)
