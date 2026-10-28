"""Integration services: override, time program, holiday.

Each service acts on every loaded gateway (there is normally one) through the
same runtime actions the entities use (runtime.AristonRuntimeData)."""
from __future__ import annotations

import time

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN
from .runtime import AristonRuntimeData

SERVICE_SET_OVERRIDE = "set_override"
SERVICE_CLEAR_OVERRIDE = "clear_override"
SERVICE_SET_TIME_PROGRAM = "set_time_program"
SERVICE_SET_HOLIDAY = "set_holiday"
SERVICE_CLEAR_HOLIDAY = "clear_holiday"

_HOLIDAY_SCHEMA = vol.Schema({vol.Required("until"): cv.datetime})

_HHMM = vol.Match(r"^\d{1,2}:\d{2}$")
_PROFILE_SCHEMA = vol.Schema({
    vol.Required("days"): [vol.All(int, vol.Range(min=1, max=7))],
    vol.Required("windows"): [vol.All([_HHMM], vol.Length(min=2, max=2))],
})
_TPROG_SCHEMA = vol.Schema({
    vol.Required("id"): vol.All(int, vol.Range(min=0, max=99)),
    vol.Optional("comfort"): vol.All(vol.Coerce(float), vol.Range(min=5, max=35)),
    vol.Optional("eco"): vol.All(vol.Coerce(float), vol.Range(min=5, max=35)),
    vol.Optional("profiles"): [_PROFILE_SCHEMA],
})

_SET_SCHEMA = vol.Schema({
    vol.Optional("zone", default=1): vol.All(int, vol.Range(min=1, max=8)),
    vol.Required("temperature"): vol.All(vol.Coerce(float), vol.Range(min=5, max=35)),
    vol.Optional("duration", default={"hours": 2}): cv.time_period,
})
_CLEAR_SCHEMA = vol.Schema({
    vol.Optional("zone", default=1): vol.All(int, vol.Range(min=1, max=8)),
})


def _runtimes(hass: HomeAssistant) -> list[AristonRuntimeData]:
    runtimes = [entry.runtime_data
                for entry in hass.config_entries.async_entries(DOMAIN)
                if entry.state is ConfigEntryState.LOADED]
    if not runtimes:
        raise HomeAssistantError("no Ariston gateway is loaded")
    return runtimes


def async_register_services(hass: HomeAssistant) -> None:
    async def _set(call: ServiceCall) -> None:
        end = int(time.time() + call.data["duration"].total_seconds())
        for rt in _runtimes(hass):
            await rt.async_set_override(
                call.data["zone"], call.data["temperature"], end)

    async def _clear(call: ServiceCall) -> None:
        for rt in _runtimes(hass):
            await rt.async_clear_override(call.data["zone"])

    async def _set_tprog(call: ServiceCall) -> None:
        changes = {k: call.data.get(k) for k in ("comfort", "eco", "profiles")}
        if all(v is None for v in changes.values()):
            raise HomeAssistantError(
                "set_time_program needs at least one of comfort, eco, profiles")
        for rt in _runtimes(hass):
            await rt.async_set_time_program(call.data["id"], **changes)

    async def _set_holiday(call: ServiceCall) -> None:
        end = int(call.data["until"].timestamp())
        for rt in _runtimes(hass):
            await rt.async_set_holiday(end)

    async def _clear_holiday(call: ServiceCall) -> None:
        for rt in _runtimes(hass):
            await rt.async_clear_holiday()

    hass.services.async_register(DOMAIN, SERVICE_SET_OVERRIDE, _set, schema=_SET_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_OVERRIDE, _clear, schema=_CLEAR_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_TIME_PROGRAM, _set_tprog,
                                 schema=_TPROG_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_HOLIDAY, _set_holiday,
                                 schema=_HOLIDAY_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_HOLIDAY, _clear_holiday)
