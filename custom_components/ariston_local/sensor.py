"""Sensor entities: read-only menu parameters + energy (Stat/cWh)."""
from __future__ import annotations

from dataclasses import dataclass

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import entities, menu, energy, tprog
from .const import DOMAIN, MANUFACTURER
from .entity import AristonEntity


@dataclass(frozen=True)
class EnergySpec:
    key: str
    name: str
    typ: int
    idx: int


@dataclass(frozen=True)
class EnergyTotalSpec:
    key: str
    name: str
    typ: int


# Confirmed against the app's energy screen (see docs/06): Typ 6 = heating,
# Typ 9 = DHW, values in Wh; idx 2 = this month (daily), idx 4 = trailing year.
#
# The month and trailing-year figures are *windowed* (the year window rolls, so
# its sum can decrease) — they make poor Energy-dashboard sources and are now
# plain diagnostic read-outs. The dashboard is fed by the lifetime TOTALS below.
ENERGY = [
    EnergySpec("heating_month", "Heating energy this month", energy.TYP_HEATING, energy.IDX_DAILY),
    EnergySpec("dhw_month", "DHW energy this month", energy.TYP_DHW, energy.IDX_DAILY),
    EnergySpec("heating_year", "Heating energy (trailing year)", energy.TYP_HEATING, energy.IDX_MONTHLY),
    EnergySpec("dhw_year", "DHW energy (trailing year)", energy.TYP_DHW, energy.IDX_MONTHLY),
]

# Monotonic lifetime counters (total_increasing) for the HA Energy dashboard.
# Built by folding the this-month accumulator into a stored running total that
# never decreases (see energy.fold_total). Counting starts at install.
ENERGY_TOTALS = [
    EnergyTotalSpec("heating_total", "Heating energy total", energy.TYP_HEATING),
    EnergyTotalSpec("dhw_total", "DHW energy total", energy.TYP_DHW),
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["coordinator"]
    stats = store["stats"]
    tprog = store["tprog"]
    active = store["active_zones"]
    ents = [AristonSensor(coordinator, spec) for spec in entities.sensors(active)]
    ents += [AristonEnergySensor(stats, entry, spec) for spec in ENERGY]
    ents += [AristonEnergyTotalSensor(stats, entry, spec) for spec in ENERGY_TOTALS]
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
    """Windowed energy figure in kWh, summed from a Stat/cWh (Typ, idx) series.

    Diagnostic read-out only. The window is not an ever-increasing total (the
    trailing-year sum drops as old months roll off), so it is deliberately not a
    `total_increasing` statistic and is not offered to the Energy dashboard —
    use the lifetime totals for that."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
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


@dataclass
class _TotalStoredData(ExtraStoredData):
    """Persisted state of a lifetime counter across restarts."""

    total: float
    last: float | None
    last_month: list | None  # [year, month]

    def as_dict(self):
        return {"total": self.total, "last": self.last,
                "last_month": self.last_month}


class AristonEnergyTotalSensor(CoordinatorEntity, RestoreSensor):
    """Monotonic lifetime energy total (kWh) for the HA Energy dashboard.

    The gateway reports no lifetime odometer, so this folds the this-month
    accumulator into a running total that only ever increases (see
    `energy.fold_total`) and persists it across restarts. Counting starts when
    the entity is first added."""

    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:counter"

    def __init__(self, stats_coordinator, entry: ConfigEntry, spec: EnergyTotalSpec):
        super().__init__(stats_coordinator)
        self._spec = spec
        mac = entry.unique_id or entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_energy_{spec.key}"
        self._attr_name = spec.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")
        self._total = 0.0
        self._last = None
        self._last_month = None

    @property
    def native_value(self):
        return round(self._total, 3)

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return _TotalStoredData(
            self._total, self._last,
            list(self._last_month) if self._last_month else None)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        stored = await self.async_get_last_extra_data()
        if stored is not None:
            d = stored.as_dict()
            self._total = float(d.get("total") or 0.0)
            self._last = d.get("last")
            lm = d.get("last_month")
            self._last_month = tuple(lm) if lm else None
        # fold in whatever the coordinator already has, then publish
        self._ingest()
        self.async_write_ha_state()

    def _current(self):
        series = (self.coordinator.data or {}).get(
            (self._spec.typ, energy.IDX_DAILY))
        return None if series is None else energy.kwh(series)

    def _ingest(self) -> None:
        now = dt_util.now()
        self._total, self._last, self._last_month = energy.fold_total(
            self._total, self._last, self._last_month,
            self._current(), (now.year, now.month))

    def _handle_coordinator_update(self) -> None:
        self._ingest()
        super()._handle_coordinator_update()


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
            # legible one-line-per-day-group summary, e.g.
            # "Mon–Fri: 06:30–08:30, 17:30–23:30"
            "schedule": tprog.describe_profiles(
                prog["profiles"], step=prog["slot_minutes"]),
            # one entry per day-group: which weekdays (1=Mon..7=Sun) and the
            # comfort windows (as [start, end] HH:MM) that apply to them
            "profiles": [
                {"days": p["days"], "comfort_windows": p["windows"]}
                for p in prog["profiles"]
            ],
        }
