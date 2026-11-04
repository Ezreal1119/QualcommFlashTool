import contextlib
import json
import logging
import sys
import threading
from dataclasses import asdict
from .models import PROTOCOL_VERSION, DeviceInfo, FlashPlan
from .runtime import setup_vendor


def main():
    wire = sys.stdout
    lock = threading.Lock()
    stopped = threading.Event()

    def emit(kind, data):
        with lock:
            wire.write(
                json.dumps(
                    {"version": PROTOCOL_VERSION, "kind": kind, "data": data},
                    ensure_ascii=False,
                )
                + "\n"
            )
            wire.flush()

    try:
        request = json.loads(sys.stdin.readline())
        if request.get("version") != PROTOCOL_VERSION:
            raise ValueError("Worker protocol version mismatch")

        def reader():
            for line in sys.stdin:
                try:
                    if json.loads(line).get("action") == "stop":
                        stopped.set()
                except ValueError:
                    pass

        threading.Thread(target=reader, daemon=True).start()
        with contextlib.redirect_stdout(sys.stderr):
            logging.basicConfig(stream=sys.stderr, level=logging.INFO)
            action = request["action"]
            setup_vendor()
            if action == "environment":
                from .devices import environment

                emit("environment", {"checks": environment()})
                result = {
                    "status": "success",
                    "message": "Runtime checks complete. Connect a device and import an extracted firmware folder.",
                }
            elif action == "scan":
                from .devices import scan, environment

                emit("devices", scan())
                emit("environment", {"checks": environment()})
                result = {"status": "success", "message": "Device list refreshed."}
            elif action == "discover":
                from .package import discover

                emit("package", discover(request["folder"]))
                result = {
                    "status": "success",
                    "message": "Package candidates loaded. Select XML variants and loader, then validate.",
                }
            elif action == "validate":
                from .package import make_plan

                plan = make_plan(
                    request["folder"],
                    request["loader"],
                    request["memory"],
                    request["raws"],
                    request["patches"],
                )
                emit("plan", asdict(plan))
                result = {
                    "status": "ready",
                    "message": "Package files validated. Inspect the selected EDL device next.",
                }
            elif action == "enter_edl":
                from .devices import enter_edl

                enter_edl(DeviceInfo(**request["device"]))
                result = {
                    "status": "success",
                    "message": "EDL requested. Refresh devices and select the EDL device; identity must be re-established.",
                }
            elif action in ("inspect", "flash", "reset"):
                from .backend import EDLBackend
                from .engine import execute

                plan = FlashPlan.from_dict(request["plan"])
                device = DeviceInfo(**request["device"])
                backend = EDLBackend(device, plan, emit)
                result = asdict(
                    execute(
                        plan,
                        backend,
                        emit,
                        stopped.is_set,
                        action,
                        request.get("reboot", True),
                        request.get("capacities"),
                    )
                )
            else:
                raise ValueError("Unsupported worker action")
        emit("result", result)
    except BaseException as exc:
        import traceback

        traceback.print_exc(file=sys.stderr)
        emit("result", {"status": "failed", "message": f"{type(exc).__name__}: {exc}"})
