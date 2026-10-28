"""Switch entities for on/off (0/1) parameters."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entities
from .entity import AristonEntity
from .runtime import AristonConfigEntry


async def async_setup_entry(hass: HomeAssistant, entry: AristonConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data.coordinator
    add_entities(AristonSwitch(coordinator, spec) for spec in entities.SWITCHES)


class AristonSwitch(AristonEntity, SwitchEntity):
    def __init__(self, coordinator, spec: entities.SwitchSpec):
        super().__init__(coordinator, spec.menu, spec.name)

    @property
    def is_on(self):
        raw = self._raw
        return None if raw is None else bool(raw)

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(0)
