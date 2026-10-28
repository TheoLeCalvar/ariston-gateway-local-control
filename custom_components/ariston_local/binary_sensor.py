"""Binary sensors: burner/status reads, holiday mode, per-zone override active."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import entities
from .const import SPECF_HOLIDAY_MODE
from .entity import AristonCoordinatorEntity, AristonEntity
from .runtime import AristonConfigEntry


def _iso(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


async def async_setup_entry(hass: HomeAssistant, entry: AristonConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    runtime = entry.runtime_data
    ents: list[BinarySensorEntity] = [
        AristonOverrideSensor(runtime.override, z) for z in runtime.active_zones]
    ents += [AristonStatusBinarySensor(runtime.coordinator, spec)
             for spec in entities.BINARY_SENSORS]
    ents.append(AristonHolidaySensor(runtime.holiday))
    add_entities(ents)


class AristonStatusBinarySensor(AristonEntity, BinarySensorEntity):
    """On/off status from a T_ read on the main poll (e.g. burner flame)."""

    def __init__(self, coordinator, spec: entities.BinarySpec):
        super().__init__(coordinator, spec.menu, spec.name, spec.prefix)
        if spec.device_class:
            self._attr_device_class = BinarySensorDeviceClass(spec.device_class)

    @property
    def is_on(self):
        raw = self._raw
        return None if raw is None else bool(raw)


class AristonHolidaySensor(AristonCoordinatorEntity, BinarySensorEntity):
    """Holiday / absence mode active (Func/SpecF Mode 4), with its end time."""

    _attr_icon = "mdi:bag-suitcase"

    def __init__(self, coordinator):
        super().__init__(coordinator, "holiday", "Holiday mode")

    @property
    def is_on(self):
        data = self.coordinator.data
        return None if not data else data.get("mode") == SPECF_HOLIDAY_MODE

    @property
    def extra_state_attributes(self):
        data = self.coordinator.data or {}
        if data.get("mode") != SPECF_HOLIDAY_MODE or not data.get("end"):
            return None
        return {"until": _iso(data["end"])}


class AristonOverrideSensor(AristonCoordinatorEntity, BinarySensorEntity):
    """A zone's temporary override (dérogation, Func/Der) is running."""

    def __init__(self, coordinator, zone: int):
        super().__init__(coordinator, f"override_z{zone}", f"Zone {zone} override active")
        self._zone = zone

    @property
    def _info(self):
        return (self.coordinator.data or {}).get(self._zone)

    @property
    def is_on(self):
        info = self._info
        return None if info is None else bool(info.get("enabled"))

    @property
    def extra_state_attributes(self):
        info = self._info
        if not info or not info.get("enabled"):
            return None
        attrs = {"target_temperature": info.get("temp")}
        if info.get("end"):
            attrs["until"] = _iso(info["end"])
        return attrs
