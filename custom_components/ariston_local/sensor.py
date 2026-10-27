"""Sensor entities: read-only menu parameters + energy (Stat/cWh)."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import entities, menu, energy
from .const import DOMAIN, MANUFACTURER
from .entity import AristonEntity


@dataclass(frozen=True)
class EnergySpec:
    key: str
    name: str
    typ: int
    idx: int


# Confirmed against the app's energy screen (see docs/06): Typ 6 = heating,
# Typ 9 = DHW, values in Wh; idx 2 = this month (daily), idx 4 = trailing year.
ENERGY = [
    EnergySpec("heating_month", "Heating energy this month", energy.TYP_HEATING, energy.IDX_DAILY),
    EnergySpec("dhw_month", "DHW energy this month", energy.TYP_DHW, energy.IDX_DAILY),
    EnergySpec("heating_year", "Heating energy (trailing year)", energy.TYP_HEATING, energy.IDX_MONTHLY),
    EnergySpec("dhw_year", "DHW energy (trailing year)", energy.TYP_DHW, energy.IDX_MONTHLY),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["coordinator"]
    stats = store["stats"]
    tprog = store["tprog"]
    ents = [AristonSensor(coordinator, spec) for spec in entities.SENSORS]
    ents += [AristonEnergySensor(stats, entry, spec) for spec in ENERGY]
    ents += [AristonTProgSensor(tprog, entry, pid) for pid in tprog.prog_ids]
    add_entities(ents)


class AristonSensor(AristonEntity, SensorEntity):
    def __init__(self, coordinator, spec: entities.SensorSpec):
        super().__init__(coordinator, spec.menu, spec.name, spec.prefix)
        self._attr_native_unit_of_measurement = spec.unit
        if spec.device_class:
            self._attr_device_class = SensorDeviceClass(spec.device_class)
            self._attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self):
        raw = self._raw
        if raw is None:
            return None
        value, enum_label = menu.decode(self._menu, raw)
        # Numeric sensors (temperature, ...) must never report the "N/A" label.
        if getattr(self, "_attr_device_class", None) is not None:
            return value
        return enum_label if enum_label is not None else value


class AristonEnergySensor(CoordinatorEntity, SensorEntity):
    """Energy total in kWh, summed from a Stat/cWh (Typ, idx) series."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR

    def __init__(self, stats_coordinator, entry: ConfigEntry, spec: EnergySpec):
        super().__init__(stats_coordinator)
        self._spec = spec
        mac = entry.unique_id or entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_energy_{spec.key}"
        self._attr_name = spec.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        series = data.get((self._spec.typ, self._spec.idx))
        return None if series is None else energy.kwh(series)


class AristonTProgSensor(CoordinatorEntity, SensorEntity):
    """Read-only view of one heating time program (weekly schedule).

    State is the comfort/eco pair; the decoded day profiles are attributes."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, tprog_coordinator, entry: ConfigEntry, prog_id: int):
        super().__init__(tprog_coordinator)
        self._prog_id = prog_id
        mac = entry.unique_id or entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_tprog_{prog_id}"
        self._attr_name = f"Time program {prog_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")

    def _program(self):
        return (self.coordinator.data or {}).get(self._prog_id)

    @property
    def native_value(self):
        prog = self._program()
        if not prog:
            return None
        return f"comfort {prog['comfort']}°C / eco {prog['eco']}°C"

    @property
    def extra_state_attributes(self):
        prog = self._program()
        if not prog:
            return None
        return {
            "comfort": prog["comfort"],
            "eco": prog["eco"],
            "slot_minutes": prog["slot_minutes"],
            # one entry per day-group: which weekdays (1=Mon..7=Sun) and the
            # comfort windows (as [start, end] HH:MM) that apply to them
            "profiles": [
                {"days": p["days"], "comfort_windows": p["windows"]}
                for p in prog["profiles"]
            ],
        }
