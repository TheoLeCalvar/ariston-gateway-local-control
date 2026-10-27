"""Ariston local — control the gateway over local MQTT (no cloud).

Built on the reverse-engineered Menu/Par protocol; see the repo docs/05.
"""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_ACTIVE_ZONES,
    CONF_CAFILE,
    CONF_HOST,
    CONF_MAC,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_ACTIVE_ZONES,
    DEFAULT_CLIMATE_HOLD_HOURS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    TPROG_IDS,
)
from .coordinator import (
    AristonCoordinator,
    AristonHolidayCoordinator,
    AristonOverrideCoordinator,
    AristonStatsCoordinator,
    AristonTProgCoordinator,
)
from .gateway import AristonGateway
from .services import async_register_services, async_unregister_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.NUMBER, Platform.SELECT, Platform.SWITCH,
             Platform.SENSOR, Platform.BINARY_SENSOR, Platform.DATETIME,
             Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = entry.data
    gateway = AristonGateway(
        data[CONF_HOST], data[CONF_PORT], data[CONF_MAC],
        cafile=data.get(CONF_CAFILE))

    try:
        await hass.async_add_executor_job(gateway.connect)
    except Exception as err:  # noqa: BLE001
        raise ConfigEntryNotReady(f"cannot connect to broker: {err}") from err

    active_zones = tuple(entry.options.get(CONF_ACTIVE_ZONES, DEFAULT_ACTIVE_ZONES))

    scan = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = AristonCoordinator(hass, entry, gateway, scan, active_zones)
    await coordinator.async_config_entry_first_refresh()

    stats = AristonStatsCoordinator(hass, entry, gateway)
    await stats.async_config_entry_first_refresh()

    overrides = AristonOverrideCoordinator(hass, entry, gateway, zones=active_zones)
    await overrides.async_config_entry_first_refresh()

    tprog = AristonTProgCoordinator(hass, entry, gateway, TPROG_IDS)
    await tprog.async_config_entry_first_refresh()

    holiday = AristonHolidayCoordinator(hass, entry, gateway)
    await holiday.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "coordinator": coordinator, "stats": stats, "override": overrides,
        "tprog": tprog, "holiday": holiday, "active_zones": active_zones,
        # default hold length for a climate-card temperature change; the
        # "Override duration" number entity updates this at runtime.
        "override_hours": DEFAULT_CLIMATE_HOLD_HOURS}
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    async_register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        store = hass.data[DOMAIN].pop(entry.entry_id)
        await hass.async_add_executor_job(store["coordinator"].gateway.disconnect)
        async_unregister_services(hass)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
