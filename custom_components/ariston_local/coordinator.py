"""DataUpdateCoordinator that polls the gateway's menu parameters."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import entities
from .const import DOMAIN, TPROG_SCAN_INTERVAL
from .gateway import AristonGateway

_LOGGER = logging.getLogger(__name__)


class AristonCoordinator(DataUpdateCoordinator):
    """Polls all mapped parameter codes in batched Menu/Par reads."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 gateway: AristonGateway, scan_interval: int,
                 active_zones=entities.ALL_ZONES):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval))
        self.entry = entry
        self.gateway = gateway
        self._codes = entities.all_codes(active_zones)

    async def _async_update_data(self) -> dict:
        try:
            return await self.hass.async_add_executor_job(
                self.gateway.read, self._codes)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"error reading gateway: {err}") from err

    async def async_write(self, code: str, raw: int) -> None:
        """Write a parameter, then refresh so entities reflect the new value."""
        rc = await self.hass.async_add_executor_job(self.gateway.write, code, raw)
        if rc not in (None, 200):
            _LOGGER.warning("write %s=%s returned response.code=%s", code, raw, rc)
        await self.async_request_refresh()


class AristonOverrideCoordinator(DataUpdateCoordinator):
    """Polls each zone's temporary override (Func/Der). Data is {zone: info|None}."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 gateway: AristonGateway, zones=(1, 2, 3), scan_interval: int = 120):
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_override",
            update_interval=timedelta(seconds=scan_interval))
        self.entry = entry
        self.gateway = gateway
        self.zones = zones

    async def _async_update_data(self) -> dict:
        def _read_all():
            return {z: self.gateway.read_override(z) for z in self.zones}
        try:
            return await self.hass.async_add_executor_job(_read_all)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"error reading overrides: {err}") from err


class AristonTProgCoordinator(DataUpdateCoordinator):
    """Polls the heating time programs (Func/TProg). Data is {id: program|None}.

    The boiler owns the schedule; this poll is how an edit made on the thermostat
    itself gets reflected in HA (and how a write is confirmed to have landed)."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 gateway: AristonGateway, prog_ids=(1, 7),
                 scan_interval: int = TPROG_SCAN_INTERVAL):
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_tprog",
            update_interval=timedelta(seconds=scan_interval))
        self.entry = entry
        self.gateway = gateway
        self.prog_ids = prog_ids

    async def _async_update_data(self) -> dict:
        def _read_all():
            return {i: self.gateway.read_time_program(i) for i in self.prog_ids}
        try:
            return await self.hass.async_add_executor_job(_read_all)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"error reading time programs: {err}") from err


class AristonHolidayCoordinator(DataUpdateCoordinator):
    """Polls the special function (Func/SpecF). Data is {mode, end}."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 gateway: AristonGateway, scan_interval: int = 300):
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_holiday",
            update_interval=timedelta(seconds=scan_interval))
        self.entry = entry
        self.gateway = gateway

    async def _async_update_data(self) -> dict:
        try:
            return await self.hass.async_add_executor_job(
                self.gateway.read_special_function)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"error reading special function: {err}") from err


class AristonStatsCoordinator(DataUpdateCoordinator):
    """Polls energy stats (Stat/cWh) on a slow interval; data is {(typ, idx): [Wh]}."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 gateway: AristonGateway, scan_interval: int = 600):
        super().__init__(
            hass, _LOGGER, name=f"{DOMAIN}_stats",
            update_interval=timedelta(seconds=scan_interval))
        self.entry = entry
        self.gateway = gateway

    async def _async_update_data(self) -> dict:
        try:
            return await self.hass.async_add_executor_job(self.gateway.read_stats)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"error reading stats: {err}") from err
