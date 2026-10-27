"""Switch entities for on/off (0/1) parameters."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entities
from .const import DOMAIN
from .entity import AristonEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    add_entities(AristonSwitch(coordinator, spec) for spec in entities.SWITCHES)


class AristonSwitch(AristonEntity, SwitchEntity):
    @property
    def is_on(self):
        raw = self._raw
        return None if raw is None else bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(0)
