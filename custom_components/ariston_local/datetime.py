"""Datetime entity: set the holiday/absence end date from the UI.

Setting a value enables holiday mode until then (Func/SpecF); clearing is done
with the "Clear holiday" button. Renders as a date/time picker in a standard
Entities/Tile card, so no custom Lovelace card is needed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, SPECF_HOLIDAY_MODE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    add_entities([AristonHolidayDateTime(store["holiday"], entry)])


class AristonHolidayDateTime(CoordinatorEntity, DateTimeEntity):
    """Holiday end date/time. Reads the active holiday end; setting it enables
    holiday mode until the chosen moment."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, coordinator, entry: ConfigEntry):
        super().__init__(coordinator)
        mac = entry.unique_id or entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_holiday_until"
        self._attr_name = "Holiday until"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        if data.get("mode") != SPECF_HOLIDAY_MODE or not data.get("end"):
            return None
        return datetime.fromtimestamp(data["end"], tz=timezone.utc)

    async def async_set_value(self, value: datetime) -> None:
        end = int(value.timestamp())
        gw = self.coordinator.gateway
        rc = await self.hass.async_add_executor_job(gw.set_holiday, end)
        if rc not in (None, 200):
            _LOGGER.warning("set_holiday returned response.code=%s", rc)
        await self.coordinator.async_request_refresh()
