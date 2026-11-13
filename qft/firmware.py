"""Offline import and conservative automatic configuration of firmware packages."""

from __future__ import annotations

import re
import shutil
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from defusedxml import ElementTree as ET

from .expressions import ValidationError
from .package import discover
from .loaders import inspect_loader
from .runtime import data_dir

MAX_EXPANDED = 128 * 1024**3


def import_firmware(source, emit=lambda *args: None, stopped=lambda: False):
    source = Path(source).resolve(strict=True)
    if source.is_dir():
        return source
    if source.suffix.lower() != ".zip":
        raise ValidationError("Choose an extracted firmware folder or a ZIP package.")
    destination = data_dir() / "packages"
    destination.mkdir(exist_ok=True)
    # Every import is independent: edits to an earlier import are never discarded.
    folder = Path(tempfile.mkdtemp(prefix="firmware-", dir=destination))
    emit("import_workspace", {"path": str(folder)})
    try:
        with zipfile.ZipFile(source) as archive:
            members = archive.infolist()
            if len(members) > 100_000:
                raise ValidationError("ZIP contains too many entries.")
            expanded = sum(m.file_size for m in members)
            if (
                expanded > MAX_EXPANDED
                or expanded + 256 * 1024**2 > shutil.disk_usage(destination).free
            ):
                raise ValidationError(
                    "Not enough space to extract this package, or package exceeds 128 GB."
                )
            names = set()
            for member in members:
                name = member.filename.replace("\\", "/")
                path = PurePosixPath(name)
                mode = member.external_attr >> 16
                if (
                    not name
                    or not path.parts
                    or path.is_absolute()
                    or ".." in path.parts
                    or "\x00" in name
                    or ":" in path.parts[0]
                    or stat.S_ISLNK(mode)
                    or (
                        stat.S_IFMT(mode)
                        and not (stat.S_ISREG(mode) or stat.S_ISDIR(mode))
                    )
                ):
                    raise ValidationError(f"Unsafe ZIP entry: {name}")
                key = str(path).casefold().rstrip("/")
                if key in names:
                    raise ValidationError(f"Duplicate ZIP entry: {name}")
                names.add(key)
                if member.flag_bits & 1:
                    raise ValidationError("Encrypted ZIP packages are unsupported.")
            done = 0
            for member in members:
                if stopped():
                    raise ValidationError("Package import stopped.")
                path = folder.joinpath(
                    *PurePosixPath(member.filename.replace("\\", "/")).parts
                )
                if member.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as src, path.open("xb") as dst:
                    while chunk := src.read(4 * 1024**2):
                        if stopped():
                            raise ValidationError("Package import stopped.")
                        dst.write(chunk)
                        done += len(chunk)
                        if done > MAX_EXPANDED:
                            raise ValidationError("Expanded ZIP exceeds import limit.")
                        emit("activity", {})
                        emit("import_progress", {"done": done, "total": expanded})
        emit(
            "log",
            {
                "text": f"Extracted a working copy to {folder}\nOriginal archive was not modified.\n"
            },
        )
        return folder
    except BaseException:
        shutil.rmtree(folder)
        raise


def automatic_configuration(folder, *, stopped=lambda: False, progress=None):
    candidates = discover(folder, stopped=stopped, progress=progress)
    issues = []
    raws, patches, loaders = (candidates[k] for k in ("raws", "patches", "loaders"))

    def indices(paths):
        return [re.search(r"(\d*)\.xml$", Path(p).name, re.I)[1] for p in paths]

    raw_indices, patch_indices = indices(raws), indices(patches)
    if not raws:
        issues.append("No rawprogram XML found. Add files in Advanced configuration.")
    if len(set(raw_indices)) != len(raw_indices):
        issues.append(
            "Multiple rawprogram variants found. Choose one per index in Advanced configuration."
        )
    if (
        not patches
        or len(set(patch_indices)) != len(patch_indices)
        or sorted(raw_indices) != sorted(patch_indices)
    ):
        issues.append(
            "Missing or ambiguous patch pairs. Review XML selections in Advanced configuration."
        )
    verified, loader_reports = [], []
    if len(loaders) > 128:
        raise ValidationError(
            "Too many ELF/MBN files. Select the extracted firmware subfolder, or configure one loader manually."
        )
    for path in loaders:
        if stopped():
            raise ValidationError("File check cancelled.")
        try:
            info = inspect_loader(path)
            verified.append(path)
            loader_reports.append(
                {
                    "path": path,
                    "sha256": info.sha256,
                    "status": "integrity_verified",
                    "message": "Hardware match pending Sahara identification",
                }
            )
        except (ValidationError, OSError) as exc:
            loader_reports.append(
                {"path": path, "status": "excluded", "message": str(exc)}
            )
        if progress:
            progress({"entries": len(loader_reports), "path": path})
    if not verified:
        issues.append(
            "No supported, signed device programmer found. "
            + "; ".join(
                f"{Path(r['path']).name}: {r['message']}" for r in loader_reports
            )
        )
    try:
        memory = infer_memory(raws)
    except Exception as exc:
        issues.append(f"Cannot read package layout: {exc}")
        memory = ""
    if not memory:
        issues.append(
            "Storage type could not be inferred. Select it in Advanced configuration."
        )
    return {
        **candidates,
        "folder": str(Path(folder).resolve()),
        "issues": issues,
        "memoryHint": memory,
        # This is provisional; only Sahara identity matching authorizes upload.
        "loader": verified[0] if verified else "",
        "loaderCandidates": verified,
        "loaderReports": loader_reports,
        "selectedRaws": raws if len(set(raw_indices)) == len(raw_indices) else [],
        "selectedPatches": patches
        if len(set(patch_indices)) == len(patch_indices)
        else [],
    }


def infer_memory(raws):
    """Infer only a package hint. Device configure and geometry must confirm it."""
    sizes, luns = set(), set()
    for path in raws:
        xml = ET.parse(path).getroot()
        for item in xml.findall("program"):
            if not item.get("filename", "").strip():
                continue
            sizes.add(int(item.get("SECTOR_SIZE_IN_BYTES", "0")))
            luns.add(int(item.get("physical_partition_number", "0")))
    if sizes == {4096} or (sizes == {512} and any(luns)):
        return "ufs"
    if sizes == {512}:
        return "emmc"
    return ""
