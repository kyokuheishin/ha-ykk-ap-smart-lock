"""Lock entity for YKKApSmartLock."""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, LOCKED, UNLOCKED
from .coordinator import (
    YKKApSmartLockCommandError,
    YKKApSmartLockCoordinator,
    YKKApSmartLockError,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: Any,
) -> None:
    """Set up the YKKApSmartLock lock entity."""

    coordinator: YKKApSmartLockCoordinator = hass.data[DOMAIN][entry.entry_id]
    entity = YKKApSmartLockLockEntity(coordinator)
    coordinator._on_update = entity.async_write_ha_state
    async_add_entities([entity])


class YKKApSmartLockLockEntity(LockEntity):
    """Expose the ordinary registered YKKApSmartLock as a HA lock."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: YKKApSmartLockCoordinator) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{DOMAIN}_{coordinator.address.lower()}"
        self._attr_name = "Lock"

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry information."""

        return DeviceInfo(
            identifiers={(DOMAIN, self._coordinator.address)},
            name=self._coordinator.name,
            manufacturer="YKKApSmartLock",
            model="BLE lock",
        )

    @property
    def available(self) -> bool:
        """Remain available for commands while the registered identity exists."""

        return self._coordinator.is_registered

    @property
    def is_locked(self) -> bool | None:
        """Return the last state confirmed by the lock."""

        if self._coordinator.lock_state == LOCKED:
            return True
        if self._coordinator.lock_state == UNLOCKED:
            return False
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose non-secret registration and operation diagnostics."""

        return self._coordinator.extra_state_attributes()

    async def async_lock(self, **kwargs: Any) -> None:
        """Lock the door."""

        del kwargs
        await self._coordinator.async_set_lock_state(LOCKED)

    async def async_unlock(self, **kwargs: Any) -> None:
        """Unlock the door."""

        del kwargs
        await self._coordinator.async_set_lock_state(UNLOCKED)

    async def async_register_device(self, service_call: ServiceCall) -> None:
        """Register this central after the user opens lock registration mode."""

        try:
            await self._coordinator.async_register(
                request_adv_key=service_call.data["request_adv_key"],
            )
        except YKKApSmartLockError as err:
            raise YKKApSmartLockCommandError(str(err)) from err
