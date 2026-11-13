"""Explicitly confirmed ADB → bound EDL → checked flash, in one worker session."""

from dataclasses import asdict
import time

from .models import DeviceInfo, TaskResult
from .expressions import ValidationError
from .package import recheck
from .engine import execute, Cancelled


def execute_one_click(
    plan,
    source,
    emit,
    stopped,
    reboot=True,
    *,
    scan_fn=None,
    enter_fn=None,
    backend_factory=None,
    clock=time.monotonic,
    sleep=time.sleep,
    timeout=60,
    expected_capacities=None,
):
    from .devices import scan, enter_edl
    from .backend import EDLBackend

    scan_fn = scan_fn or scan
    enter_fn = enter_fn or enter_edl
    backend_factory = backend_factory or EDLBackend

    def boundary():
        if stopped():
            raise Cancelled("One-click flash stopped. No further operations were sent.")

    def stage(state, **extra):
        emit("stage", {"state": state, **extra})

    try:
        boundary()
        stage("checking")
        # Validate immutable input before changing the device's operating mode.
        recheck(plan)
        boundary()
        snapshot = scan_fn()
        current = next(
            (DeviceInfo(**d) for d in snapshot["devices"] if d["id"] == source.id), None
        )
        if current is None:
            raise ValidationError(
                "Selected device is no longer connected. Refresh and select it again."
            )
        edl = [d for d in snapshot["devices"] if d["mode"] == "edl"]
        if current.mode == "edl":
            if len(edl) != 1 or (current.bus, current.address, current.serial) != (
                source.bus,
                source.address,
                source.serial,
            ):
                raise ValidationError(
                    "EDL identity changed or multiple EDL devices are connected. Refresh before retrying."
                )
            target = current
        elif current.mode == "adb":
            if current.status == "unauthorized":
                raise ValidationError(
                    "Unlock the device and accept USB debugging authorization, then Refresh."
                )
            if current.status != "device":
                raise ValidationError(
                    "ADB device is offline or unavailable. Reconnect the USB cable and Refresh."
                )
            if current.serial != source.serial:
                raise ValidationError(
                    "ADB identity changed. No reboot command was sent."
                )
            if not current.usb_path or (
                source.usb_path and current.usb_path != source.usb_path
            ):
                raise ValidationError(
                    "The physical USB connection cannot be confirmed. Use a direct USB connection, or enter EDL manually and Refresh."
                )
            if snapshot["errors"]:
                raise ValidationError(
                    "USB detection must succeed before switching to EDL: "
                    + "; ".join(snapshot["errors"])
                )
            if edl:
                raise ValidationError(
                    "Another EDL device is already connected. Disconnect it before starting this target."
                )
            boundary()
            stage("entering_edl", label=current.name)
            enter_fn(
                current
            )  # Serialized command is bound to the confirmed ADB serial.
            boundary()
            stage(
                "waiting_edl", label="Waiting for the same USB port to reconnect in EDL"
            )
            deadline = clock() + timeout
            while True:
                boundary()
                if clock() >= deadline:
                    raise ValidationError(
                        "EDL did not appear within 60 seconds. The device may not support adb reboot edl. Enter EDL using the device manufacturer method, then Refresh. No firmware was written."
                    )
                observed = scan_fn()
                candidates = [d for d in observed["devices"] if d["mode"] == "edl"]
                if len(candidates) > 1:
                    raise ValidationError(
                        "Multiple EDL devices appeared. No device was selected or flashed."
                    )
                if candidates:
                    candidate = DeviceInfo(**candidates[0])
                    if not candidate.usb_path or candidate.usb_path != current.usb_path:
                        raise ValidationError(
                            "The EDL device is not confirmed on the original USB port. Refresh and explicitly select the target. No firmware was written."
                        )
                    adb_online = any(
                        d["mode"] == "adb"
                        and d["serial"] == current.serial
                        and d["status"] == "device"
                        for d in observed["devices"]
                    )
                    if not adb_online:
                        target = candidate
                        break
                emit("activity", {})
                sleep(0.5)
        else:
            raise ValidationError("Unsupported device mode")
        boundary()
        emit(
            "device_bound",
            {
                "device": asdict(target),
                "source": asdict(source),
                "binding": "same_usb_port" if source.mode == "adb" else "selected_edl",
            },
        )
        backend = backend_factory(target, plan, emit)
        # A single Firehose connection performs geometry validation before any
        # writes. Every failure is propagated; the existing engine owns cleanup.
        return execute(
            plan,
            backend,
            emit,
            stopped,
            action="flash",
            reboot=reboot,
            expected_capacities=expected_capacities,
        )
    except Cancelled as exc:
        return TaskResult("interrupted", str(exc))
    except Exception as exc:
        return TaskResult("failed", f"{type(exc).__name__}: {exc}")
