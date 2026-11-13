"""Short-lived evidence for reconnecting to this tool's own RAM programmer.

An OS USB session ID changes on reconnect. Bus/address/port alone can be reused
and are insufficient evidence. No cache is accepted when OS evidence is absent.
This is local workflow state, not protection against malicious local software.
"""

import json
import plistlib
import subprocess
import sys
import time
from dataclasses import asdict

from .expressions import ValidationError
from .loaders import DeviceIdentity, select_loader
from .runtime import data_dir
from .storage import atomic_json

TTL = 300


def usb_session(device):
    if sys.platform != "darwin":
        return None
    try:
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-a", "-p", "IOUSB", "-l"],
            capture_output=True,
            timeout=5,
        )
        if result.returncode or len(result.stdout) > 16 * 1024**2:
            return None
        found = []

        def visit(nodes):
            if not isinstance(nodes, list):
                return
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                if (
                    node.get("idVendor") == device.vid
                    and node.get("idProduct") == device.pid
                    and node.get("USB Address") == device.address
                    and isinstance(node.get("locationID"), int)
                    and node["locationID"] >> 24 == device.bus
                    and isinstance(node.get("sessionID"), int)
                ):
                    found.append(
                        {
                            "session": node["sessionID"],
                            "location": node["locationID"],
                            "vid": device.vid,
                            "pid": device.pid,
                            "address": device.address,
                        }
                    )
                visit(node.get("IORegistryEntryChildren", []))

        visit(plistlib.loads(result.stdout))
        return found[0] if len(found) == 1 else None
    except (
        OSError,
        ValueError,
        TypeError,
        subprocess.SubprocessError,
        plistlib.InvalidFileException,
    ):
        return None


def _path():
    return data_dir() / "loader-session.json"


def forget():
    _path().unlink(missing_ok=True)


def remember(token, identity, loader):
    if token is not None:
        atomic_json(
            _path(),
            {
                "version": 1,
                "time": time.time(),
                "usb": token,
                "identity": asdict(identity),
                "sha256": loader.sha256,
            },
        )


def resume(token, plan, emit):
    try:
        path = _path()
        if token is None or path.stat().st_size > 32768:
            raise ValueError("Missing OS USB session evidence")
        record = json.loads(path.read_text())
        if (
            record["version"] != 1
            or record["usb"] != token
            or not 0 <= time.time() - record["time"] <= TTL
        ):
            raise ValueError("Expired or different USB session")
        identity = DeviceIdentity(**record["identity"])
        loader = select_loader(plan, identity, emit)
        if loader.sha256 != record["sha256"]:
            raise ValueError("RAM programmer differs from selected programmer")
        emit(
            "log",
            {
                "text": "Reusing this tool's verified programmer on the same OS USB session.\n"
            },
        )
        return identity, loader
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValidationError(
            "Cannot authenticate the existing RAM programmer. Re-enter normal EDL/Sahara mode "
            "and check again. No firmware was written."
        ) from exc
