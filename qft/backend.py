"""Checked, private adapter over pinned EDL Sahara / Firehose primitives.

The upstream qfil handler, configure auto-switching and expression evaluator are
not called. Every transfer requires initial AND final ACK. No automatic reset.
"""

import json
import logging
import re
from xml.sax.saxutils import quoteattr
from .runtime import setup_vendor
from .expressions import ValidationError
from .devices import scan


class EDLBackend:
    def __init__(self, device, plan, emit):
        self.device = device
        self.plan = plan
        self.emit = emit
        self.cdc = None
        self.fh = None
        self.capacities = {}
        self.supports_digest = False

    def activity(self):
        self.emit("activity", {})

    def connect(self):
        setup_vendor()
        from edlclient.Library.Connection.usblib import usb_class
        from edlclient.Library.sahara import sahara
        from edlclient.Library.sahara_defs import cmd_t, sahara_mode_t
        from edlclient.Library.firehose import firehose
        from edlclient.Library.xmlparser import xmlparser

        candidates = [d for d in scan()["devices"] if d["mode"] == "edl"]
        if len(candidates) != 1:
            raise ValidationError("Connect exactly one EDL device before continuing")
        current = candidates[0]
        if (current["bus"], current["address"], current["serial"]) != (
            self.device.bus,
            self.device.address,
            self.device.serial,
        ):
            raise ValidationError(
                "USB identity changed. Refresh and select the device again."
            )
        self.cdc = usb_class(
            portconfig=[[self.device.vid, self.device.pid, -1]], loglevel=logging.INFO
        )
        self.cdc.selected_location = (self.device.bus, self.device.address)
        self.cdc.timeout = 1500
        if not self.cdc.connect():
            raise ValidationError(
                "Cannot open EDL device. Check USB access permissions and other running tools."
            )
        old_read = self.cdc.read

        def checked_write(data, *args, **kwargs):
            # Upstream transport retries and ignores short transfers. Never retry
            # raw writes: their device-side completion is unknown after failure.
            data = data.encode() if isinstance(data, str) else data
            length = len(data)
            sent = 0
            if not length:
                self.cdc.EP_OUT.write(b"", timeout=1500)
            while sent < length:
                packet = data[sent : sent + 1048576]
                count = self.cdc.EP_OUT.write(packet, timeout=1500)
                if count != len(packet):
                    raise IOError("Short USB write; no retry was attempted")
                sent += count
                self.activity()
            return True

        def checked_read(*args, **kwargs):
            data = old_read(*args, **kwargs)
            if data:
                self.activity()
            return data

        self.cdc.write = checked_write
        self.cdc.read = checked_read
        sah = sahara(self.cdc, logging.INFO)
        sah.programmer = self.plan.loader
        info = sah.connect()
        mode = info.get("mode", "error")
        if mode == "sahara":
            hello = info.get("data")
            version = getattr(hello, "version", 2)
            if getattr(hello, "mode", None) == sahara_mode_t.SAHARA_MODE_MEMORY_DEBUG:
                raise ValidationError(
                    "Memory-debug / streaming mode is unsupported. Re-enter normal EDL mode."
                )
            if info.get("cmd") != cmd_t.SAHARA_HELLO_REQ or not sah.cmd_info(
                version=version
            ):
                raise ValidationError("Sahara handshake failed. No reset was sent.")
            info = sah.connect()
            mode = info.get("mode", "error")
            if mode == "sahara":
                mode = sah.upload_loader(version=version)
        if mode != "firehose":
            raise ValidationError(
                f"Loader did not enter Firehose ({mode}). Check loader compatibility or device authorization."
            )
        cfg = firehose.cfg()
        cfg.MemoryName = self.plan.memory
        cfg.SECTOR_SIZE_IN_BYTES = self.plan.programs[0].sector_size
        cfg.programmer = self.plan.loader
        cfg.bit64 = sah.bit64
        self.fh = firehose(
            self.cdc,
            xmlparser(),
            cfg,
            logging.INFO,
            "",
            sah.serial,
            False,
            sorted({p.lun for p in self.plan.programs}),
            {},
        )
        self.cdc.timeout = 1000
        # Drain initial loader greeting without loading shared edl_config.json.
        greeting = bytearray()
        for _ in range(4):
            data = self.cdc.read(timeout=100)
            if not data:
                break
            greeting += data
        self.supports_digest = b"getsha256digest" in greeting.lower()
        self.emit("log", {"text": greeting.decode("utf-8", errors="replace")})
        attrs = {
            "MemoryName": self.plan.memory,
            "Verbose": 0,
            "MaxPayloadSizeToTargetInBytes": 1048576,
            "ZLPAwareHost": 1,
            "SkipStorageInit": 0,
            "SkipWrite": 0,
        }
        for attempt in range(2):
            rsp = self.fh.xmlsend(self.xml("configure", attrs))
            raw = self._response_fields(rsp)
            if self.acked(rsp):
                break
            supported = raw.get("MaxPayloadSizeToTargetInBytesSupported")
            if attempt == 0 and supported and 512 <= int(supported) <= 1048576:
                attrs["MaxPayloadSizeToTargetInBytes"] = int(supported)
                continue
            raise ValidationError(
                f"Firehose configuration rejected: {rsp.error}. Verify memory type and authorization."
            )
        reported = raw.get("MemoryName", self.plan.memory).lower().replace("-", "")
        if reported != self.plan.memory:
            raise ValidationError(
                "Device memory type conflicts with the selected package"
            )
        payload = int(
            raw.get(
                "MaxPayloadSizeToTargetInBytes", attrs["MaxPayloadSizeToTargetInBytes"]
            )
        )
        if (
            not cfg.SECTOR_SIZE_IN_BYTES <= payload <= 16 * 1024 * 1024
            or payload % cfg.SECTOR_SIZE_IN_BYTES
        ):
            raise ValidationError("Invalid negotiated transfer size")
        cfg.MaxPayloadSizeToTargetInBytes = payload
        for lun in sorted({p.lun for p in [*self.plan.programs, *self.plan.patches]}):
            rsp = self.fh.xmlsend(
                self.xml("getstorageinfo", {"physical_partition_number": lun})
            )
            self.require(rsp, "storage information")
            fields = self._storage_fields(rsp)
            reported_memory = (
                str(fields.get("mem_type", self.plan.memory)).lower().replace("-", "")
            )
            if reported_memory != self.plan.memory:
                raise ValidationError(f"LUN {lun} reported a conflicting memory type")
            self.capacities[lun] = self.geometry(fields, lun, cfg.SECTOR_SIZE_IN_BYTES)
        return self.capacities

    @classmethod
    def geometry(cls, fields, lun, sector_size):
        blocks = fields.get(
            "total_blocks",
            fields.get("total_sectors", fields.get("ufs lu total blocks")),
        )
        unit = fields.get(
            "block_size",
            fields.get(
                "logical_block_size",
                fields.get("sector_size", fields.get("ufs block size")),
            ),
        )
        if "total_sectors" in fields:
            unit = fields.get("sector_size", fields.get("page_size"))
        try:
            blocks = cls.number(blocks)
            unit = cls.number(unit)
            page = cls.number(fields.get("page_size", fields.get("sector_size", unit)))
            reported_lun = cls.number(fields.get("physical_partition_number", lun))
        except Exception as exc:
            raise ValidationError(
                f"Cannot establish capacity of LUN {lun}; device must report block count and logical block size. No write was started."
            ) from exc
        if (
            page != sector_size
            or unit != sector_size
            or blocks <= 0
            or reported_lun != lun
        ):
            raise ValidationError(f"Invalid or conflicting geometry on LUN {lun}")
        return blocks

    @staticmethod
    def number(v):
        return int(str(v), 16) if str(v).lower().startswith("0x") else int(v)

    @staticmethod
    def xml(tag, attrs):
        return (
            '<?xml version="1.0"?><data><'
            + tag
            + " "
            + " ".join(k + "=" + quoteattr(str(v)) for k, v in attrs.items())
            + " /></data>"
        )

    def _response_fields(self, rsp):
        if isinstance(rsp.resp, dict):
            return rsp.resp
        if isinstance(rsp.data, dict):
            return rsp.data
        try:
            return self.fh.xml.getresponse(rsp.data)
        except Exception:
            return {}

    def acked(self, rsp):
        fields = self._response_fields(rsp)
        return fields.get("value") == "ACK" if "value" in fields else rsp.resp is True

    def require(self, rsp, operation):
        if not self.acked(rsp):
            raise IOError(f"{operation} rejected: {rsp.error}")

    def _storage_fields(self, rsp):
        fields = {str(k).lower(): v for k, v in self._response_fields(rsp).items()}
        lines = rsp.log or []
        for line in lines:
            self.emit("log", {"text": str(line) + "\n"})
            begin = str(line).find("{")
            if begin >= 0:
                try:
                    obj = json.loads(str(line)[begin:])
                    fields.update(obj.get("storage_info", obj))
                except ValueError:
                    pass
            match = re.match(
                r"(?:INFO:\s*)?([^:=]+)\s*[:=]\s*(0x[0-9a-fA-F]+|\d+)\b",
                str(line).strip(),
            )
            if match:
                fields[match[1].strip().lower()] = match[2]
        return fields

    def _final_ack(self, operation):
        data = self.fh.wait_for_data()
        fields = self.fh.xml.getresponse(data)
        if fields.get("value") != "ACK":
            raise IOError(
                f"{operation} final ACK missing or rejected: {self.fh.xml.getlog(data)}"
            )

    def program(self, op, start, chunks, progress):
        attrs = {
            "SECTOR_SIZE_IN_BYTES": op.sector_size,
            "num_partition_sectors": op.sectors,
            "physical_partition_number": op.lun,
            "start_sector": start,
        }
        self.require(self.fh.xmlsend(self.xml("program", attrs)), "program")
        sent = 0
        pending = bytearray()
        limit = self.fh.cfg.MaxPayloadSizeToTargetInBytes
        for chunk in chunks:
            pending.extend(chunk)
            while len(pending) >= limit:
                self.cdc.write(bytes(pending[:limit]))
                del pending[:limit]
                sent += limit
                progress(sent)
        if pending:
            self.cdc.write(bytes(pending))
            sent += len(pending)
            progress(sent)
        if sent != op.sectors * op.sector_size:
            raise IOError("Image stream length mismatch")
        self.cdc.write(b"")
        self._final_ack("program")

    def patch(self, op, start, value):
        self.require(
            self.fh.xmlsend(
                self.xml(
                    "patch",
                    {
                        "SECTOR_SIZE_IN_BYTES": op.sector_size,
                        "byte_offset": op.byte_offset,
                        "filename": "DISK",
                        "physical_partition_number": op.lun,
                        "size_in_bytes": op.size,
                        "start_sector": start,
                        "value": value,
                    },
                )
            ),
            "patch",
        )

    def read(self, lun, start, count):
        ss = self.fh.cfg.SECTOR_SIZE_IN_BYTES
        if count <= 0 or count * ss > 64 * 1024 * 1024:
            raise ValidationError("Invalid verification read size")
        self.require(
            self.fh.xmlsend(
                self.xml(
                    "read",
                    {
                        "SECTOR_SIZE_IN_BYTES": ss,
                        "num_partition_sectors": count,
                        "physical_partition_number": lun,
                        "start_sector": start,
                    },
                )
            ),
            "read",
        )
        self.cdc.xmlread = False
        data = bytearray()
        remaining = count * ss
        try:
            while remaining:
                chunk = self.cdc.read(min(remaining, 1048576))
                if not chunk:
                    raise IOError("USB disconnected or timed out during verification")
                data += chunk
                remaining -= len(chunk)
        finally:
            self.cdc.xmlread = True
        if remaining != 0:
            raise IOError("Device returned an unexpected verification length")
        self._final_ack("read")
        return bytes(data)

    def digest(self, lun, start, count):
        if not self.supports_digest:
            return None
        rsp = self.fh.xmlsend(
            self.xml(
                "getsha256digest",
                {
                    "SECTOR_SIZE_IN_BYTES": self.fh.cfg.SECTOR_SIZE_IN_BYTES,
                    "num_partition_sectors": count,
                    "physical_partition_number": lun,
                    "start_sector": start,
                },
            )
        )
        self.require(rsp, "SHA-256 verification")
        text = " ".join(str(x) for x in (rsp.log or []))
        found = re.search(r"\b([0-9a-fA-F]{64})\b", text)
        if not found:
            raise IOError("Device digest response could not be parsed")
        return found[1].lower()

    def set_bootable(self, lun):
        self.require(
            self.fh.xmlsend(self.xml("setbootablestoragedrive", {"value": lun})),
            "set bootable storage drive",
        )

    def reset(self):
        self.require(self.fh.xmlsend(self.xml("power", {"value": "reset"})), "reset")

    def close(self):
        if self.cdc:
            try:
                self.cdc.close()
            except Exception:
                pass
