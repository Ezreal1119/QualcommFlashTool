"""Validate architecture, deployment targets and runtime resource completeness."""

from pathlib import Path
import sys
from macholib.MachO import MachO
from macholib.mach_o import LC_BUILD_VERSION, LC_VERSION_MIN_MACOSX

root = Path(sys.argv[1] if len(sys.argv) > 1 else "dist/QualcommFlashTool.app")
errors = []
checked = 0
visited = set()
for p in root.rglob("*"):
    if not p.is_file() or p.resolve() in visited:
        continue
    visited.add(p.resolve())
    with p.open("rb") as f:
        magic = f.read(4)
    if magic not in (
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
        b"\xbe\xba\xfe\xca",
    ):
        continue
    checked += 1
    try:
        macho = MachO(str(p))
        arm = False
        for header in macho.headers:
            if header.header.cputype != 0x100000C:
                continue
            arm = True
            for load, cmd, data in header.commands:
                version = (
                    cmd.minos
                    if load.cmd == LC_BUILD_VERSION
                    else cmd.version
                    if load.cmd == LC_VERSION_MIN_MACOSX
                    else None
                )
                if version and version > (13 << 16):
                    errors.append(
                        f"{p}: requires macOS {version >> 16}.{(version >> 8) & 255}"
                    )
        if not arm:
            errors.append(f"{p}: has no arm64 slice")
    except Exception as exc:
        errors.append(f"{p}: Mach-O inspection failed: {exc}")
for resource in (
    "qft/ui/Main.qml",
    "vendor/edlclient/Library/firehose.py",
    "vendor/baseline.json",
    "resources/bin/adb",
    "resources/lib/libusb-1.0.dylib",
    "resources/python-licenses",
    "THIRD_PARTY_NOTICES.md",
):
    if not (root / "Contents/Resources" / resource).exists():
        errors.append("Missing resource: " + resource)
print(f"Inspected {checked} Mach-O files")
if errors:
    print("\n".join(errors))
    raise SystemExit(1)
print("arm64 / macOS 13 deployment and bundled resources: OK")
