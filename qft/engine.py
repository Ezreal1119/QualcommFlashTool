import zlib
from .models import TaskResult
from .expressions import ValidationError, evaluate
from .package import recheck, validate_ranges
from .images import image_range
from .verification import verify_gpt, expected_digest


class Cancelled(Exception):
    pass


def execute(
    plan, backend, emit, stopped, action="flash", reboot=True, expected_capacities=None
):
    verified = []
    unverified = []
    patch_values = []

    def stage(name, **data):
        emit("stage", {"state": name, **data})

    def boundary():
        if stopped():
            raise Cancelled("Stopped at an operation boundary. Device was not reset.")

    try:
        if action not in ("inspect", "flash", "reset"):
            raise ValidationError("Unsupported device action")
        stage("checking")
        recheck(plan)
        boundary()
        stage("connecting")
        capacities = backend.connect()
        boundary()
        if (
            expected_capacities
            and {int(k): v for k, v in expected_capacities.items()} != capacities
        ):
            raise ValidationError(
                "Device capacity changed since inspection. Run checks again."
            )
        errors, warnings = validate_ranges(plan, capacities)
        if errors:
            raise ValidationError("\n".join(errors))
        emit(
            "preflight",
            {
                "capacities": capacities,
                "warnings": warnings,
                "ready": True,
                "identity_match": "Model / firmware compatibility has not been established",
            },
        )
        if action == "inspect":
            return TaskResult(
                "ready", "Device geometry checked. Review the plan before flashing."
            )
        if action == "reset":
            stage("rebooting")
            backend.reset()
            return TaskResult("success", "Reset command acknowledged.", rebooted=True)
        total = sum(p.sectors * p.sector_size for p in plan.programs)
        done = 0
        for index, op in enumerate(plan.programs):
            boundary()
            stage(
                "flashing",
                file=op.path,
                label=op.label,
                index=index + 1,
                count=len(plan.programs),
            )
            start = evaluate(op.start, capacities[op.lun])
            size = op.sectors * op.sector_size
            backend.program(
                op,
                start,
                image_range(op.path, op.file_offset, size),
                lambda sent: emit(
                    "progress",
                    {
                        "done": done + sent,
                        "total": total,
                        "file_done": sent,
                        "file_total": size,
                    },
                ),
            )
            done += size
            emit("operation", {"label": op.label, "status": "acknowledged"})
        for index, op in enumerate(plan.patches):
            boundary()
            stage(
                "patching",
                index=index + 1,
                count=len(plan.patches),
                label=op.description,
            )
            cap = capacities[op.lun]
            start = evaluate(op.start, cap)

            def crc(sector, length):
                if sector + (length + op.sector_size - 1) // op.sector_size > cap:
                    raise ValidationError("CRC32 read exceeds capacity")
                if length == 0:
                    return 0
                return (
                    zlib.crc32(
                        backend.read(
                            op.lun,
                            sector,
                            (length + op.sector_size - 1) // op.sector_size,
                        )[:length]
                    )
                    & 0xFFFFFFFF
                )

            value = evaluate(op.value, cap, crc)
            if not 0 <= value < 2 ** (8 * op.size):
                raise ValidationError("Resolved patch value does not fit")
            backend.patch(op, start, value)
            patch_values.append((op, start, value))
        boot_luns = {
            p.lun for p in plan.programs if p.label.lower() in ("xbl", "xbl_a", "sbl1")
        }
        if len(boot_luns) > 1:
            raise ValidationError("Ambiguous bootable storage drive")
        if boot_luns:
            boundary()
            backend.set_bootable(next(iter(boot_luns)))
        stage("verifying")
        for lun in sorted({p.lun for p in plan.programs}):
            boundary()
            required = any(
                p.lun == lun and "gpt" in p.label.lower() for p in plan.programs
            )
            if verify_gpt(
                backend, lun, capacities[lun], plan.programs[0].sector_size, required
            ):
                verified.append(f"LUN {lun}: primary / backup GPT and CRC")
            else:
                unverified.append(f"LUN {lun}: no GPT available to verify")
        for op in plan.programs:
            if op.label.lower() not in (
                "boot",
                "boot_a",
                "boot_b",
                "vbmeta",
                "vbmeta_a",
                "vbmeta_b",
            ):
                continue
            boundary()
            stage("verifying", label=op.label)
            actual = backend.digest(
                op.lun, evaluate(op.start, capacities[op.lun]), op.sectors
            )
            if actual is None:
                unverified.append(
                    f"{op.label}: loader did not advertise SHA-256 support"
                )
                continue
            expected = expected_digest(plan, op, capacities[op.lun], patch_values)
            if actual != expected:
                raise ValidationError(f"{op.label}: SHA-256 verification failed")
            verified.append(f"{op.label}: final SHA-256 digest")
        if unverified:
            return TaskResult(
                "incomplete",
                "Writes acknowledged; some verification was unavailable. Review details before manually rebooting.",
                tuple(verified),
                tuple(unverified),
            )
        if reboot:
            boundary()
            stage("rebooting")
            try:
                backend.reset()
            except Exception as exc:
                return TaskResult(
                    "reboot_failed",
                    f"Flash verified, but reboot failed: {exc}",
                    tuple(verified),
                )
        return TaskResult(
            "success",
            "Flash completed and required checks passed.",
            tuple(verified),
            rebooted=reboot,
        )
    except Cancelled as exc:
        return TaskResult("interrupted", str(exc), tuple(verified), tuple(unverified))
    except Exception as exc:
        return TaskResult(
            "failed", f"{type(exc).__name__}: {exc}", tuple(verified), tuple(unverified)
        )
    finally:
        backend.close()
