from __future__ import annotations
import hashlib
import re
from pathlib import Path
from defusedxml import ElementTree as ET
from .models import FileStamp, Program, Patch, FlashPlan
from .expressions import ValidationError, evaluate
from .images import image_info, image_chunks


def stamp(path):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(4 * 1024 * 1024):
            digest.update(data)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValidationError(f"File changed during check: {path}")
    return FileStamp(str(path), after.st_size, after.st_mtime_ns, digest.hexdigest())


def recheck(plan):
    for saved in plan.files:
        if stamp(saved.path) != saved:
            raise ValidationError(f"File changed; run checks again: {saved.path}")


def discover(folder):
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValidationError("Select an extracted firmware folder")
    # Files are found recursively; ambiguities are exposed, never guessed.
    files = sorted(
        (p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p).lower()
    )
    raws = [
        p
        for p in files
        if re.fullmatch(r"rawprogram(?:_unsparse)?\d*\.xml", p.name, re.I)
    ]
    patches = [p for p in files if re.fullmatch(r"patch\d*\.xml", p.name, re.I)]
    loaders = [
        p
        for p in files
        if p.suffix.lower() in (".elf", ".mbn")
        and ("firehose" in p.name.lower() or "prog" in p.name.lower())
    ]
    if not loaders:
        loaders = [p for p in files if p.suffix.lower() in (".elf", ".mbn")]

    def key(p):
        n = re.search(r"(\d+)\.xml$", p.name, re.I)
        return (int(n[1]) if n else -1, str(p))

    return {
        "raws": [str(p) for p in sorted(raws, key=key)],
        "patches": [str(p) for p in sorted(patches, key=key)],
        "loaders": [str(p) for p in loaders],
    }


def _integer(element, name, default=None):
    value = element.get(name, default)
    if value is None:
        raise ValidationError(f"Missing XML attribute: {name}")
    if "NUM_DISK_SECTORS" in str(value) or "CRC32" in str(value):
        raise ValidationError(
            f"Dynamic {name} is unsupported; use a prepared package with explicit lengths"
        )
    n = evaluate(str(value))
    if n < 0:
        raise ValidationError(f"Negative XML attribute: {name}")
    return n


def _xml(path, tag):
    root = ET.parse(path).getroot()
    if root.tag != "data":
        raise ValidationError(f"Expected <data> XML root: {path}")
    for elem in root:
        if elem.tag != tag:
            raise ValidationError(f"Unsupported XML operation <{elem.tag}> in {path}")
        yield elem


