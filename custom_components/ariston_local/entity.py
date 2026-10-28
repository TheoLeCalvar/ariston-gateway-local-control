"""Shared entity plumbing: device identity and the base entity classes."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity import DeviceInfo, Entity
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from . import menu
from .const import DOMAIN, MANUFACTURER
from .coordinator import AristonCoordinator


def entry_mac(entry: ConfigEntry) -> str:
    """The gateway MAC that keys this entry's device and unique ids."""
    return entry.unique_id or entry.data.get("mac", "unknown")


def identify(entity: Entity, entry: ConfigEntry, key: str, name: str) -> None:
    """Give an entity its unique id (`<mac>_<key>`), name and gateway device.

    Every entity of the integration hangs off the one gateway device. The
    unique-id scheme is load-bearing: changing a key orphans that entity's
    history for existing installs (see tests/test_integration.py)."""
    mac = entry_mac(entry)
    entity._attr_has_entity_name = True
    entity._attr_unique_id = f"{mac}_{key}"
    entity._attr_name = name
    entity._attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, mac)},
        manufacturer=MANUFACTURER,
        name=f"Ariston gateway {mac}",
    )


class AristonCoordinatorEntity(CoordinatorEntity):
    """Base for an entity fed by one of the entry's coordinators."""

    def __init__(self, coordinator: DataUpdateCoordinator, key: str, name: str):
        super().__init__(coordinator)
        identify(self, coordinator.config_entry, key, name)


class AristonEntity(AristonCoordinatorEntity):
    """Base: binds an entity to one menu code (read via U_<menu> or T_<menu>)."""

    coordinator: AristonCoordinator

    def __init__(self, coordinator: AristonCoordinator, menu_num: str, name: str,
                 prefix: str = "U_"):
        self._menu = menu_num
        self._code = menu.code_for(menu_num, prefix)
        super().__init__(coordinator, self._code, name)

    @property
    def _raw(self):
        """The raw device value for this entity's code, or None."""
        data = self.coordinator.data or {}
        return data.get(self._code)

    @property
    def available(self) -> bool:
        return super().available and self._raw is not None

    async def _write(self, raw: int) -> None:
        await self.coordinator.async_write(self._code, raw)
