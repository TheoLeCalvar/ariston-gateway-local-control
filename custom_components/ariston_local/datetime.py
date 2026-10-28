"""Datetime entity: set the holiday/absence end date from the UI.

Setting a value enables holiday mode until then (Func/SpecF); clearing is done
with the "Clear holiday" button. Renders as a date/time picker in a standard
Entities/Tile card, so no custom Lovelace card is needed.
"""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import SPECF_HOLIDAY_MODE
from .entity import AristonCoordinatorEntity
from .runtime import AristonConfigEntry, AristonRuntimeData


async def async_setup_entry(hass: HomeAssistant, entry: AristonConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    add_entities([AristonHolidayDateTime(entry.runtime_data)])


class AristonHolidayDateTime(AristonCoordinatorEntity, DateTimeEntity):
    """Holiday end date/time. Reads the active holiday end; setting it enables
    holiday mode until the chosen moment."""

    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, runtime: AristonRuntimeData):
        super().__init__(runtime.holiday, "holiday_until", "Holiday until")
        self._runtime = runtime

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data or {}
        if data.get("mode") != SPECF_HOLIDAY_MODE or not data.get("end"):
            return None
        return datetime.fromtimestamp(data["end"], tz=timezone.utc)

    async def async_set_value(self, value: datetime) -> None:
        await self._runtime.async_set_holiday(int(value.timestamp()))
