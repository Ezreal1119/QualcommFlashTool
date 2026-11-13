"""Bounded USB transfers with packet-safe reads and no raw-write retries."""

import usb.core


class CheckedUSB:
    def __init__(self, cdc, activity):
        self.cdc = cdc
        self.activity = activity
        self.pending = bytearray()

    def read(self, length=None, timeout=-1):
        length = self.cdc.maxsize if length is None else length
        if length <= 0:
            raise ValueError("USB read length must be positive")
        if self.pending:
            data = bytes(self.pending[:length])
            del self.pending[:length]
            return data
        # A Sahara header can be shorter than a Firehose packet already queued
        # on this endpoint. Receive a complete packet and retain the excess.
        packet_size = self.cdc.EP_IN.wMaxPacketSize
        size = ((length + packet_size - 1) // packet_size) * packet_size
        timeout = self.cdc.timeout if timeout in (-1, None) else max(50, timeout)
        try:
            data = bytes(self.cdc.EP_IN.read(size, timeout=timeout))
        except usb.core.USBTimeoutError:
            return b""
        except usb.core.USBError as exc:
            raise IOError(f"USB read failed: {exc}") from exc
        if data:
            self.activity()
        self.pending.extend(data[length:])
        return data[:length]

    def write(self, data, *args, **kwargs):
        data = data.encode() if isinstance(data, str) else data
        # Preserve zero-length protocol transfers. Never retry a failed write:
        # the device may already have accepted some or all of its contents.
        packets = (
            (data[start : start + 1048576] for start in range(0, len(data), 1048576))
            if data
            else (b"",)
        )
        for packet in packets:
            try:
                count = self.cdc.EP_OUT.write(packet, timeout=1500)
            except usb.core.USBError as exc:
                raise IOError(
                    f"USB write failed ({len(packet)} bytes); completion unknown; no retry: {exc}"
                ) from exc
            if count != len(packet):
                raise IOError("Short USB write; no retry was attempted")
            self.activity()
        return True
