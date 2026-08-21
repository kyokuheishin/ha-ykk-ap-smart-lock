"""BLE transport for YKKApSmartLock."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_RESPONSE_TIMEOUT,
    NOTIFY_CHAR_UUID,
    SERVICE_UUID,
    WRITE_CHAR_UUID,
)
from .protocol import (
    YKKApSmartLockFrame,
    YKKApSmartLockProtocolError,
    YKKApSmartLockResponseTimeout,
    ensure_response,
    build_frame,
    parse_frame,
    response_length,
)

_LOGGER = logging.getLogger(__name__)
_MAX_RESPONSE_SIZE = 512
# After a matching command response whose payload is not yet acceptable, keep
# listening this long for a later frame (old-then-new lock state).
_ACCEPT_GRACE_SECONDS = 1.0


def take_next_frame(
    buffer: bytearray,
    *,
    expected: int | None,
    target_header: bytes,
) -> YKKApSmartLockFrame | None:
    """Remove one complete frame from *buffer* if a parseable frame is present.

    Returns None when more bytes are required. Leading bytes that cannot start a
    valid frame are discarded so concatenated notifications can be walked.
    """

    while True:
        if len(buffer) < 4:
            return None

        sizes: list[int] = []
        if expected is not None and len(buffer) >= expected:
            sizes.append(expected)
        for size in range(4, len(buffer) + 1):
            if size not in sizes:
                sizes.append(size)

        for size in sizes:
            try:
                frame = parse_frame(bytes(buffer[:size]))
            except YKKApSmartLockProtocolError:
                continue
            del buffer[:size]
            return frame

        if buffer.startswith(target_header) and (
            expected is None or len(buffer) < expected
        ):
            return None
        del buffer[0]


class YKKApSmartLockConnectionError(Exception):
    """Raised when the lock cannot be reached or its GATT layout is invalid."""


class YKKApSmartLockBleClient:
    """Short-lived GATT client for one YKKApSmartLock operation sequence."""

    def __init__(
        self,
        hass: HomeAssistant,
        address: str,
        name: str,
        *,
        response_timeout: float = DEFAULT_RESPONSE_TIMEOUT,
    ) -> None:
        self._hass = hass
        self._address = address
        self._name = name
        self._response_timeout = response_timeout
        self._client: Any | None = None
        self._notify_characteristic: Any | None = None
        self._write_characteristic: Any | None = None
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._rx_buffer = bytearray()
        self._notification_callback: Callable[..., None] | None = None

    async def __aenter__(self) -> YKKApSmartLockBleClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.disconnect()

    async def connect(self) -> None:
        """Connect through Home Assistant's shared Bluetooth manager."""

        ble_device = bluetooth.async_ble_device_from_address(
            self._hass, self._address, connectable=True
        )
        if ble_device is None:
            raise YKKApSmartLockConnectionError(
                f"{self._address} is not currently reachable through a connectable "
                "Home Assistant Bluetooth adapter"
            )

        try:
            self._client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self._name or self._address,
                max_attempts=4,
                pair=True,
                timeout=DEFAULT_CONNECTION_TIMEOUT,
            )
        except YKKApSmartLockConnectionError:
            raise
        except Exception as err:  # bleak backends expose different exception types
            raise YKKApSmartLockConnectionError(
                f"unable to connect to YKKApSmartLock at {self._address}: {err}"
            ) from err

        try:
            services = self._client.services
            service = services.get_service(SERVICE_UUID)
            if service is None:
                raise YKKApSmartLockConnectionError(
                    f"YKKApSmartLock service {SERVICE_UUID} was not found"
                )
            self._notify_characteristic = service.get_characteristic(NOTIFY_CHAR_UUID)
            self._write_characteristic = service.get_characteristic(WRITE_CHAR_UUID)
            if (
                self._notify_characteristic is None
                or self._write_characteristic is None
            ):
                raise YKKApSmartLockConnectionError(
                    "YKKApSmartLock notify/write characteristics were not found"
                )

            self._notification_callback = self._on_notification
            await self._client.start_notify(
                self._notify_characteristic, self._notification_callback
            )
        except YKKApSmartLockConnectionError:
            await self.disconnect()
            raise
        except Exception as err:
            await self.disconnect()
            raise YKKApSmartLockConnectionError(
                f"unable to initialize YKKApSmartLock services at {self._address}: "
                f"{err}"
            ) from err

    async def disconnect(self) -> None:
        """Stop notifications and release the BLE connection."""

        client = self._client
        try:
            if client is not None:
                if (
                    self._notify_characteristic is not None
                    and self._notification_callback
                ):
                    try:
                        await client.stop_notify(self._notify_characteristic)
                    except Exception:  # the connection may already have disappeared
                        _LOGGER.debug(
                            "Unable to stop YKKApSmartLock notifications",
                            exc_info=True,
                        )
                try:
                    if client.is_connected:
                        await client.disconnect()
                except Exception:  # the connection may already have disappeared
                    _LOGGER.debug(
                        "Unable to disconnect YKKApSmartLock", exc_info=True
                    )
        finally:
            self._client = None
            self._notify_characteristic = None
            self._write_characteristic = None
            self._notification_callback = None
            self._discard_pending_notifications()

    def _on_notification(self, *args: Any) -> None:
        """Accept both Bleak 1-arg and 2-arg notification callbacks."""

        if not args:
            return
        try:
            packet = bytes(args[-1])
        except (TypeError, ValueError):
            _LOGGER.debug(
                "Ignoring non-bytes YKKApSmartLock notification: %r", args[-1]
            )
            return
        _LOGGER.debug("YKKApSmartLock RX len=%d data=%s", len(packet), packet.hex())
        self._responses.put_nowait(packet)

    def _discard_pending_notifications(self) -> None:
        """Drop stale notifications so a later command cannot adopt them."""

        rx_buffer = getattr(self, "_rx_buffer", None)
        if rx_buffer is not None:
            rx_buffer.clear()
        responses = getattr(self, "_responses", None)
        if responses is None:
            return
        while True:
            try:
                responses.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def command(
        self,
        base: int,
        command: int,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
        accept: Callable[[YKKApSmartLockFrame], bool] | None = None,
    ) -> YKKApSmartLockFrame:
        """Write a command and wait for its matching validated response."""

        if self._client is None or self._write_characteristic is None:
            raise YKKApSmartLockConnectionError(
                "YKKApSmartLock BLE client is not connected"
            )

        packet = build_frame(base, command, payload)
        properties = set(getattr(self._write_characteristic, "properties", ()))
        # YKKApSmartLock firmware variants may expose write-with-response or only
        # write-without-response.  Select the mode from the discovered GATT
        # characteristic instead of retrying a potentially destructive write.
        write_with_response = "write" in properties or not properties
        # Unsolicited 0x03/0x04 status frames can sit in the queue from connect
        # or the previous command.  Drop them before this write so lock/unlock
        # cannot adopt the pre-command state as the operation result.
        self._discard_pending_notifications()
        try:
            await self._client.write_gatt_char(
                self._write_characteristic,
                packet,
                response=write_with_response,
            )
        except YKKApSmartLockConnectionError:
            raise
        except Exception as err:
            raise YKKApSmartLockConnectionError(
                f"unable to write YKKApSmartLock command 0x{command:02x}: {err}"
            ) from err
        return await self._wait_for_response(
            base,
            command,
            timeout or self._response_timeout,
            accept=accept,
        )

    def _consider_frame(
        self,
        frame: YKKApSmartLockFrame,
        base: int,
        command: int,
        accept: Callable[[YKKApSmartLockFrame], bool] | None,
        fallback: YKKApSmartLockFrame | None,
    ) -> tuple[YKKApSmartLockFrame | None, YKKApSmartLockFrame | None]:
        """Return an accepted matching frame, or an updated fallback."""

        if not (frame.is_response and frame.base == base and frame.command == command):
            _LOGGER.debug(
                "Ignoring unrelated YKKApSmartLock notification: "
                "direction=0x%02x command=0x%02x",
                frame.direction,
                frame.command,
            )
            return None, fallback
        frame = ensure_response(frame, base, command)
        if accept is None or accept(frame):
            return frame, fallback
        _LOGGER.debug(
            "Deferring non-matching YKKApSmartLock payload for command 0x%02x: %s",
            command,
            frame.payload.hex(),
        )
        return None, frame

    async def _wait_for_response(
        self,
        base: int,
        command: int,
        timeout: float,
        accept: Callable[[YKKApSmartLockFrame], bool] | None = None,
    ) -> YKKApSmartLockFrame:
        """Collect notifications until a valid matching frame arrives."""

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        expected = response_length(base, command)
        target_header = bytes((base, command))
        fallback: YKKApSmartLockFrame | None = None
        fallback_deadline: float | None = None

        def _timeout_error() -> YKKApSmartLockResponseTimeout:
            return YKKApSmartLockResponseTimeout(
                f"no response for YKKApSmartLock command 0x{command:02x}"
            )

        while True:
            accepted, fallback = self._consume_buffer(
                base, command, expected, target_header, accept, fallback
            )
            if accepted is not None:
                return accepted
            if fallback is not None and fallback_deadline is None:
                fallback_deadline = loop.time() + _ACCEPT_GRACE_SECONDS

            now = loop.time()
            remaining = deadline - now
            if fallback is not None and fallback_deadline is not None:
                remaining = min(remaining, fallback_deadline - now)
            if remaining <= 0:
                if fallback is not None:
                    return fallback
                raise _timeout_error()

            try:
                notification = await asyncio.wait_for(self._responses.get(), remaining)
            except TimeoutError as err:
                if fallback is not None:
                    return fallback
                raise _timeout_error() from err

            if len(notification) > _MAX_RESPONSE_SIZE:
                raise YKKApSmartLockProtocolError(
                    f"YKKApSmartLock notification has {len(notification)} bytes; "
                    f"maximum is {_MAX_RESPONSE_SIZE}"
                )

            # A notification can be a complete unrelated frame while a target
            # response is already split across earlier notifications.  Validate
            # it independently before adding anything to the target partial.
            if len(notification) >= 4:
                try:
                    standalone = parse_frame(notification)
                except YKKApSmartLockProtocolError:
                    standalone = None
                else:
                    if standalone is not None:
                        accepted, fallback = self._consider_frame(
                            standalone, base, command, accept, fallback
                        )
                        if accepted is not None:
                            return accepted
                        if fallback is not None and fallback_deadline is None:
                            fallback_deadline = loop.time() + _ACCEPT_GRACE_SECONDS
                        continue

            if (
                not self._rx_buffer
                and 2 <= len(notification) < 4
                and not notification.startswith(target_header)
            ):
                _LOGGER.debug(
                    "Ignoring non-matching YKKApSmartLock notification header: "
                    "data=%s",
                    notification.hex(),
                )
                continue

            if len(self._rx_buffer) + len(notification) > _MAX_RESPONSE_SIZE:
                raise YKKApSmartLockProtocolError(
                    f"YKKApSmartLock response exceeded {_MAX_RESPONSE_SIZE} bytes"
                )
            self._rx_buffer.extend(notification)

    def _consume_buffer(
        self,
        base: int,
        command: int,
        expected: int | None,
        target_header: bytes,
        accept: Callable[[YKKApSmartLockFrame], bool] | None,
        fallback: YKKApSmartLockFrame | None,
    ) -> tuple[YKKApSmartLockFrame | None, YKKApSmartLockFrame | None]:
        """Walk buffered bytes and return an accepted frame if one is complete."""

        while True:
            frame = take_next_frame(
                self._rx_buffer, expected=expected, target_header=target_header
            )
            if frame is None:
                return None, fallback
            accepted, fallback = self._consider_frame(
                frame, base, command, accept, fallback
            )
            if accepted is not None:
                return accepted, fallback
