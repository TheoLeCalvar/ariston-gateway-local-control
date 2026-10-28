"""Per-zone climate entity.

Current temperature and active setpoint come from the live T_ reads confirmed in
docs/05 (zone 1: T_4.3.0 room temp, T_4.3.1 active setpoint). Setting a target
issues a timed hold (dérogation via Func/Der) that the boiler auto-reverts; the
HVAC mode maps to the zone mode (U_0.<zone>: Manuel / Chauffage programmé).
"""
from __future__ import annotations

import time

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entities, menu
from .const import SPECF_HOLIDAY_MODE
from .entity import AristonCoordinatorEntity
from .runtime import AristonConfigEntry, AristonRuntimeData

# Zone mode (U_0.<zone>) <-> HVAC mode. 3 = Chauffage programmé (follow schedule),
# 2 = Manuel. The device has no per-zone OFF (that is the global season mode).
_MODE_TO_HVAC = {3: HVACMode.AUTO, 2: HVACMode.HEAT}
_HVAC_TO_MODE = {HVACMode.AUTO: 3, HVACMode.HEAT: 2}


async def async_setup_entry(hass: HomeAssistant, entry: AristonConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    runtime = entry.runtime_data
    add_entities(AristonClimate(runtime, spec)
                 for spec in entities.climates(runtime.active_zones))


class AristonClimate(AristonCoordinatorEntity, ClimateEntity):
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    # HA >= 2024.x expects these on climate entities that only support the above.
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, runtime: AristonRuntimeData, spec: entities.ClimateSpec):
        super().__init__(runtime.coordinator, f"climate_zone{spec.zone}", spec.name)
        self._runtime = runtime
        self._spec = spec
        self._temp_code = f"T_{spec.temp_menu}"
        self._setpoint_code = f"T_{spec.setpoint_menu}"
        self._mode_code = f"U_{spec.mode_menu}"
        self._attr_min_temp = spec.min_temp
        self._attr_max_temp = spec.max_temp
        self._attr_target_temperature_step = spec.step

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Holds (Func/Der) and holiday (Func/SpecF) are polled separately but
        # change what this entity shows, so re-render when either refreshes.
        for other in (self._runtime.override, self._runtime.holiday):
            self.async_on_remove(
                other.async_add_listener(self._handle_coordinator_update))

    def _hold_temp(self):
        """The running hold's target for this zone, unless holiday overrides it."""
        holiday = self._runtime.holiday.data or {}
        if holiday.get("mode") == SPECF_HOLIDAY_MODE:
            return None
        info = (self._runtime.override.data or {}).get(self._spec.zone)
        if info and info.get("enabled"):
            return info.get("temp")
        return None

    def _value(self, code: str, menu_num: str):
        raw = (self.coordinator.data or {}).get(code)
        if raw is None:
            return None
        value, _ = menu.decode(menu_num, raw)
        return value

    @property
    def current_temperature(self):
        return self._value(self._temp_code, self._spec.temp_menu)

    @property
    def target_temperature(self):
        # A hold's temperature is known the moment Func/Der reports it; the
        # active-setpoint read (T_) can lag it by a poll.
        hold = self._hold_temp()
        if hold is not None:
            return hold
        return self._value(self._setpoint_code, self._spec.setpoint_menu)

    @property
    def hvac_mode(self):
        raw = (self.coordinator.data or {}).get(self._mode_code)
        return _MODE_TO_HVAC.get(raw, HVACMode.HEAT)

    @property
    def hvac_action(self):
        current = self.current_temperature
        target = self.target_temperature
        if current is None or target is None:
            return None
        return HVACAction.HEATING if current < target else HVACAction.IDLE

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        raw = _HVAC_TO_MODE.get(hvac_mode)
        if raw is None:
            return
        await self.coordinator.async_write(self._mode_code, raw)
        # Returning to the schedule (AUTO = Chauffage programmé) cancels any
        # manual hold still running, so the zone actually follows the program.
        if hvac_mode == HVACMode.AUTO:
            await self._runtime.async_clear_override(self._spec.zone)

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        end = int(time.time() + self._runtime.override_hours * 3600)
        await self._runtime.async_set_override(self._spec.zone, float(temp), end)
