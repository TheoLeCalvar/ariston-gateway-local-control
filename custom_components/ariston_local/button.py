"""Button entities: cancel holiday mode, or cancel a zone's temporary override.

These give one-tap actions in a standard card for the two things that otherwise
need a service call: clearing holiday (Func/SpecF) and clearing a dérogation
(Func/Der) on a zone.
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    ents: list[ButtonEntity] = [AristonClearHolidayButton(store, entry)]
    for zone in store["active_zones"]:
        ents.append(AristonClearOverrideButton(store, entry, zone))
    add_entities(ents)


class _AristonButton(ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, store, entry: ConfigEntry):
        self._store = store
        self._gateway = store["coordinator"].gateway
        mac = entry.unique_id or entry.data.get("mac", "unknown")
        self._mac = mac
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")


class AristonClearHolidayButton(_AristonButton):
    _attr_icon = "mdi:bag-suitcase-off"

    def __init__(self, store, entry: ConfigEntry):
        super().__init__(store, entry)
        self._attr_unique_id = f"{self._mac}_clear_holiday"
        self._attr_name = "Clear holiday"

    async def async_press(self) -> None:
        await self.hass.async_add_executor_job(self._gateway.clear_holiday)
        await self._store["holiday"].async_request_refresh()


class AristonClearOverrideButton(_AristonButton):
    _attr_icon = "mdi:thermostat-auto"

    def __init__(self, store, entry: ConfigEntry, zone: int):
        super().__init__(store, entry)
        self._zone = zone
        self._attr_unique_id = f"{self._mac}_clear_override_z{zone}"
        self._attr_name = f"Zone {zone} clear override"

    async def async_press(self) -> None:
        await self.hass.async_add_executor_job(self._gateway.clear_override, self._zone)
        await self._store["override"].async_request_refresh()
