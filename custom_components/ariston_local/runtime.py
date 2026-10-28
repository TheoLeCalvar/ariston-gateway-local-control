"""Per-entry runtime state, and the device actions every surface shares.

One `AristonRuntimeData` lives on `entry.runtime_data` for each configured
gateway: the MQTT gateway client, one coordinator per polled resource, and the
HA-side settings. Its `async_*` actions are the single write path for the
non-parameter resources -- the climate, buttons, datetime and services all go
through them, so a write is always run off the event loop, checked for the
device's response.code, and followed by a refresh of what it changed.
"""
from __future__ import annotations

import functools
import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later

from .const import (
    DEFAULT_CLIMATE_HOLD_HOURS,
    HOLIDAY_SCAN_INTERVAL,
    OVERRIDE_SCAN_INTERVAL,
    SETTLE_REFRESH_DELAYS,
    STATS_SCAN_INTERVAL,
    TPROG_IDS,
    TPROG_SCAN_INTERVAL,
)
from .coordinator import AristonCoordinator, AristonPollCoordinator
from .gateway import AristonGateway

_LOGGER = logging.getLogger(__name__)

type AristonConfigEntry = ConfigEntry[AristonRuntimeData]


@dataclass
class AristonRuntimeData:
    hass: HomeAssistant
    gateway: AristonGateway
    active_zones: tuple[int, ...]
    coordinator: AristonCoordinator       # Menu/Par: {code: raw}
    stats: AristonPollCoordinator         # Stat/cWh: {(typ, idx): [Wh]}
    override: AristonPollCoordinator      # Func/Der: {zone: info | None}
    tprog: AristonPollCoordinator         # Func/TProg: {id: program | None}
    holiday: AristonPollCoordinator       # Func/SpecF: {mode, end}
    # How long a climate-card temperature change holds; owned by the
    # "Override duration" number entity, read by the climate.
    override_hours: float = DEFAULT_CLIMATE_HOLD_HOURS
    # cancel callbacks of scheduled follow-up refreshes (see _settle)
    _pending: set[Callable[[], None]] = field(default_factory=set, repr=False)

    @classmethod
    def create(cls, hass: HomeAssistant, entry: ConfigEntry, gateway: AristonGateway,
               *, scan_interval: int, active_zones: tuple[int, ...]):
        def poll(name, interval, fetch):
            return AristonPollCoordinator(
                hass, entry, name=name, interval=interval, fetch=fetch)

        return cls(
            hass=hass, gateway=gateway, active_zones=active_zones,
            coordinator=AristonCoordinator(
                hass, entry, gateway, scan_interval, active_zones),
            stats=poll("stats", STATS_SCAN_INTERVAL, gateway.read_stats),
            override=poll("override", OVERRIDE_SCAN_INTERVAL, lambda: {
                z: gateway.read_override(z) for z in active_zones}),
            tprog=poll("tprog", TPROG_SCAN_INTERVAL, lambda: {
                i: gateway.read_time_program(i) for i in TPROG_IDS}),
            holiday=poll("holiday", HOLIDAY_SCAN_INTERVAL,
                         gateway.read_special_function),
        )

    @property
    def coordinators(self) -> tuple[AristonPollCoordinator, ...]:
        return (self.coordinator, self.stats, self.override, self.tprog,
                self.holiday)

    async def _call(self, what: str, fn, *args, **kwargs) -> None:
        """Run a blocking gateway write and log a non-200 response.code."""
        rc = await self.hass.async_add_executor_job(
            functools.partial(fn, *args, **kwargs))
        if rc not in (None, 200):
            _LOGGER.warning("%s returned response.code=%s", what, rc)

    async def _settle(self, *coordinators: AristonPollCoordinator) -> None:
        """Refresh what a write changed, now and again as the boiler settles."""
        for c in coordinators:
            await c.async_request_refresh()
        for delay in SETTLE_REFRESH_DELAYS:
            self._refresh_later(delay, coordinators)

    def _refresh_later(self, delay: float, coordinators) -> None:
        cancel: Callable[[], None]

        @callback
        def _fire(_now) -> None:
            self._pending.discard(cancel)
            for c in coordinators:
                self.hass.async_create_task(c.async_refresh())

        cancel = async_call_later(self.hass, delay, _fire)
        self._pending.add(cancel)

    @callback
    def async_shutdown(self) -> None:
        """Cancel follow-up refreshes still scheduled (entry unload)."""
        for cancel in self._pending:
            cancel()
        self._pending.clear()

    # ----------------------------------------------------------- override
    async def async_set_override(self, zone: int, temp_c: float, end_epoch: int) -> None:
        """Hold a zone at temp_c until end_epoch (dérogation, Func/Der)."""
        await self._call(f"set_override zone {zone} -> {temp_c}°C",
                         self.gateway.set_override, zone, temp_c, end_epoch)
        # the hold changes the zone's active setpoint (a T_ read) too
        await self._settle(self.coordinator, self.override)

    async def async_clear_override(self, zone: int) -> None:
        """Cancel a zone's override so it follows its schedule now."""
        await self._call(f"clear_override zone {zone}",
                         self.gateway.clear_override, zone)
        await self._settle(self.coordinator, self.override)

    # ----------------------------------------------------------- holiday
    # Holiday drives the active setpoint (frost-protection) and ends a running
    # hold, so it refreshes those as well as its own state.
    async def async_set_holiday(self, end_epoch: int) -> None:
        await self._call("set_holiday", self.gateway.set_holiday, end_epoch)
        await self._settle(self.holiday, self.coordinator, self.override)

    async def async_clear_holiday(self) -> None:
        await self._call("clear_holiday", self.gateway.clear_holiday)
        await self._settle(self.holiday, self.coordinator, self.override)

    # ----------------------------------------------------------- time program
    async def async_set_time_program(self, prog_id: int, *, comfort=None, eco=None,
                                     profiles=None) -> None:
        await self._call(f"set_time_program id={prog_id}",
                         self.gateway.set_time_program, prog_id,
                         comfort=comfort, eco=eco, profiles=profiles)
        await self.tprog.async_request_refresh()
