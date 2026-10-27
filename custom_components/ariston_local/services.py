"""Services: temporary override set / clear."""
from __future__ import annotations

import functools
import logging
import time

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_SET_OVERRIDE = "set_override"
SERVICE_CLEAR_OVERRIDE = "clear_override"
SERVICE_SET_TIME_PROGRAM = "set_time_program"

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


def _stores(hass: HomeAssistant):
    return list(hass.data.get(DOMAIN, {}).values())


def async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_SET_OVERRIDE):
        return

    async def _set(call: ServiceCall) -> None:
        zone = call.data["zone"]
        temp = call.data["temperature"]
        end = int(time.time() + call.data["duration"].total_seconds())
        for store in _stores(hass):
            gw = store["coordinator"].gateway
            rc = await hass.async_add_executor_job(gw.set_override, zone, temp, end)
            if rc not in (None, 200):
                _LOGGER.warning("set_override zone %s returned response.code=%s", zone, rc)
            await store["override"].async_request_refresh()

    async def _clear(call: ServiceCall) -> None:
        zone = call.data["zone"]
        for store in _stores(hass):
            gw = store["coordinator"].gateway
            await hass.async_add_executor_job(gw.clear_override, zone)
            await store["override"].async_request_refresh()

    async def _set_tprog(call: ServiceCall) -> None:
        pid = call.data["id"]
        comfort = call.data.get("comfort")
        eco = call.data.get("eco")
        profiles = call.data.get("profiles")
        if comfort is None and eco is None and profiles is None:
            raise HomeAssistantError(
                "set_time_program needs at least one of comfort, eco, profiles")
        for store in _stores(hass):
            gw = store["coordinator"].gateway
            fn = functools.partial(gw.set_time_program, pid, comfort=comfort,
                                   eco=eco, profiles=profiles)
            rc = await hass.async_add_executor_job(fn)
            if rc not in (None, 200):
                _LOGGER.warning("set_time_program id=%s returned response.code=%s",
                                pid, rc)
            await store["tprog"].async_request_refresh()

    hass.services.async_register(DOMAIN, SERVICE_SET_OVERRIDE, _set, schema=_SET_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CLEAR_OVERRIDE, _clear, schema=_CLEAR_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_SET_TIME_PROGRAM, _set_tprog,
                                 schema=_TPROG_SCHEMA)


def async_unregister_services(hass: HomeAssistant) -> None:
    if not hass.data.get(DOMAIN):
        hass.services.async_remove(DOMAIN, SERVICE_SET_OVERRIDE)
        hass.services.async_remove(DOMAIN, SERVICE_CLEAR_OVERRIDE)
        hass.services.async_remove(DOMAIN, SERVICE_SET_TIME_PROGRAM)
