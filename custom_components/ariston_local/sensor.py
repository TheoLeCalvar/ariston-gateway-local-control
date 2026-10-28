"""Sensor entities: read-only menu parameters + energy (Stat/cWh)."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData

from . import entities, menu, energy, tprog
from .const import TPROG_IDS
from .entity import AristonCoordinatorEntity, AristonEntity
from .runtime import AristonConfigEntry

_LOGGER = logging.getLogger(__name__)


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


# Typ 6 = heating, Typ 9 = DHW, values in Wh (see energy.py for the windows).
#
# These are the device's own rolling windows of *completed* periods (recent
# days up to yesterday, the 13 months up to last month): handy read-outs, but
# their sums go down as old periods roll off, so they're diagnostics, not
# Energy-dashboard sources. (Keys keep their original "month"/"year" names so
# existing entities keep their ids and history.)
ENERGY = [
    EnergySpec("heating_month", "Heating energy (recent days)", energy.TYP_HEATING, energy.IDX_DAILY),
    EnergySpec("dhw_month", "DHW energy (recent days)", energy.TYP_DHW, energy.IDX_DAILY),
    EnergySpec("heating_year", "Heating energy (last 13 months)", energy.TYP_HEATING, energy.IDX_MONTHLY),
    EnergySpec("dhw_year", "DHW energy (last 13 months)", energy.TYP_DHW, energy.IDX_MONTHLY),
]

# Monotonic lifetime counters (total_increasing) for the HA Energy dashboard,
# built from the 2-hour-block window, the only series that includes today
# (see energy.fold_blocks). Counting starts when the entity is first added.
ENERGY_TOTALS = [
    EnergyTotalSpec("heating_total", "Heating energy total", energy.TYP_HEATING),
    EnergyTotalSpec("dhw_total", "DHW energy total", energy.TYP_DHW),
]


async def async_setup_entry(hass: HomeAssistant, entry: AristonConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    runtime = entry.runtime_data
    ents: list[SensorEntity] = [
        AristonSensor(runtime.coordinator, spec)
        for spec in entities.sensors(runtime.active_zones)]
    ents += [AristonEnergySensor(runtime.stats, spec) for spec in ENERGY]
    ents += [AristonEnergyTotalSensor(runtime.stats, spec) for spec in ENERGY_TOTALS]
    ents += [AristonTProgSensor(runtime.tprog, pid) for pid in TPROG_IDS]
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


class AristonEnergySensor(AristonCoordinatorEntity, SensorEntity):
    """Windowed energy figure in kWh, summed from a Stat/cWh (Typ, idx) series.

    Diagnostic read-out only. The window is not an ever-increasing total (the
    trailing-year sum drops as old months roll off), so it is deliberately not a
    `total_increasing` statistic and is not offered to the Energy dashboard —
    use the lifetime totals for that."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:fire"

    def __init__(self, stats_coordinator, spec: EnergySpec):
        super().__init__(stats_coordinator, f"energy_{spec.key}", spec.name)
        self._spec = spec

    @property
    def native_value(self):
        data = self.coordinator.data or {}
        series = data.get((self._spec.typ, self._spec.idx))
        return None if series is None else energy.kwh(series)


@dataclass
class _TotalStoredData(ExtraStoredData):
    """Persisted state of a lifetime counter across restarts."""

    total: float
    blocks: list[int] | None  # last idx-1 window folded in
    block: int | None         # block number of that window's newest entry

    def as_dict(self):
        return {"total": self.total, "blocks": self.blocks, "block": self.block}


# Consecutive reads that fit no alignment before the window is re-baselined
# (with a 10-min stats poll, about an hour). Guards against counting forever
# nothing if the window ever stops lining up, at the cost of that hour.
_MAX_UNALIGNED = 6


class AristonEnergyTotalSensor(AristonCoordinatorEntity, RestoreSensor):
    """Monotonic lifetime energy total (kWh) for the HA Energy dashboard.

    The gateway reports no lifetime odometer, so each new read of the rolling
    2-hour-block window is lined up with the previous one and only what grew
    or is new is added (see `energy.fold_blocks`). The total and the last
    window persist across restarts; a restart shorter than the window (48 h)
    loses nothing. Counting starts when the entity is first added."""

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_icon = "mdi:fire"

    def __init__(self, stats_coordinator, spec: EnergyTotalSpec):
        super().__init__(stats_coordinator, f"energy_{spec.key}", spec.name)
        self._spec = spec
        self._total = 0.0
        self._blocks: list[int] | None = None
        self._block: int | None = None
        self._unaligned = 0
        self._folded = None  # the coordinator data last folded in

    @property
    def native_value(self):
        return round(self._total, 3)

    @property
    def extra_restore_state_data(self) -> ExtraStoredData:
        return _TotalStoredData(self._total, self._blocks, self._block)

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        stored = await self.async_get_last_extra_data()
        if stored is not None:
            d = stored.as_dict()
            self._total = float(d.get("total") or 0.0)
            # (state saved by 0.13-0.15 has no window: it re-baselines)
            self._blocks = d.get("blocks")
            self._block = d.get("block")
        # fold in whatever the coordinator already has, then publish
        self._ingest()
        self.async_write_ha_state()

    def _ingest(self) -> None:
        data = self.coordinator.data
        # Listeners also fire after a failed poll, with the old data: only
        # fold in a read we haven't seen yet.
        if not self.coordinator.last_update_success or data is self._folded:
            return
        self._folded = data
        new = (data or {}).get((self._spec.typ, energy.IDX_BLOCKS))
        if not new:
            return
        now_block = energy.block_number(dt_util.now())
        if self._blocks is None or self._block is None or len(self._blocks) != len(new):
            # first read (or the window changed shape): a baseline, adds nothing
            self._blocks, self._block = list(new), now_block
            return
        added, shift = energy.fold_blocks(self._blocks, new, now_block - self._block)
        if shift is None:
            self._unaligned += 1
            if self._unaligned < _MAX_UNALIGNED:
                _LOGGER.debug("%s: read doesn't line up with the last one; "
                              "skipped", self.entity_id)
                return
            _LOGGER.warning("%s: energy window stopped lining up; re-baselining "
                            "(up to an hour of usage not counted)", self.entity_id)
            added, shift = 0, now_block - self._block
        self._unaligned = 0
        self._total += added / 1000.0
        self._blocks = list(new)
        self._block += shift

    def _handle_coordinator_update(self) -> None:
        self._ingest()
        super()._handle_coordinator_update()


class AristonTProgSensor(AristonCoordinatorEntity, SensorEntity):
    """Read-only view of one heating time program (weekly schedule).

    State is the comfort/eco pair; the decoded day profiles are attributes."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, tprog_coordinator, prog_id: int):
        super().__init__(tprog_coordinator, f"tprog_{prog_id}", f"Time program {prog_id}")
        self._prog_id = prog_id

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
