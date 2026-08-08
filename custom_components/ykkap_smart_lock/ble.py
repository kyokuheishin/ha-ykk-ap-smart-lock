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
        self._notification_callback: Callable[[Any, bytearray], None] | None = None

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
                timeout=DEFAULT_CONNECTION_TIMEOUT,
            )
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

            def _notification_handler(characteristic: Any, data: bytearray) -> None:
                del characteristic
                self._responses.put_nowait(bytes(data))

            self._notification_callback = _notification_handler
            await self._client.start_notify(
                self._notify_characteristic, self._notification_callback
            )
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Stop notifications and release the BLE connection."""

        if self._client is None:
            return
        try:
            if self._notify_characteristic is not None and self._notification_callback:
                try:
                    await self._client.stop_notify(self._notify_characteristic)
                except Exception:  # the connection may already have disappeared
                    _LOGGER.debug(
                        "Unable to stop YKKApSmartLock notifications", exc_info=True
                    )
            if self._client.is_connected:
                await self._client.disconnect()
        finally:
            self._client = None
            self._notify_characteristic = None
            self._write_characteristic = None
            self._notification_callback = None

    async def command(
        self,
        base: int,
        command: int,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
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
        await self._client.write_gatt_char(
            self._write_characteristic,
            packet,
            response=write_with_response,
        )
        return await self._wait_for_response(
            base, command, timeout or self._response_timeout
        )

    async def _wait_for_response(
        self, base: int, command: int, timeout: float
    ) -> YKKApSmartLockFrame:
        """Collect notifications until a valid matching frame arrives."""

        deadline = asyncio.get_running_loop().time() + timeout
        expected = response_length(base, command)
        partial = bytearray()

        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise YKKApSmartLockResponseTimeout(
                    f"no response for YKKApSmartLock command 0x{command:02x}"
                )
            try:
                notification = await asyncio.wait_for(self._responses.get(), remaining)
            except TimeoutError as err:
                raise YKKApSmartLockResponseTimeout(
                    f"no response for YKKApSmartLock command 0x{command:02x}"
                ) from err

            if expected is not None:
                partial.extend(notification)
                while len(partial) >= expected:
                    candidate = bytes(partial[:expected])
                    del partial[:expected]
                    try:
                        frame = parse_frame(candidate)
                    except YKKApSmartLockProtocolError as err:
                        raise YKKApSmartLockProtocolError(
                            "invalid YKKApSmartLock response for command "
                            f"0x{command:02x}: {err}"
                        ) from err
                    if (
                        frame.base == base
                        and frame.command == command
                        and frame.is_response
                    ):
                        return frame
                continue

            partial.extend(notification)
            try:
                frame = parse_frame(bytes(partial))
            except YKKApSmartLockProtocolError as err:
                # A short notification may be one fragment of a larger packet.
                # For a complete but unrelated/bad notification, discard it and
                # continue waiting so passive events cannot break a command.
                if len(partial) < 4 or "shorter" in str(err):
                    continue
                _LOGGER.debug(
                    "Ignoring non-matching YKKApSmartLock notification: %s", err
                )
                partial.clear()
                continue

            partial.clear()
            if frame.base == base and frame.command == command and frame.is_response:
                return ensure_response(frame, base, command)
