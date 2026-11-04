from __future__ import annotations
from dataclasses import dataclass, asdict
from typing import Any

PROTOCOL_VERSION = 1


@dataclass(frozen=True)
class DeviceInfo:
    id: str
    mode: str
    name: str
    status: str
    serial: str = ""
    bus: int = 0
    address: int = 0
    vid: int = 0x05C6
    pid: int = 0x9008


@dataclass(frozen=True)
class FileStamp:
    path: str
    size: int
    mtime_ns: int
    sha256: str


@dataclass(frozen=True)
class Program:
    path: str
    label: str
    lun: int
    start: str
    sectors: int
    sector_size: int
    file_offset: int
    expanded_size: int
    sparse: bool = False


@dataclass(frozen=True)
class Patch:
    lun: int
    start: str
    byte_offset: int
    size: int
    value: str
    sector_size: int
    description: str = ""


@dataclass(frozen=True)
class FlashPlan:
    folder: str
    loader: str
    memory: str
    programs: tuple[Program, ...]
    patches: tuple[Patch, ...]
    files: tuple[FileStamp, ...]
    raw_files: tuple[str, ...]
    patch_files: tuple[str, ...]

    @classmethod
    def from_dict(cls, d):
        return cls(
            **{
                **d,
                "programs": tuple(Program(**p) for p in d["programs"]),
                "patches": tuple(Patch(**p) for p in d["patches"]),
                "files": tuple(FileStamp(**p) for p in d["files"]),
                "raw_files": tuple(d["raw_files"]),
                "patch_files": tuple(d["patch_files"]),
            }
        )


@dataclass(frozen=True)
class PreflightReport:
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    capacities: tuple[tuple[int, int], ...] = ()
    ready: bool = False


@dataclass(frozen=True)
class TaskEvent:
    kind: str
    data: dict[str, Any]
    version: int = PROTOCOL_VERSION


@dataclass(frozen=True)
class TaskResult:
    status: str
    message: str
    verified: tuple[str, ...] = ()
    unverified: tuple[str, ...] = ()
    rebooted: bool = False


def serializable(value):
    return asdict(value)
