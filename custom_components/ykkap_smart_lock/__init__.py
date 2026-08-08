"""YKKApSmartLock Home Assistant integration."""

from __future__ import annotations

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, service
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN, SERVICE_REGISTER_DEVICE
from .coordinator import YKKApSmartLockCoordinator

PLATFORMS: list[Platform] = [Platform.LOCK]


async def _async_register_device(entity: object, service_call: ServiceCall) -> None:
    """Forward the entity action to the YKKApSmartLock lock entity."""

    await entity.async_register_device(service_call)  # type: ignore[attr-defined]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up integration-wide YKKApSmartLock services."""

    del config
    service.async_register_platform_entity_service(
        hass,
        DOMAIN,
        SERVICE_REGISTER_DEVICE,
        entity_domain="lock",
        schema={
            vol.Required("request_adv_key", default=True): cv.boolean,
            vol.Required("exit_registration", default=True): cv.boolean,
        },
        func=_async_register_device,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up YKKApSmartLock from a config entry."""

    coordinator = YKKApSmartLockCoordinator(hass, entry, lambda: None)
    # The lock entity replaces this callback after it is constructed.  Keeping
    # the coordinator in hass.data makes the platform reload-safe and also
    # gives future diagnostic entities a single operation owner.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a YKKApSmartLock config entry."""

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unloaded
