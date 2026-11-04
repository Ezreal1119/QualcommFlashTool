import hashlib
import struct
import zlib
from .expressions import ValidationError, evaluate
from .images import image_range


def verify_gpt(backend, lun, capacity, ss, required):
    primary = backend.read(lun, 1, 1)
    if primary[:8] != b"EFI PART":
        if required:
            raise ValidationError(f"LUN {lun}: primary GPT is missing")
        return False
    backup = backend.read(lun, capacity - 1, 1)

    def check(header, current, other):
        if header[:8] != b"EFI PART":
            raise ValidationError(f"LUN {lun}: backup GPT is missing")
        revision, size, crc = struct.unpack_from("<III", header, 8)
        if revision != 0x10000 or not 92 <= size <= ss:
            raise ValidationError("Invalid GPT header version/size")
        work = bytearray(header[:size])
        work[16:20] = bytes(4)
        if zlib.crc32(work) & 0xFFFFFFFF != crc:
            raise ValidationError("GPT header CRC mismatch")
        here, there, first, last = struct.unpack_from("<4Q", header, 24)
        if (here, there) != (current, other) or not 2 <= first <= last < capacity - 1:
            raise ValidationError("GPT LBA bounds mismatch")
        table, count, entry_size, entries_crc = struct.unpack_from("<QIII", header, 72)
        length = count * entry_size
        if (
            count == 0
            or entry_size < 128
            or entry_size % 128
            or length > 32 * 1024 * 1024
            or table < 2
            or table + (length + ss - 1) // ss > capacity - 1
        ):
            raise ValidationError("Invalid GPT entry array bounds")
        data = backend.read(lun, table, (length + ss - 1) // ss)[:length]
        if zlib.crc32(data) & 0xFFFFFFFF != entries_crc:
            raise ValidationError("GPT partition array CRC mismatch")
        for offset in range(0, length, entry_size):
            entry = data[offset : offset + entry_size]
            if entry[:16] != bytes(16):
                a, b = struct.unpack_from("<QQ", entry, 32)
                if not first <= a <= b <= last:
                    raise ValidationError("GPT partition lies outside usable bounds")
        return header[56:72], first, last, count, entry_size, data

    if check(primary, 1, capacity - 1) != check(backup, capacity - 1, 1):
        raise ValidationError("Primary and backup GPT disagree")
    return True


def expected_digest(plan, op, capacity, patch_values):
    """Reconstruct final bytes, including later overlapping writes and patches."""
    ss = op.sector_size
    begin = evaluate(op.start, capacity) * ss
    end = begin + op.sectors * ss
    digest = hashlib.sha256()
    for chunk_start in range(begin, end, 1024 * 1024):
        chunk_end = min(end, chunk_start + 1024 * 1024)
        out = bytearray(chunk_end - chunk_start)
        for other in plan.programs:
            if other.lun != op.lun:
                continue
            start = evaluate(other.start, capacity) * ss
            stop = start + other.sectors * ss
            a, b = max(start, chunk_start), min(stop, chunk_end)
            if a < b:
                offset = other.file_offset + a - start
                payload = b"".join(image_range(other.path, offset, b - a))
                out[a - chunk_start : b - chunk_start] = payload
        for patch, start, value in patch_values:
            if patch.lun != op.lun:
                continue
            a = start * ss + patch.byte_offset
            data = value.to_bytes(patch.size, "little")
            b = a + len(data)
            x, y = max(a, chunk_start), min(b, chunk_end)
            if x < y:
                out[x - chunk_start : y - chunk_start] = data[x - a : y - a]
        digest.update(out)
    return digest.hexdigest()
