from __future__ import annotations
import hashlib
import os
import time
import re
from pathlib import Path
from defusedxml import ElementTree as ET
from .models import FileStamp, Program, Patch, FlashPlan
from .expressions import ValidationError, evaluate
from .images import image_info, image_chunks
from .patch_layout import validate_file_mirrors


def stamp(path, stopped=lambda: False):
    path = Path(path).resolve(strict=True)
    before = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while data := f.read(4 * 1024 * 1024):
            if stopped():
                raise ValidationError("File check cancelled.")
            digest.update(data)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValidationError(f"File changed during check: {path}")
    return FileStamp(str(path), after.st_size, after.st_mtime_ns, digest.hexdigest())


def recheck(plan):
    for saved in plan.files:
        if stamp(saved.path) != saved:
            raise ValidationError(f"File changed; run checks again: {saved.path}")


def discover(
    folder,
    *,
    stopped=lambda: False,
    progress=None,
    max_entries=20000,
    max_depth=6,
    timeout=15,
):
    """Bounded streaming discovery. Never recursively walk an entire volume."""
    root = Path(folder).resolve(strict=True)
    if not root.is_dir():
        raise ValidationError("Select an extracted firmware folder")
    if root == Path.home() or os.path.ismount(root):
        raise ValidationError(
            "Select the firmware folder, not an entire disk or your home folder."
        )
    started = time.monotonic()
    count = 0
    files = []
    pending = [(root, 0)]
    while pending:
        if stopped():
            raise ValidationError("Folder scan cancelled.")
        directory, depth = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    if stopped():
                        raise ValidationError("Folder scan cancelled.")
                    count += 1
                    if count > max_entries or time.monotonic() - started > timeout:
                        raise ValidationError(
                            "Folder is too large to scan. Select the specific firmware folder instead."
                        )
                    if progress and (count == 1 or count % 100 == 0):
                        progress({"entries": count, "path": str(directory)})
                    if (
                        entry.name.startswith(".")
                        or entry.name == "__MACOSX"
                        or entry.is_symlink()
                    ):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        if depth >= max_depth:
                            raise ValidationError(
                                "Folder nesting is too deep. Select the folder containing the firmware XML files."
                            )
                        pending.append((Path(entry.path), depth + 1))
                    elif entry.is_file(follow_symlinks=False):
                        name = entry.name.lower()
                        if name.endswith((".xml", ".elf", ".mbn")):
                            files.append(Path(entry.path))
        except PermissionError as exc:
            raise ValidationError(
                f"Cannot read {directory}. Choose a firmware folder you can access."
            ) from exc
    files.sort(key=lambda p: str(p).lower())
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
    allowed = ("data", "patches") if tag == "patch" else ("data",)
    if root.tag not in allowed:
        raise ValidationError(f"Unexpected <{root.tag}> XML root: {path}")
    for elem in root:
        if elem.tag != tag:
            raise ValidationError(f"Unsupported XML operation <{elem.tag}> in {path}")
        yield elem


def make_plan(folder, loader, memory, raws, patches, *, stopped=lambda: False):
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
    file_patches = []
    used = {
        str(loader),
        *[str(Path(p).resolve(strict=True)) for p in [*raws, *patches]],
    }
    for xml in raws:
        if stopped():
            raise ValidationError("File check cancelled.")
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
            if e.get("partofsingleimage", "false").lower() not in (
                "false",
                "0",
                "true",
                "1",
            ):
                raise ValidationError("Invalid partofsingleimage flag")
            ss = _integer(e, "SECTOR_SIZE_IN_BYTES")
            count = _integer(e, "num_partition_sectors")
            offset = _integer(e, "file_sector_offset", "0") * ss
            if ss not in (512, 4096):
                raise ValidationError("Invalid sector size")
            start = e.get("start_sector", "")
            evaluate(start)
            if "CRC32" in start:
                raise ValidationError("CRC32 is only allowed in patch values")
            expanded, sparse = image_info(image)
            if offset >= expanded:
                raise ValidationError(f"Image offset exceeds file: {name}")
            available_sectors = (expanded - offset + ss - 1) // ss
            # XML count is an upper bound; zero means derive from the file.
            # Only the last physical sector is padded, never the whole partition.
            actual_sectors = (
                min(count, available_sectors) if count else available_sectors
            )
            if sparse:
                for _ in image_chunks(image):
                    if stopped():
                        raise ValidationError("File check cancelled.")
            programs.append(
                Program(
                    str(image),
                    e.get("label", image.stem),
                    _integer(e, "physical_partition_number"),
                    start,
                    actual_sectors,
                    ss,
                    offset,
                    expanded,
                    sparse,
                    count,
                )
            )
            used.add(str(image))
    for xml in patches:
        local_disk, local_host = [], []
        for e in _xml(xml, "patch"):
            if stopped():
                raise ValidationError("File check cancelled.")
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
            start, value = e.get("start_sector", ""), e.get("value", "")
            evaluate(start)
            evaluate(value)
            if (
                ss not in (512, 4096)
                or size not in (1, 2, 4, 8)
                or byte + size > ss
                or "CRC32" in start
            ):
                raise ValidationError("Unsupported patch range")
            target = e.get("filename", "")
            if target != "DISK":
                if not target:
                    raise ValidationError("Patch filename is missing")
                target = str(
                    (Path(xml).parent / target.replace("\\", "/")).resolve(strict=True)
                )
                try:
                    Path(target).relative_to(root)
                except ValueError:
                    raise ValidationError("Host patch escapes package folder")
                used.add(target)
            op = Patch(
                _integer(e, "physical_partition_number"),
                start,
                byte,
                size,
                value,
                ss,
                e.get("what", ""),
                target,
            )
            (local_disk if target == "DISK" else local_host).append(op)
        validate_file_mirrors(programs, local_disk, local_host)
        patch_ops.extend(local_disk)
        file_patches.extend(local_host)
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
        tuple(stamp(p, stopped=stopped) for p in sorted(used)),
        tuple(str(Path(p).resolve()) for p in raws),
        tuple(str(Path(p).resolve()) for p in patches),
        tuple(file_patches),
    )


def validate_ranges(plan, capacities):
    errors = []
    warnings = layout_notes(plan)
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
        limit_count = (
            (op.declared_sectors or op.sectors) if isinstance(op, Program) else count
        )
        if start < 0 or start + limit_count > capacity:
            errors.append(
                f"Write or XML allocation outside LUN {op.lun}: sector {start}, length {limit_count}"
            )
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


def layout_notes(plan):
    short = [
        p
        for p in plan.programs
        if p.declared_sectors and p.sectors < p.declared_sectors
    ]
    auto = [p for p in plan.programs if p.declared_sectors == 0]
    notes = []
    if short:
        notes.append(
            f"{len(short)} image writes are shorter than their XML allocation; only image data and final-sector padding are written. Remaining allocation is unchanged."
        )
    if auto:
        notes.append(
            f"{len(auto)} zero-count entries use the image length; no whole-partition erase or fill is performed."
        )
    if plan.file_patches:
        notes.append(
            f"{len(plan.file_patches)} host-file patches have equivalent ordered DISK patches. Only the DISK operations are sent; source images are unchanged."
        )
    return notes
