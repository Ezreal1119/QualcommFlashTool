import subprocess
from dataclasses import asdict
from .runtime import setup_vendor, adb_path, root
from .models import DeviceInfo
from .expressions import ValidationError


def usb_backend():
    import os
    import usb.backend.libusb1
    import ctypes.util

    return usb.backend.libusb1.get_backend(
        find_library=lambda _: os.environ.get("QFT_LIBUSB")
        or ctypes.util.find_library("usb-1.0")
    )


def environment():
    setup_vendor()

    backend = usb_backend()
    edl_ok = True
    edl_detail = "3.62 · controlled adapter"
    try:
        import importlib

        for module in (
            "edlclient.Library.sahara",
            "edlclient.Library.firehose",
            "Cryptodome.Cipher.AES",
        ):
            importlib.import_module(module)
    except Exception as exc:
        edl_ok = False
        edl_detail = f"EDL runtime failed to import: {exc}"
    path = adb_path()
    checks = []
    checks.append(
        {
            "name": "ADB",
            "ok": bool(path),
            "detail": path or "ADB is missing from this build",
        }
    )
    checks.append(
        {
            "name": "USB runtime",
            "ok": backend is not None,
            "detail": "libusb loaded" if backend else "libusb could not be loaded",
        }
    )
    checks.append(
        {
            "name": "EDL baseline",
            "ok": edl_ok
            and (root() / "vendor/edlclient/Library/firehose.py").is_file(),
            "detail": edl_detail,
        }
    )
    checks.append(
        {
            "name": "Hardware validation",
            "ok": False,
            "detail": "Not yet validated on a physical device",
        }
    )
    return checks


def scan():
    setup_vendor()
    found = []
    errors = []
    path = adb_path()
    if path:
        try:
            result = subprocess.run(
                [path, "devices", "-l"], capture_output=True, text=True, timeout=10
            )
            if result.returncode:
                errors.append(result.stderr.strip() or "ADB device scan failed")
            for line in result.stdout.splitlines()[1:]:
                fields = line.split()
                if len(fields) < 2:
                    continue
                serial, status = fields[:2]
                details = dict(x.split(":", 1) for x in fields[2:] if ":" in x)
                found.append(
                    DeviceInfo(
                        "adb:" + serial,
                        "adb",
                        details.get("model", "Android device").replace("_", " "),
                        status,
                        serial,
                    )
                )
        except Exception as exc:
            errors.append(f"ADB: {exc}")
    try:
        import usb.core
        import usb.backend.libusb1

        backend = usb_backend()
        if backend is None:
            raise ValidationError("Bundled libusb is unavailable")
        for dev in usb.core.find(
            find_all=True, idVendor=0x05C6, idProduct=0x9008, backend=backend
        ):
            try:
                serial = dev.serial_number or ""
            except Exception:
                serial = ""
            found.append(
                DeviceInfo(
                    f"usb:{dev.bus}:{dev.address}",
                    "edl",
                    "Qualcomm HS-USB QDLoader 9008",
                    "ready",
                    serial,
                    dev.bus or 0,
                    dev.address or 0,
                )
            )
    except Exception as exc:
        errors.append(f"USB: {exc}. Check cable and device access permissions.")
    return {"devices": [asdict(d) for d in found], "errors": errors}


def enter_edl(device):
    if device.mode != "adb" or device.status != "device":
        raise ValidationError("Select an authorized, online ADB device")
    result = subprocess.run(
        [adb_path(), "-s", device.serial, "reboot", "edl"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode:
        raise ValidationError(result.stderr.strip() or "Device rejected reboot edl")
