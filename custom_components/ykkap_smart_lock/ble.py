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

            def _notification_handler(characteristic: Any, data: bytearray) -> None:
                del characteristic
                packet = bytes(data)
                _LOGGER.debug(
                    "YKKApSmartLock RX len=%d data=%s", len(packet), packet.hex()
                )
                self._responses.put_nowait(packet)

            self._notification_callback = _notification_handler
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
            base, command, timeout or self._response_timeout
        )

    async def _wait_for_response(
        self, base: int, command: int, timeout: float
    ) -> YKKApSmartLockFrame:
        """Collect notifications until a valid matching frame arrives."""

        deadline = asyncio.get_running_loop().time() + timeout
        expected = response_length(base, command)
        partial = bytearray()
        target_header = bytes((base, command))

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
                except YKKApSmartLockProtocolError as err:
                    standalone = None
                    standalone_error = err
                else:
                    standalone_error = None

                if standalone is not None:
                    if (
                        standalone.base == base
                        and standalone.command == command
                        and standalone.is_response
                    ):
                        return ensure_response(standalone, base, command)
                    _LOGGER.debug(
                        "Ignoring unrelated YKKApSmartLock notification: "
                        "direction=0x%02x command=0x%02x",
                        standalone.direction,
                        standalone.command,
                    )
                    continue
            else:
                standalone_error = None

            if not partial and len(notification) >= 2 and not notification.startswith(
                target_header
            ):
                _LOGGER.debug(
                    "Ignoring non-matching YKKApSmartLock notification header: "
                    "data=%s error=%s",
                    notification.hex(),
                    standalone_error,
                )
                continue

            if len(partial) + len(notification) > _MAX_RESPONSE_SIZE:
                raise YKKApSmartLockProtocolError(
                    f"YKKApSmartLock response exceeded {_MAX_RESPONSE_SIZE} bytes"
                )
            partial.extend(notification)

            if expected is not None:
                while len(partial) >= expected:
                    candidate = bytes(partial[:expected])
                    del partial[:expected]
                    try:
                        frame = parse_frame(candidate)
                        return ensure_response(frame, base, command)
                    except YKKApSmartLockProtocolError as err:
                        raise YKKApSmartLockProtocolError(
                            "invalid YKKApSmartLock response for command "
                            f"0x{command:02x}: {err}"
                        ) from err
                continue

            try:
                frame = parse_frame(bytes(partial))
            except YKKApSmartLockProtocolError as err:
                # A variable response may be split after four or more bytes;
                # retain it until CRC succeeds, timeout, or the 512-byte cap.
                if len(partial) >= _MAX_RESPONSE_SIZE:
                    raise YKKApSmartLockProtocolError(
                        f"YKKApSmartLock response reached {_MAX_RESPONSE_SIZE} "
                        "bytes without a valid frame"
                    ) from err
                _LOGGER.debug(
                    "Waiting for more YKKApSmartLock response data: %s", err
                )
                continue

            partial.clear()
            return ensure_response(frame, base, command)
