import os
import sys
from pathlib import Path


def root():
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))


def setup_vendor():
    path = str(root() / "vendor")
    if path not in sys.path:
        sys.path.insert(0, path)
    lib = root() / "resources/lib/libusb-1.0.dylib"
    if lib.exists():
        os.environ["QFT_LIBUSB"] = str(lib)


def adb_path():
    bundled = root() / "resources/bin/adb"
    if bundled.is_file():
        return str(bundled)
    # Developer fallback only; packaged builds never depend on PATH.
    if not getattr(sys, "frozen", False):
        from shutil import which

        return which("adb") or ""
    return ""


def data_dir():
    path = Path(
        os.environ.get(
            "QFT_DATA_DIR",
            str(Path.home() / "Library/Application Support/QualcommFlashTool"),
        )
    )
    path.mkdir(parents=True, exist_ok=True)
    return path