def make_plan(folder, loader, memory, raws, patches):
    if memory not in ("ufs", "emmc"):
        raise ValidationError("Select UFS or eMMC")
    root = Path(folder).resolve(strict=True)
    loader = Path(loader).resolve(strict=True)
    if loader.suffix.lower() not in (".elf", ".mbn"):
        raise ValidationError("Select an ELF or MBN loader")
    if not raws or not patches:
        raise ValidationError("Both rawprogram and patch XML files are required")
    if len(raws) != len(patches):
        raise ValidationError("Each rawprogram must have its corresponding patch XML")

    def index(p):
        match = re.search(r"(\d*)\.xml$", Path(p).name, re.I)
        return match[1] if match else Path(p).stem

    indices = [index(p) for p in raws]
    if len(set(indices)) != len(indices):
        raise ValidationError(
            "Ambiguous rawprogram variants. Select one XML per index."
        )
    if sorted(indices) != sorted(index(p) for p in patches):
        raise ValidationError("Missing or mismatched rawprogram/patch indices")
    programs = []
    patch_ops = []
    used = {
        str(loader),
        *[str(Path(p).resolve(strict=True)) for p in [*raws, *patches]],
    }
    for xml in raws:
        xml = Path(xml).resolve(strict=True)
        for e in _xml(xml, "program"):
            name = e.get("filename", "").strip()
            # Empty filenames describe unprogrammed partitions in standard packages.
            if not name:
                continue
            image = (xml.parent / name.replace("\\", "/")).resolve(strict=True)
            try:
                image.relative_to(root)
            except ValueError:
                raise ValidationError(f"Image escapes package folder: {name}")
            unsupported = set(e.attrib) - {
                "SECTOR_SIZE_IN_BYTES",
                "file_sector_offset",
                "filename",
                "label",
                "num_partition_sectors",
                "physical_partition_number",
                "size_in_KB",
                "sparse",
                "start_byte_hex",
                "start_sector",
                "last_sector",
                "readbackverify",
                "partofsingleimage",
            }
            if unsupported:
                raise ValidationError(
                    f"Unsupported program attributes: {sorted(unsupported)}"
                )
            if e.get("readbackverify", "false").lower() not in ("false", "0"):
                raise ValidationError(
                    "Per-image readbackverify is unsupported; default critical-area verification is separate"
                )
            if e.get("partofsingleimage", "false").lower() not in ("false", "0"):
                raise ValidationError("Single-image layouts are unsupported")
            ss = _integer(e, "SECTOR_SIZE_IN_BYTES")
            count = _integer(e, "num_partition_sectors")
            offset = _integer(e, "file_sector_offset", "0") * ss
            if ss not in (512, 4096) or count == 0:
                raise ValidationError("Invalid sector size or empty write")
            start = e.get("start_sector", "")
            evaluate(start)
            if "CRC32" in start:
                raise ValidationError("CRC32 is only allowed in patch values")
            expanded, sparse = image_info(image)
            if offset >= expanded or count * ss > expanded - offset + ss - 1:
                raise ValidationError(f"Image range exceeds file: {name}")
            if sparse:
                for _ in image_chunks(image):
                    pass
            programs.append(
                Program(
                    str(image),
                    e.get("label", image.stem),
                    _integer(e, "physical_partition_number"),
                    start,
                    count,
                    ss,
                    offset,
                    expanded,
                    sparse,
                )
            )
            used.add(str(image))
    for xml in patches:
        for e in _xml(xml, "patch"):
            # Host-file patches must not be silently skipped.
            if e.get("filename") != "DISK":
                raise ValidationError(
                    "Only DISK patch targets are supported; host-file patches require a prepared package"
                )
            unsupported = set(e.attrib) - {
                "SECTOR_SIZE_IN_BYTES",
                "byte_offset",
                "filename",
                "physical_partition_number",
                "size_in_bytes",
                "start_sector",
                "value",
                "what",
            }
            if unsupported:
                raise ValidationError(
                    f"Unsupported patch attributes: {sorted(unsupported)}"
                )
            ss = _integer(e, "SECTOR_SIZE_IN_BYTES")
            size = _integer(e, "size_in_bytes")
            byte = _integer(e, "byte_offset")
            start = e.get("start_sector", "")
            value = e.get("value", "")
            evaluate(start)
            evaluate(value)
            if (
                ss not in (512, 4096)
                or size not in (1, 2, 4, 8)
                or byte + size > ss
                or "CRC32" in start
            ):
                raise ValidationError("Unsupported patch range")
            patch_ops.append(
                Patch(
                    _integer(e, "physical_partition_number"),
                    start,
                    byte,
                    size,
                    value,
                    ss,
                    e.get("what", ""),
                )
            )
    if not programs:
        raise ValidationError("Package contains no image writes")
    if len({p.sector_size for p in [*programs, *patch_ops]}) != 1:
        raise ValidationError("Mixed sector sizes are unsupported")
    if memory == "emmc" and any(p.lun > 0 for p in [*programs, *patch_ops]):
        raise ValidationError("eMMC plan refers to nonzero physical partitions")
    return FlashPlan(
        str(root),
        str(loader),
        memory,
        tuple(programs),
        tuple(patch_ops),
        tuple(stamp(p) for p in sorted(used)),
        tuple(str(Path(p).resolve()) for p in raws),
        tuple(str(Path(p).resolve()) for p in patches),
    )


def validate_ranges(plan, capacities):
    errors = []
    warnings = []
    seen = {}
    if (
        len(
            {
                p.lun
                for p in plan.programs
                if p.label.lower() in ("xbl", "xbl_a", "sbl1")
            }
        )
        > 1
    ):
        errors.append("Ambiguous bootable storage drive")
    for op in [*plan.programs, *plan.patches]:
        capacity = capacities.get(op.lun, 0)
        if capacity <= 0:
            errors.append(f"Cannot establish capacity of LUN {op.lun}")
            continue
        start = evaluate(op.start, capacity)
        count = op.sectors if isinstance(op, Program) else 1
        if start < 0 or start + count > capacity:
            errors.append(f"Write outside LUN {op.lun}: sector {start}, length {count}")
        if isinstance(op, Program):
            for old_start, old_end, label in seen.get(op.lun, []):
                if start < old_end and start + count > old_start:
                    warnings.append(
                        f"Overlapping writes on LUN {op.lun}: {label} / {op.label}; package order is preserved"
                    )
            seen.setdefault(op.lun, []).append((start, start + count, op.label))
        else:
            value = evaluate(op.value, capacity)
            if "CRC32" not in op.value and not 0 <= value < 2 ** (8 * op.size):
                errors.append("Patch value does not fit its declared size")
    return errors, warnings
