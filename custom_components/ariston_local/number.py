"""Number entities for writable setpoints."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entities, menu
from .const import DOMAIN
from .entity import AristonEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    add_entities(AristonNumber(coordinator, spec) for spec in entities.NUMBERS)


class AristonNumber(AristonEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, spec: entities.NumberSpec):
        super().__init__(coordinator, spec.menu, spec.name)
        self._scale = menu.meta(spec.menu).get("scale")
        self._attr_native_min_value = spec.min
        self._attr_native_max_value = spec.max
        self._attr_native_step = spec.step
        if spec.unit == "°C":
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        else:
            self._attr_native_unit_of_measurement = spec.unit

    @property
    def native_value(self):
        raw = self._raw
        if raw is None:
            return None
        value, _ = menu.decode(self._menu, raw)
        return value

    async def async_set_native_value(self, value: float) -> None:
        await self._write(menu.to_raw(value, self._scale))
