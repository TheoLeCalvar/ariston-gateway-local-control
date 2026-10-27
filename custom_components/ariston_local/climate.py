"""Per-zone climate entity.

Current temperature and active setpoint come from the live T_ reads confirmed in
docs/05 (zone 1: T_4.3.0 room temp, T_4.3.1 active setpoint). Setting a target
issues a timed hold (dérogation via Func/Der) that the boiler auto-reverts; the
HVAC mode maps to the zone mode (U_0.<zone>: Manuel / Chauffage programmé).
"""
from __future__ import annotations

import logging
import time

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import entities, menu
from .const import DEFAULT_CLIMATE_HOLD_HOURS, DOMAIN, MANUFACTURER

_LOGGER = logging.getLogger(__name__)

# Zone mode (U_0.<zone>) <-> HVAC mode. 3 = Chauffage programmé (follow schedule),
# 2 = Manuel. The device has no per-zone OFF (that is the global season mode).
_MODE_TO_HVAC = {3: HVACMode.AUTO, 2: HVACMode.HEAT}
_HVAC_TO_MODE = {HVACMode.AUTO: 3, HVACMode.HEAT: 2}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["coordinator"]
    override = store.get("override")
    add_entities(
        AristonClimate(coordinator, override, spec) for spec in entities.CLIMATES)


class AristonClimate(CoordinatorEntity, ClimateEntity):
    _attr_has_entity_name = True
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_hvac_modes = [HVACMode.AUTO, HVACMode.HEAT]
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE
    # HA >= 2024.x expects these on climate entities that only support the above.
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(self, coordinator, override_coordinator, spec: entities.ClimateSpec):
        super().__init__(coordinator)
        self._spec = spec
        self._override = override_coordinator
        self._temp_code = f"T_{spec.temp_menu}"
        self._setpoint_code = f"T_{spec.setpoint_menu}"
        self._mode_code = f"U_{spec.mode_menu}"
        mac = coordinator.entry.unique_id or coordinator.entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_climate_zone{spec.zone}"
        self._attr_name = spec.name
        self._attr_min_temp = spec.min_temp
        self._attr_max_temp = spec.max_temp
        self._attr_target_temperature_step = spec.step
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")

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

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        end = int(time.time() + DEFAULT_CLIMATE_HOLD_HOURS * 3600)
        rc = await self.hass.async_add_executor_job(
            self.coordinator.gateway.set_override, self._spec.zone, float(temp), end)
        if rc not in (None, 200):
            _LOGGER.warning("override zone %s -> %s°C returned response.code=%s",
                            self._spec.zone, temp, rc)
        await self.coordinator.async_request_refresh()
        if self._override is not None:
            await self._override.async_request_refresh()
