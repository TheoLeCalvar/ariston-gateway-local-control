"""Button entities: cancel holiday mode, or cancel a zone's temporary override.

These give one-tap actions in a standard card for the two things that otherwise
need a service call: clearing holiday (Func/SpecF) and clearing a dérogation
(Func/Der) on a zone.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import identify
from .runtime import AristonConfigEntry, AristonRuntimeData


async def async_setup_entry(hass: HomeAssistant, entry: AristonConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    runtime = entry.runtime_data
    ents: list[ButtonEntity] = [AristonClearHolidayButton(runtime, entry)]
    ents += [AristonClearOverrideButton(runtime, entry, zone)
             for zone in runtime.active_zones]
    add_entities(ents)


class AristonClearHolidayButton(ButtonEntity):
    _attr_icon = "mdi:bag-suitcase-off"

    def __init__(self, runtime: AristonRuntimeData, entry: AristonConfigEntry):
        self._runtime = runtime
        identify(self, entry, "clear_holiday", "Clear holiday")

    async def async_press(self) -> None:
        await self._runtime.async_clear_holiday()


class AristonClearOverrideButton(ButtonEntity):
    _attr_icon = "mdi:thermostat-auto"

    def __init__(self, runtime: AristonRuntimeData, entry: AristonConfigEntry,
                 zone: int):
        self._runtime = runtime
        self._zone = zone
        identify(self, entry, f"clear_override_z{zone}", f"Zone {zone} clear override")

    async def async_press(self) -> None:
        await self._runtime.async_clear_override(self._zone)
