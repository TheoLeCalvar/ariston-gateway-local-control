"""HA-level tests: set the integration up against a fake gateway (no MQTT).

The protocol layer has its own stdlib tests (custom_components/ariston_local/
tests/test_protocol.py); these cover the HA wiring on top of it -- entity
creation (unique ids must stay stable across refactors, or users lose entity
history), the write actions behind buttons/climate/datetime/services, and
setup/unload of the gateway connection.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.ariston_local.const import DOMAIN

MAC = "AABBCCDDEEFF"
GATEWAY = "custom_components.ariston_local.AristonGateway"

# Every entity the integration creates with all three zones active. A change to
# this list renames entities for existing installs -- only do it deliberately.
EXPECTED_UNIQUE_IDS = sorted(f"{MAC}_{key}" for key in [
    # Menu/Par parameters (menu code as read: U_ user / T_ technical)
    "T_19.1.1", "T_2.1.0", "T_4.3.0", "T_4.3.1", "T_8.2.9", "T_8.4.0",
    "U_3", "U_6.16.6", "U_6.16.7", "U_6.3.0.0", "U_6.3.0.2", "U_6.3.0.4",
    "U_6.3.1.0.0", "U_6.3.1.0.1", "U_6.3.1.1.0", "U_6.3.1.1.1",
    "U_6.3.1.2.0", "U_6.3.1.2.1", "U_6.9.0", "U_6.9.5.0", "U_6.9.5.1",
    # climate, override, holiday
    "climate_zone1", "override_hours",
    "override_z1", "override_z2", "override_z3",
    "clear_override_z1", "clear_override_z2", "clear_override_z3",
    "holiday", "holiday_until", "clear_holiday",
    # energy and time programs
    "energy_heating_month", "energy_dhw_month",
    "energy_heating_year", "energy_dhw_year",
    "energy_heating_total", "energy_dhw_total",
    "tprog_1", "tprog_7",
])


class FakeGateway:
    """Stands in for AristonGateway: canned reads, records every write."""

    instances: list["FakeGateway"] = []

    def __init__(self, host, port, mac, **kwargs):
        self.calls: list[tuple] = []
        self.reads = 0                      # Menu/Par polls
        self.holds: dict[int, dict] = {}    # zone -> active Func/Der hold
        self.holiday = {"mode": 1, "end": 0}
        # Stat/cWh: idx 1 = 24 two-hour blocks (newest last), 2 = days, 4 = months
        self.stats = {(6, 1): [0] * 24, (9, 1): [0] * 23 + [605],
                      (6, 2): [1000, 2000], (9, 2): [500],
                      (6, 4): [3000, 4000], (9, 4): [700]}
        self.stats_fail = False
        FakeGateway.instances.append(self)

    # lifecycle
    def connect(self, timeout=10):
        self.calls.append(("connect",))

    def disconnect(self):
        self.calls.append(("disconnect",))

    # reads
    def read(self, codes, timeout=8):
        self.reads += 1
        return {code: 200 for code in codes}   # setpoints decode to 20.0 °C

    def read_stats(self, timeout=8):
        if self.stats_fail:
            raise TimeoutError("no Stat/cWh reply")
        return {key: list(values) for key, values in self.stats.items()}

    def read_override(self, zone=1, timeout=8):
        return self.holds.get(
            zone, {"zone": zone, "enabled": False, "temp": None, "end": None})

    def read_time_program(self, prog_id, timeout=8):
        return {"id": prog_id, "slot_minutes": 15, "comfort": 20.0, "eco": 16.0,
                "profiles": [{"days": [1, 2, 3, 4, 5],
                              "windows": [["06:30", "08:30"]]}]}

    def read_special_function(self, timeout=8):
        return dict(self.holiday)

    # writes
    def write(self, code, raw, timeout=8):
        self.calls.append(("write", code, raw))
        return 200

    def set_override(self, zone, temp_c, end_epoch, timeout=8):
        self.calls.append(("set_override", zone, temp_c))
        self.override_end = end_epoch
        self.holds[zone] = {"zone": zone, "enabled": True, "temp": temp_c,
                            "end": end_epoch}
        return 200

    def clear_override(self, zone, timeout=8):
        self.calls.append(("clear_override", zone))
        self.holds.pop(zone, None)
        return 200

    def set_holiday(self, end_epoch, timeout=8):
        self.calls.append(("set_holiday", end_epoch))
        self.holiday = {"mode": 4, "end": end_epoch}
        return 200

    def clear_holiday(self, timeout=8):
        self.calls.append(("clear_holiday",))
        self.holiday = {"mode": 1, "end": 0}
        return 200

    def set_time_program(self, prog_id, *, comfort=None, eco=None, profiles=None,
                         timeout=8, verify=True):
        self.calls.append(("set_time_program", prog_id, comfort, eco))
        return 200


@pytest.fixture
def fake_gateway():
    FakeGateway.instances.clear()
    with patch(GATEWAY, FakeGateway):
        yield FakeGateway


async def _setup(hass, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, title=f"Ariston {MAC}",
        data={"host": "127.0.0.1", "port": 8883, "mac": MAC},
        options=options or {})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, FakeGateway.instances[-1]


def _entity_id(hass, entry, unique_id):
    reg = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(reg, entry.entry_id):
        if ent.unique_id == unique_id:
            return ent.entity_id
    raise AssertionError(f"no entity with unique_id {unique_id}")


async def test_unique_ids_are_stable(hass, fake_gateway):
    entry, _ = await _setup(hass)
    reg = er.async_get(hass)
    uids = sorted(e.unique_id for e in
                  er.async_entries_for_config_entry(reg, entry.entry_id))
    assert uids == EXPECTED_UNIQUE_IDS


async def test_single_zone_drops_zone_2_3_entities(hass, fake_gateway):
    entry, _ = await _setup(hass, options={"active_zones": [1]})
    reg = er.async_get(hass)
    uids = {e.unique_id for e in
            er.async_entries_for_config_entry(reg, entry.entry_id)}
    assert f"{MAC}_override_z1" in uids
    assert not {f"{MAC}_override_z2", f"{MAC}_clear_override_z3"} & uids


async def test_energy_sensors(hass, fake_gateway):
    entry, _ = await _setup(hass)
    month = hass.states.get(_entity_id(hass, entry, f"{MAC}_energy_heating_month"))
    total = hass.states.get(_entity_id(hass, entry, f"{MAC}_energy_heating_total"))
    assert float(month.state) == 3.0          # 1000 + 2000 Wh
    assert float(total.state) == 0.0          # first reading is the baseline
    assert total.attributes["state_class"] == "total_increasing"
    assert "state_class" not in month.attributes


async def test_energy_total_counts_the_two_hour_blocks(hass, fake_gateway):
    entry, gw = await _setup(hass)
    total_id = _entity_id(hass, entry, f"{MAC}_energy_dhw_total")
    stats = entry.runtime_data.stats
    value = lambda: float(hass.states.get(total_id).state)  # noqa: E731
    assert value() == 0.0                         # first window is a baseline

    gw.stats[(9, 1)][-1] += 2008                  # a shower in the open block
    await stats.async_refresh()
    await hass.async_block_till_done()
    assert value() == 2.008

    gw.stats_fail = True                          # a failed poll re-notifies
    await stats.async_refresh()                   # listeners with the old data:
    gw.stats_fail = False                         # it must not be counted twice
    await stats.async_refresh()
    await hass.async_block_till_done()
    assert value() == 2.008

    # a reload keeps the total *and* the window, so nothing is re-counted
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    gw = FakeGateway.instances[-1]
    gw.stats[(9, 1)] = [0] * 23 + [605 + 2008]    # same window as before reload
    await entry.runtime_data.stats.async_refresh()
    await hass.async_block_till_done()
    assert value() == 2.008
    gw.stats[(9, 1)][-1] += 400
    await entry.runtime_data.stats.async_refresh()
    await hass.async_block_till_done()
    assert value() == 2.408


async def test_buttons(hass, fake_gateway):
    entry, gw = await _setup(hass)
    for uid in ("clear_holiday", "clear_override_z2"):
        await hass.services.async_call(
            "button", "press",
            {"entity_id": _entity_id(hass, entry, f"{MAC}_{uid}")}, blocking=True)
    assert ("clear_holiday",) in gw.calls
    assert ("clear_override", 2) in gw.calls


async def test_climate_hold_uses_override_duration(hass, fake_gateway):
    entry, gw = await _setup(hass)
    await hass.services.async_call(
        "number", "set_value",
        {"entity_id": _entity_id(hass, entry, f"{MAC}_override_hours"), "value": 5},
        blocking=True)
    climate = _entity_id(hass, entry, f"{MAC}_climate_zone1")
    before = datetime.now(timezone.utc).timestamp()
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": climate, "temperature": 21},
        blocking=True)
    assert ("set_override", 1, 21.0) in gw.calls
    assert abs(gw.override_end - (before + 5 * 3600)) < 60

    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": climate, "hvac_mode": "auto"},
        blocking=True)
    assert ("write", "U_0.0", 3) in gw.calls       # zone 1 → Chauffage programmé
    assert ("clear_override", 1) in gw.calls       # back on the schedule now


async def test_holiday_datetime(hass, fake_gateway):
    entry, gw = await _setup(hass)
    until = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(days=3)
    await hass.services.async_call(
        "datetime", "set_value",
        {"entity_id": _entity_id(hass, entry, f"{MAC}_holiday_until"),
         "datetime": until.isoformat()}, blocking=True)
    assert ("set_holiday", int(until.timestamp())) in gw.calls


async def test_services(hass, fake_gateway):
    _, gw = await _setup(hass)
    until = datetime.now(timezone.utc) + timedelta(days=2)
    for service, data in [
        ("set_override", {"zone": 2, "temperature": 19}),
        ("clear_override", {"zone": 2}),
        ("set_holiday", {"until": until.isoformat()}),
        ("clear_holiday", {}),
        ("set_time_program", {"id": 1, "comfort": 20.5}),
    ]:
        await hass.services.async_call(DOMAIN, service, data, blocking=True)
    assert ("set_override", 2, 19.0) in gw.calls
    assert ("clear_override", 2) in gw.calls
    assert ("set_holiday", int(until.timestamp())) in gw.calls
    assert ("clear_holiday",) in gw.calls
    assert ("set_time_program", 1, 20.5, None) in gw.calls


async def test_climate_shows_hold_target_immediately(hass, fake_gateway):
    """The hold's temperature shows before the active-setpoint read catches up."""
    entry, gw = await _setup(hass)
    climate = _entity_id(hass, entry, f"{MAC}_climate_zone1")
    assert hass.states.get(climate).attributes["temperature"] == 20.0
    await hass.services.async_call(
        DOMAIN, "set_override", {"zone": 1, "temperature": 22.5}, blocking=True)
    await hass.async_block_till_done()
    # the fake's T_ setpoint still reads 20.0; the hold (Func/Der) says 22.5
    assert hass.states.get(climate).attributes["temperature"] == 22.5

    # holiday takes precedence over a hold: back to the boiler's setpoint read
    await hass.services.async_call(
        DOMAIN, "set_holiday", {"until": (datetime.now(timezone.utc)
                                          + timedelta(days=1)).isoformat()},
        blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(climate).attributes["temperature"] == 20.0


async def test_writes_refresh_again_as_the_boiler_settles(hass, fake_gateway):
    entry, gw = await _setup(hass)
    before = gw.reads
    await hass.services.async_call(DOMAIN, "clear_holiday", {}, blocking=True)
    await hass.async_block_till_done()
    assert gw.reads == before + 1          # holiday now re-reads the setpoint too
    for delay in (5, 15):                  # const.SETTLE_REFRESH_DELAYS
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=delay + 1))
        await hass.async_block_till_done()
    assert gw.reads == before + 3


async def test_unload_cancels_pending_refreshes(hass, fake_gateway):
    entry, gw = await _setup(hass)
    await hass.services.async_call(
        DOMAIN, "clear_override", {"zone": 1}, blocking=True)
    assert await hass.config_entries.async_unload(entry.entry_id)
    reads = gw.reads
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()
    assert gw.reads == reads               # nothing polls a disconnected gateway


async def test_unload_disconnects(hass, fake_gateway):
    entry, gw = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert ("disconnect",) in gw.calls


async def test_failed_first_poll_releases_the_connection(hass, fake_gateway):
    """A setup retry must not leave the previous MQTT client running."""
    def failing_read(self, codes, timeout=8):
        raise TimeoutError("no reply")

    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC,
        data={"host": "127.0.0.1", "port": 8883, "mac": MAC})
    entry.add_to_hass(hass)
    with patch.object(FakeGateway, "read", failing_read):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY
    assert ("disconnect",) in FakeGateway.instances[-1].calls
