"""Config flow for registering a YKKApSmartLock as an ordinary BLE device."""

from __future__ import annotations

import logging
import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_NAME
from homeassistant.helpers import config_validation as cv

from .const import CONF_ADDRESS, CONF_LOCK_NAME, DOMAIN
from .coordinator import (
    YKKApSmartLockRegistrationError,
    async_register_general_device,
)

_LOGGER = logging.getLogger(__name__)


def _normalise_address(value: str) -> str:
    """Normalize common BLE address spellings without rejecting platform UUIDs."""

    address = value.strip().upper()
    if not address or any(char.isspace() for char in address):
        raise ValueError("invalid Bluetooth address")

    if re.fullmatch(r"[0-9A-F]{2}(-[0-9A-F]{2}){5}", address):
        return address.replace("-", ":")
    if re.fullmatch(r"[0-9A-F]{2}(:[0-9A-F]{2}){5}", address):
        return address
    # Core Bluetooth on macOS and some remote adapters expose UUID-like
    # addresses instead of a six-octet MAC address.
    if re.fullmatch(
        r"[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}",
        address,
    ):
        return address
    raise ValueError("invalid Bluetooth address")


class YKKApSmartLockConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle manual and Bluetooth-discovered YKKApSmartLock setup."""

    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Accept an address, then ask the user to open registration mode."""

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                address = _normalise_address(user_input[CONF_ADDRESS])
            except ValueError:
                errors[CONF_ADDRESS] = "invalid_address"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                self._address = address
                self._name = user_input.get(CONF_NAME) or f"YKKApSmartLock {address}"
                return await self.async_step_register()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ADDRESS): cv.string,
                    vol.Optional(CONF_NAME, default="YKKApSmartLock"): cv.string,
                }
            ),
            errors=errors,
        )

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> config_entries.FlowResult:
        """Start setup after Home Assistant discovers the lock."""

        if not getattr(discovery_info, "connectable", True):
            return self.async_abort(reason="not_connectable")

        address = _normalise_address(discovery_info.address)
        await self.async_set_unique_id(address)
        self._abort_if_unique_id_configured()
        self._address = address
        self._name = discovery_info.name or f"YKKApSmartLock {address}"
        self.context["title_placeholders"] = {"name": self._name}
        return await self.async_step_register()

    async def async_step_register(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.FlowResult:
        """Wait for the user to put the lock into ordinary registration mode."""

        if self._address is None or self._name is None:
            return self.async_abort(reason="missing_device")

        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input["ready"]:
                errors["base"] = "not_ready"
            else:
                try:
                    result = await async_register_general_device(
                        self.hass,
                        self._address,
                        self._name,
                        request_adv_key=user_input["request_adv_key"],
                    )
                except YKKApSmartLockRegistrationError as err:
                    _LOGGER.warning("YKKApSmartLock registration failed: %s", err)
                    errors["base"] = "registration_failed"
                else:
                    return self.async_create_entry(
                        title=result.get(CONF_LOCK_NAME, self._name), data=result
                    )

        return self.async_show_form(
            step_id="register",
            data_schema=vol.Schema(
                {
                    vol.Required("ready", default=False): cv.boolean,
                    vol.Required("request_adv_key", default=True): cv.boolean,
                }
            ),
            errors=errors,
        )
