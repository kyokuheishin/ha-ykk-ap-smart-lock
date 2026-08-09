"""YKKApSmartLock application protocol helpers.

The values in this module come from the static APK analysis in
``analysis/bluetooth_protocol.md``.  The packet formats still need to be
validated against a real lock before this integration is used in production.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .const import (
    BASE_INFORMATION,
    BASE_MAIN,
    BASE_SETTINGS,
    CMD_LOCK_REQUEST,
)


class YKKApSmartLockProtocolError(Exception):
    """Raised when a packet is malformed or fails validation."""


class YKKApSmartLockResponseTimeout(YKKApSmartLockProtocolError):
    """Raised when the lock does not answer a command in time."""


@dataclass(frozen=True, slots=True)
class YKKApSmartLockFrame:
    """A decoded YKKApSmartLock frame."""

    direction: int
    command: int
    payload: bytes

    @property
    def base(self) -> int:
        """Return the command family."""

        return self.direction & 0x7F

    @property
    def is_response(self) -> bool:
        """Return whether this is a lock-to-central frame."""

        return self.direction < 0x80


def crc16(data: bytes) -> int:
    """Calculate the APK's CRC-16/CCITT variant."""

    value = 0xFFFF
    for byte in data:
        value ^= byte << 8
        for _ in range(8):
            if value & 0x8000:
                value = ((value << 1) ^ 0x1021) & 0xFFFF
            else:
                value = (value << 1) & 0xFFFF
    return (~value) & 0xFFFF


def build_frame(
    base: int, command: int, payload: bytes = b"", *, request: bool = True
) -> bytes:
    """Build a request or response frame."""

    if not 0 <= base <= 0x7F or not 0 <= command <= 0xFF:
        raise ValueError("base and command must fit in one byte")
    direction = base | (0x80 if request else 0)
    body = bytes((direction, command)) + payload
    checksum = crc16(body)
    return body + checksum.to_bytes(2, "big")


def parse_frame(packet: bytes) -> YKKApSmartLockFrame:
    """Decode and validate one complete frame."""

    if len(packet) < 4:
        raise YKKApSmartLockProtocolError(
            "YKKApSmartLock frame is shorter than four bytes"
        )

    body = packet[:-2]
    received_crc = int.from_bytes(packet[-2:], "big")
    calculated_crc = crc16(body)
    if received_crc != calculated_crc:
        raise YKKApSmartLockProtocolError(
            f"YKKApSmartLock CRC mismatch: received 0x{received_crc:04x}, "
            f"calculated 0x{calculated_crc:04x}"
        )

    return YKKApSmartLockFrame(packet[0], packet[1], packet[2:-2])


def encode_pin(pin: str) -> bytes:
    """Encode the six numeric PIN bytes used by commands 0x12/0x13."""

    if len(pin) != 6 or not pin.isdecimal() or any(int(char) > 9 for char in pin):
        raise ValueError("PIN must contain exactly six decimal digits")
    return bytes(int(char) for char in pin)


def encode_lock_payload(
    desired_state: int,
    smartphone_id: int,
    lot_number: str,
    serial_number: int,
) -> bytes:
    """Build the payload for the ordinary lock request."""

    if desired_state not in (1, 2):
        raise ValueError("lock state must be 1 (locked) or 2 (unlocked)")
    if not 1 <= smartphone_id <= 0xFF:
        raise ValueError("smartphone_id must be between 1 and 255")
    if len(lot_number) != 5:
        raise ValueError("lot_number must contain exactly five characters")
    if not 0 <= serial_number <= 0xFFFF:
        raise ValueError("serial_number must be between 0 and 65535")

    # The APK pads to four characters but does not truncate a larger 16-bit
    # serial number, so retain the full decimal value for those models.
    serial_text = str(serial_number).zfill(4)
    try:
        lot_bytes = lot_number.encode("ascii")
        serial_bytes = serial_text.encode("ascii")
    except UnicodeEncodeError as err:
        raise ValueError("lot_number must contain ASCII characters") from err
    return bytes((desired_state, smartphone_id)) + lot_bytes + serial_bytes


def encode_timestamp(value: datetime) -> bytes:
    """Build the twelve ASCII timestamp bytes used by command 0x02."""

    return value.strftime("%Y%m%d%H%M").encode("ascii")


def response_length(base: int, command: int) -> int | None:
    """Return a known response length, or None for a variable response."""

    known_lengths = {
        (BASE_MAIN, 0x02): 5,
        (BASE_MAIN, CMD_LOCK_REQUEST): 5,
        (BASE_MAIN, 0x04): 5,
        (BASE_MAIN, 0x10): 20,
        # Registration responses with verified fixed framing.
        (BASE_SETTINGS, 0x41): 15,
        (BASE_SETTINGS, 0x42): 14,
        (BASE_SETTINGS, 0x52): 13,
        (BASE_SETTINGS, 0x12): 11,
        (BASE_SETTINGS, 0x13): 5,
    }
    return known_lengths.get((base, command))


def decode_text_field(values: bytes) -> str:
    """Decode a fixed-width text field, tolerating numeric-byte responses."""

    if all(0x20 <= value <= 0x7E for value in values):
        return values.decode("ascii")
    if all(value <= 9 for value in values):
        return "".join(str(value) for value in values)
    return values.hex().upper()


def decode_serial_field(values: bytes) -> int:
    """Decode the four-byte serial field returned by registration commands."""

    if all(0x30 <= value <= 0x39 for value in values):
        return int(values.decode("ascii"))
    if all(value <= 9 for value in values):
        return int("".join(str(value) for value in values))
    return int.from_bytes(values, "big")


def decode_lock_identity(payload: bytes) -> tuple[str, int]:
    """Decode lot/serial data from a registration response."""

    if len(payload) < 9:
        raise YKKApSmartLockProtocolError(
            "registration response has "
            f"{len(payload)} payload bytes; expected at least 9"
        )
    return decode_text_field(payload[:5]), decode_serial_field(payload[5:9])


def ensure_response(
    frame: YKKApSmartLockFrame, base: int, command: int
) -> YKKApSmartLockFrame:
    """Validate the header of a response for a requested command."""

    if not frame.is_response or frame.base != base or frame.command != command:
        raise YKKApSmartLockProtocolError(
            f"unexpected response header: direction=0x{frame.direction:02x}, "
            f"command=0x{frame.command:02x}; expected base=0x{base:02x}, "
            f"command=0x{command:02x}"
        )
    return frame
