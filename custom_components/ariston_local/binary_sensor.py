"""Binary sensors: per-zone temporary-override active state."""
from __future__ import annotations

from datetime import datetime, timezone

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            add_entities: AddEntitiesCallback) -> None:
    store = hass.data[DOMAIN][entry.entry_id]
    coordinator = store["override"]
    add_entities(AristonOverrideSensor(coordinator, entry, z) for z in coordinator.zones)


class AristonOverrideSensor(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, zone: int):
        super().__init__(coordinator)
        self._zone = zone
        mac = entry.unique_id or entry.data.get("mac", "unknown")
        self._attr_unique_id = f"{mac}_override_z{zone}"
        self._attr_name = f"Zone {zone} override active"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)}, manufacturer=MANUFACTURER,
            name=f"Ariston gateway {mac}")

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
            attrs["until"] = datetime.fromtimestamp(
                info["end"], tz=timezone.utc).isoformat()
        return attrs
