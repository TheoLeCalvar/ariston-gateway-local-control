"""DataUpdateCoordinator that polls the gateway's menu parameters."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import entities
from .const import DOMAIN
from .gateway import AristonGateway

_LOGGER = logging.getLogger(__name__)


class AristonCoordinator(DataUpdateCoordinator):
    """Polls all mapped parameter codes in batched Menu/Par reads."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry,
                 gateway: AristonGateway, scan_interval: int):
        super().__init__(
            hass, _LOGGER, name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval))
        self.entry = entry
        self.gateway = gateway
        self._codes = entities.all_codes()

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
