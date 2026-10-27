"""Select entities for enumerated parameters (e.g. season mode)."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entities
from .const import DOMAIN
from .entity import AristonEntity


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    add_entities(AristonSelect(coordinator, spec) for spec in entities.SELECTS)


class AristonSelect(AristonEntity, SelectEntity):
    def __init__(self, coordinator, spec: entities.SelectSpec):
        super().__init__(coordinator, spec.menu, spec.name)
        self._label_to_raw = dict(spec.options)
        self._raw_to_label = {v: k for k, v in spec.options.items()}
        self._attr_options = list(spec.options)

    @property
    def current_option(self):
        raw = self._raw
        if raw is None:
            return None
        return self._raw_to_label.get(raw, f"Unknown ({raw})")

    async def async_select_option(self, option: str) -> None:
        if option in self._label_to_raw:
            await self._write(self._label_to_raw[option])
