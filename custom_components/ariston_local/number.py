"""Number entities for writable setpoints."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode, RestoreNumber
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entities, menu
from .const import DEFAULT_CLIMATE_HOLD_HOURS, DOMAIN, MANUFACTURER
from .entity import AristonEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["coordinator"]
    active = store["active_zones"]
    ents: list[NumberEntity] = [AristonNumber(coordinator, spec)
                                for spec in entities.numbers(active)]
    ents.append(AristonOverrideDurationNumber(store, entry))
    add_entities(ents)


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


class AristonOverrideDurationNumber(RestoreNumber):
    """How long a climate-card temperature change holds before auto-reverting.

    Not a device parameter: it's an HA-side setting the climate entity reads when
    it issues a dérogation (Func/Der). Restored across restarts and stored on the
    entry so the climate can read it synchronously."""

    _attr_has_entity_name = True
    _attr_native_min_value = 1
    _attr_native_max_value = 72
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "h"
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-sand"

    def __init__(self, store, entry: ConfigEntry):
        self._store = store
        self._value = float(DEFAULT_CLIMATE_HOLD_HOURS)
        store["override_hours"] = self._value
        mac = entry.unique_id or entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_override_hours"
        self._attr_name = "Override duration"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self._value = float(last.native_value)
            self._store["override_hours"] = self._value

    @property
    def native_value(self) -> float:
        return self._value

    async def async_set_native_value(self, value: float) -> None:
        self._value = float(value)
        self._store["override_hours"] = self._value
        self.async_write_ha_state()
