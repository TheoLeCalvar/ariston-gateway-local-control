"""Ariston local — control the gateway over local MQTT (no cloud).

Built on the reverse-engineered Menu/Par protocol; see the repo docs/05.

Layout: the pure protocol layer (kura, menu, energy, override, tprog,
entities) has no Home Assistant imports and is unit-tested standalone;
gateway.py is the blocking MQTT client on top of it; coordinator.py polls it;
runtime.py holds each entry's state and the shared write actions; the platform
modules only map that onto HA entities.
"""
from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_ACTIVE_ZONES,
    CONF_CAFILE,
    CONF_HOST,
    CONF_MAC,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_ACTIVE_ZONES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .gateway import AristonGateway
from .runtime import AristonConfigEntry, AristonRuntimeData
from .services import async_register_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.CLIMATE, Platform.NUMBER, Platform.SELECT, Platform.SWITCH,
             Platform.SENSOR, Platform.BINARY_SENSOR, Platform.DATETIME,
             Platform.BUTTON]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    # Services are integration-wide (they act on every loaded gateway), so they
    # are registered once here rather than per config entry.
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AristonConfigEntry) -> bool:
    data = entry.data
    gateway = AristonGateway(
        data[CONF_HOST], data[CONF_PORT], data[CONF_MAC],
        cafile=data.get(CONF_CAFILE))

    try:
        await hass.async_add_executor_job(gateway.connect)
    except Exception as err:  # noqa: BLE001
        await hass.async_add_executor_job(gateway.disconnect)
        raise ConfigEntryNotReady(f"cannot connect to broker: {err}") from err

    runtime = AristonRuntimeData.create(
        hass, entry, gateway,
        scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        active_zones=tuple(entry.options.get(CONF_ACTIVE_ZONES, DEFAULT_ACTIVE_ZONES)))
    try:
        for coordinator in runtime.coordinators:
            await coordinator.async_config_entry_first_refresh()
    except Exception:
        # HA retries setup with a fresh gateway; don't leave this one's MQTT
        # client (and its network thread) running behind it.
        await hass.async_add_executor_job(gateway.disconnect)
        raise

    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AristonConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await hass.async_add_executor_job(entry.runtime_data.gateway.disconnect)
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: AristonConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)
