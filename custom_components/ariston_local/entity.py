"""Shared base entity for Ariston local."""
from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import menu
from .const import DOMAIN, MANUFACTURER
from .coordinator import AristonCoordinator


class AristonEntity(CoordinatorEntity[AristonCoordinator]):
    """Base: binds an entity to one menu code (read via U_<menu>)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AristonCoordinator, menu_num: str, name: str,
                 prefix: str = "U_"):
        super().__init__(coordinator)
        self._menu = menu_num
        self._code = menu.code_for(menu_num, prefix)
        mac = coordinator.entry.unique_id or coordinator.entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_{self._code}"
        self._attr_name = name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}",
        )

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
