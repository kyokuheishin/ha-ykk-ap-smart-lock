"""YKKApSmartLock operation coordinator and registration workflow."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .ble import YKKApSmartLockBleClient, YKKApSmartLockConnectionError
from .const import (
    BASE_INFORMATION,
    BASE_MAIN,
    BASE_SETTINGS,
    CMD_ADV_DATA_KEY,
    CMD_EXIT_GENERAL_REGISTRATION,
    CMD_LOCK_REQUEST,
    CMD_REQUEST_GENERAL_LOCK_ID,
    CMD_REQUEST_GENERAL_SMARTPHONE_ID,
    CMD_SET_TIMESTAMP,
    CONF_ADDRESS,
    CONF_ADVERTISING_KEY,
    CONF_LOT_NUMBER,
    CONF_SERIAL_NUMBER,
    CONF_SMARTPHONE_ID,
    LOCKED,
    MANUFACTURER_ID,
)
from .protocol import (
    YKKApSmartLockFrame,
    YKKApSmartLockProtocolError,
    YKKApSmartLockResponseTimeout,
    decode_advertisement_state,
    decode_lock_identity,
    encode_lock_payload,
    encode_timestamp,
    ensure_response,
)

_LOGGER = logging.getLogger(__name__)


class YKKApSmartLockError(Exception):
    """Base class for user-visible YKKApSmartLock errors."""


class YKKApSmartLockNotRegistered(YKKApSmartLockError):
    """Raised when the lock has not received a smartphone slot yet."""


class YKKApSmartLockRegistrationError(YKKApSmartLockError):
    """Raised when the manual registration sequence cannot be completed."""


class YKKApSmartLockCommandError(YKKApSmartLockError):
    """Raised when an operation is rejected or returns an invalid state."""


def _parse_smartphone_id(frame: YKKApSmartLockFrame) -> int:
    """Extract the assigned smartphone ID from a registration response."""

    if not frame.payload or frame.payload[0] == 0:
        raise YKKApSmartLockRegistrationError(
            "the lock returned smartphoneId=0; make sure the official phone "
            "has opened ordinary-device registration mode"
        )
    return frame.payload[0]


def _identity_from_advertisement(
    hass: HomeAssistant, address: str
) -> tuple[str, int] | None:
    """Recover the five-character lot and serial from the cached advertisement.

    Home Assistant removes the two-byte company ID from manufacturer data. The
    APK's remaining layout is productCode, registrationMode, four-byte packed
    lock ID, and encrypted state data.
    """

    service_info = bluetooth.async_last_service_info(hass, address, connectable=True)
    if service_info is None:
        return None
    manufacturer_data = service_info.manufacturer_data.get(MANUFACTURER_ID)
    if manufacturer_data is None:
        return None
    data = bytes(manufacturer_data)
    if data.startswith(bytes((0x9D, 0x09))):
        data = data[2:]
    if len(data) < 6:
        return None

    packed_lock_id = int.from_bytes(data[2:6], "big")
    year = (packed_lock_id >> 25) & 0x7F
    month = (packed_lock_id >> 21) & 0x0F
    day = (packed_lock_id >> 16) & 0x1F
    serial_number = packed_lock_id & 0xFFFF
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    lot_number = f"{year:02d}{chr(64 + month)}{day:02d}"
    return lot_number, serial_number


async def async_register_general_device(
    hass: HomeAssistant,
    address: str,
    name: str,
    *,
    request_adv_key: bool = True,
    exit_registration: bool = True,
) -> dict[str, Any]:
    """Register a new ordinary BLE central in an already-managed lock.

    The official phone must already own/manage the lock and must have put the
    lock into ordinary smartphone registration mode before this is called.
    """

    result: dict[str, Any] = {}
    lot_number: str | None = None
    serial_number: int | None = None
    try:
        async with YKKApSmartLockBleClient(hass, address, name) as client:
            if request_adv_key:
                key_response = await client.command(BASE_MAIN, CMD_ADV_DATA_KEY)
                key_response = ensure_response(
                    key_response, BASE_MAIN, CMD_ADV_DATA_KEY
                )
                if len(key_response.payload) != 16:
                    raise YKKApSmartLockRegistrationError(
                        "advertising-key response has "
                        f"{len(key_response.payload)} bytes; expected 16"
                    )
                result[CONF_ADVERTISING_KEY] = key_response.payload.hex()

            lock_response = await client.command(
                BASE_SETTINGS, CMD_REQUEST_GENERAL_LOCK_ID
            )
            lock_response = ensure_response(
                lock_response, BASE_SETTINGS, CMD_REQUEST_GENERAL_LOCK_ID
            )
            # The APK's generic integer decoder makes the 0x52 response
            # model-dependent. Prefer the same packed lock ID that the APK
            # reads from advertising data, and retain the response as fallback.
            try:
                lot_number, serial_number = decode_lock_identity(lock_response.payload)
            except (YKKApSmartLockProtocolError, ValueError):
                _LOGGER.debug("Could not decode the 0x52 identity response directly")

            advertised_identity = _identity_from_advertisement(hass, address)
            if advertised_identity is not None:
                lot_number, serial_number = advertised_identity

            # The APK's 0x51 request has no payload; the lock allocates the next
            # ordinary-smartphone slot.
            smartphone_response = await client.command(
                BASE_SETTINGS,
                CMD_REQUEST_GENERAL_SMARTPHONE_ID,
            )
            smartphone_response = ensure_response(
                smartphone_response, BASE_SETTINGS, CMD_REQUEST_GENERAL_SMARTPHONE_ID
            )
            smartphone_id = _parse_smartphone_id(smartphone_response)

            # Some firmware returns the lot/serial alongside the smartphone ID.
            # Prefer it when it is decodable, but retain the 0x52 values as the
            # canonical fallback.
            if len(smartphone_response.payload) >= 11 and (
                lot_number is None or serial_number is None
            ):
                try:
                    lot_number, serial_number = decode_lock_identity(
                        smartphone_response.payload[2:]
                    )
                except (YKKApSmartLockProtocolError, ValueError):
                    _LOGGER.debug("Ignoring optional identity fields in 0x51 response")

            if lot_number is not None and len(lot_number) != 5:
                _LOGGER.debug("Ignoring non-five-character lot number from registration")
                lot_number = None
            if serial_number is not None and not 0 <= serial_number <= 0xFFFF:
                _LOGGER.debug("Ignoring out-of-range serial number from registration")
                serial_number = None
            if lot_number is None or serial_number is None:
                raise YKKApSmartLockRegistrationError(
                    "could not determine lot/serial; keep the lock advertising and "
                    "retry registration so Home Assistant can read its manufacturer "
                    "data"
                )
            if exit_registration:
                await client.command(
                    BASE_SETTINGS,
                    CMD_EXIT_GENERAL_REGISTRATION,
                )
    except (
        YKKApSmartLockConnectionError,
        YKKApSmartLockProtocolError,
        YKKApSmartLockResponseTimeout,
    ) as err:
        raise YKKApSmartLockRegistrationError(str(err)) from err

    result.update(
        {
            CONF_ADDRESS: address,
            CONF_SMARTPHONE_ID: smartphone_id,
            CONF_LOT_NUMBER: lot_number,
            CONF_SERIAL_NUMBER: serial_number,
        }
    )
    return result


class YKKApSmartLockCoordinator:
    """Serialize operations and retain the lock state returned by the device."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        on_update: Callable[[], None],
    ) -> None:
        self.hass = hass
        self.entry = entry
        self._on_update = on_update
        self._operation_lock = asyncio.Lock()
        self._data = dict(entry.data)
        self.lock_state: int | None = None
        self.last_command: str | None = None
        self.last_error: str | None = None
        self.registered_at: str | None = None

    @property
    def address(self) -> str:
        """Return the configured BLE address."""

        return str(self._data[CONF_ADDRESS])

    @property
    def name(self) -> str:
        """Return a friendly lock name."""

        return self.entry.title

    @property
    def smartphone_id(self) -> int:
        """Return the assigned ordinary smartphone ID."""

        return int(self._data.get(CONF_SMARTPHONE_ID, 0))

    @property
    def lot_number(self) -> str:
        """Return the lock lot number."""

        return str(self._data.get(CONF_LOT_NUMBER, ""))

    @property
    def serial_number(self) -> int:
        """Return the lock serial number."""

        return int(self._data.get(CONF_SERIAL_NUMBER, 0))

    @property
    def is_registered(self) -> bool:
        """Return whether enough identity data exists for a lock request."""

        return bool(
            1 <= self.smartphone_id <= 0xFF
            and len(self.lot_number) == 5
            and 0 <= self.serial_number <= 0xFFFF
        )

    @property
    def advertising_key(self) -> str | None:
        """Return the hex-encoded advertising key, if captured."""

        return self._data.get(CONF_ADVERTISING_KEY)

    def extra_state_attributes(self) -> dict[str, Any]:
        """Return safe diagnostics for the entity state attributes."""

        return {
            "smartphone_id": self.smartphone_id or None,
            "lot_number": self.lot_number or None,
            "serial_number": self.serial_number or None,
            "last_command": self.last_command,
            "last_error": self.last_error,
            "registered": self.is_registered,
            "advertising_key_available": bool(self.advertising_key),
        }

    async def async_register(
        self, *, request_adv_key: bool, exit_registration: bool
    ) -> None:
        """Re-run ordinary-device registration for an existing entry."""

        async with self._operation_lock:
            result = await async_register_general_device(
                self.hass,
                self.address,
                self.name,
                request_adv_key=request_adv_key,
                exit_registration=exit_registration,
            )
            self._save_identity(result)
            self.lock_state = None
            self.last_command = "register_device"
            self.last_error = None
            self._on_update()

    def process_bluetooth_update(
        self, service_info: bluetooth.BluetoothServiceInfoBleak
    ) -> None:
        """Update the state from a validated manufacturer advertisement."""

        if not self.advertising_key:
            return
        manufacturer_data = service_info.manufacturer_data.get(MANUFACTURER_ID)
        if manufacturer_data is None:
            return
        data = bytes(manufacturer_data)
        try:
            state = decode_advertisement_state(data, self.advertising_key)
        except YKKApSmartLockProtocolError as err:
            _LOGGER.debug(
                "Could not decode YKKApSmartLock advertisement: "
                "len=%d data=%s error=%s",
                len(data),
                data.hex(),
                err,
            )
            return
        if state == self.lock_state:
            return
        _LOGGER.debug(
            "YKKApSmartLock %s state changed from %s to %s from BLE advertisement",
            service_info.address,
            self.lock_state,
            state,
        )
        self.lock_state = state
        self._on_update()

    async def async_set_lock_state(self, desired_state: int) -> None:
        """Set the physical lock state using the ordinary lock request."""

        if not self.is_registered:
            raise YKKApSmartLockNotRegistered(
                "the YKKApSmartLock entry has no smartphoneId/lot/serial; run "
                "ykkap_smart_lock.register_device after opening registration mode"
            )

        payload = encode_lock_payload(
            desired_state,
            self.smartphone_id,
            self.lot_number,
            self.serial_number,
        )
        async with self._operation_lock:
            try:
                async with YKKApSmartLockBleClient(
                    self.hass, self.address, self.name
                ) as client:
                    await client.command(
                        BASE_INFORMATION,
                        CMD_SET_TIMESTAMP,
                        encode_timestamp(dt_util.now()),
                    )
                    response = await client.command(
                        BASE_MAIN, CMD_LOCK_REQUEST, payload
                    )
                response = ensure_response(response, BASE_MAIN, CMD_LOCK_REQUEST)
                if not response.payload or response.payload[0] != desired_state:
                    received = response.payload[0] if response.payload else None
                    raise YKKApSmartLockCommandError(
                        f"lock returned state {received!r}, expected {desired_state}"
                    )
            except (
                YKKApSmartLockConnectionError,
                YKKApSmartLockProtocolError,
            ) as err:
                self.last_error = str(err)
                self._on_update()
                raise YKKApSmartLockCommandError(str(err)) from err

            self.lock_state = desired_state
            self.last_command = "lock" if desired_state == LOCKED else "unlock"
            self.last_error = None
            self._on_update()

    def _save_identity(self, result: dict[str, Any]) -> None:
        """Persist the assigned slot and lock identity without any PIN."""

        updated = {
            **self._data,
            CONF_ADDRESS: result[CONF_ADDRESS],
            CONF_SMARTPHONE_ID: result[CONF_SMARTPHONE_ID],
            CONF_LOT_NUMBER: result[CONF_LOT_NUMBER],
            CONF_SERIAL_NUMBER: result[CONF_SERIAL_NUMBER],
        }
        if result.get(CONF_ADVERTISING_KEY):
            updated[CONF_ADVERTISING_KEY] = result[CONF_ADVERTISING_KEY]
        self._data = updated
        self.hass.config_entries.async_update_entry(self.entry, data=updated)
        self.registered_at = dt_util.now().isoformat()
