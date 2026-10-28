"""DataUpdateCoordinators that poll the gateway's resources."""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import entities
from .const import DOMAIN
from .gateway import AristonGateway

_LOGGER = logging.getLogger(__name__)


class AristonPollCoordinator(DataUpdateCoordinator):
    """Polls one gateway resource on its own interval.

    `fetch` is a blocking gateway call (MQTT request/reply) run in the executor;
    its return value becomes the coordinator data. Each resource (Menu/Par,
    Stat/cWh, Func/Der, Func/TProg, Func/SpecF) gets one of these."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, *, name: str,
                 interval: int, fetch: Callable[[], Any]):
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_{name}",
            update_interval=timedelta(seconds=interval))
        self._fetch = fetch
        self._what = name

    async def _async_update_data(self) -> Any:
        try:
            return await self.hass.async_add_executor_job(self._fetch)
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"error reading {self._what}: {err}") from err


class AristonCoordinator(AristonPollCoordinator):
    """The main poll: all mapped parameter codes in batched Menu/Par reads.

    Data is {code: raw_value}. Also the write path for those parameters."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 gateway: AristonGateway, scan_interval: int,
                 active_zones=entities.ALL_ZONES):
        super().__init__(
            hass, entry, name="menu", interval=scan_interval,
            fetch=functools.partial(gateway.read, entities.all_codes(active_zones)))
        self.gateway = gateway

    async def async_write(self, code: str, raw: int) -> None:
        """Write a parameter, then refresh so entities reflect the new value."""
        rc = await self.hass.async_add_executor_job(self.gateway.write, code, raw)
        if rc not in (None, 200):
            _LOGGER.warning("write %s=%s returned response.code=%s", code, raw, rc)
        await self.async_request_refresh()
