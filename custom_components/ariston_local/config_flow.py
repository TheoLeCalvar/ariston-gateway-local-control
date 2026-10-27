"""Config + options flow for Ariston local."""
from __future__ import annotations

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_ACTIVE_ZONES,
    CONF_CAFILE,
    CONF_HOST,
    CONF_MAC,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_ACTIVE_ZONES,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .gateway import AristonGateway


class AristonConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            mac = user_input[CONF_MAC].replace(":", "").upper()
            user_input[CONF_MAC] = mac
            await self.async_set_unique_id(mac)
            self._abort_if_unique_id_configured()
            try:
                gw = AristonGateway(
                    user_input[CONF_HOST], user_input[CONF_PORT], mac,
                    cafile=user_input.get(CONF_CAFILE) or None)
                await self.hass.async_add_executor_job(gw.connect)
                await self.hass.async_add_executor_job(gw.disconnect)
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=f"Ariston {mac}", data=user_input)

        schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
            vol.Required(CONF_MAC): str,
            vol.Optional(CONF_CAFILE, default=""): str,
        })
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return AristonOptionsFlow(entry)


class AristonOptionsFlow(OptionsFlow):
    def __init__(self, entry: ConfigEntry):
        self.entry = entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            # Zone 1 always stays on (it carries the climate entity); the
            # selector only offers the optional zones 2/3.
            zones = [1] + sorted(
                int(z) for z in user_input.get(CONF_ACTIVE_ZONES, []))
            return self.async_create_entry(title="", data={
                CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                CONF_ACTIVE_ZONES: zones,
            })

        opts = self.entry.options
        scan = opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        active = opts.get(CONF_ACTIVE_ZONES, DEFAULT_ACTIVE_ZONES)
        extra = [str(z) for z in (2, 3) if z in active]
        schema = vol.Schema({
            vol.Optional(CONF_SCAN_INTERVAL, default=scan): vol.All(int, vol.Range(min=15)),
            vol.Optional(CONF_ACTIVE_ZONES, default=extra): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        selector.SelectOptionDict(value="2", label="Zone 2"),
                        selector.SelectOptionDict(value="3", label="Zone 3"),
                    ],
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
