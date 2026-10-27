# Ariston Local — Home Assistant integration

Control the Ariston Wi-Fi + Zigbee gateway **entirely locally** over MQTT, with no
cloud round-trip. It speaks the reverse-engineered `Menu/Par` protocol (see the
repo's [docs/05](../../docs/05-menu-parameters.md)) directly to the gateway on your
local broker.

## Prerequisites

The gateway must be running in **Mode A** — connected to your local Mosquitto
broker instead of the Ariston cloud (DNS override + self-signed cert; see
[docs/03](../../docs/03-local-broker-and-bridge.md)). This integration is a second
MQTT client on that same broker; it publishes `Menu/Par` GET/PUT requests to the
gateway and reads the replies.

## Install

1. Copy `custom_components/ariston_local/` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → "Ariston Local"**.
4. Enter:
   - **Broker host / port** — your Mosquitto broker (e.g. `127.0.0.1`, `8883`).
   - **Gateway MAC** — the gateway client id, e.g. `AABBCCDDEEFF`.
   - **CA/cert file** — optional; the broker cert. Left blank, TLS is used without
     verification (the gateway itself doesn't verify either).

Under the integration's **Options** you can set the polling interval (default
60 s) and pick which heating **zones** to expose. Zone 1 is always on; zones 2
and 3 are on by default — turn them off on a single-zone system and their
setpoint/flow entities (and polling) disappear.

## Entities

Driven by the parameter map in [`menu_params.json`](menu_params.json) and the
catalog in [`entities.py`](entities.py):

- **Climate** — a per-zone thermostat (zone 1 confirmed). Current temperature and
  active setpoint are the live `T_` reads; `AUTO`/`HEAT` map to the zone mode
  (Chauffage programmé / Manuel). Setting a target issues a timed hold
  (dérogation, default 3 h, auto-reverting) — for another duration use the
  `set_override` service. Zones 2/3 are added once their `T_` codes are captured.

- **Numbers** — zone comfort/eco setpoints, zone flow setpoints, DHW comfort
  setpoint (°C, written with the correct ×10 scaling).
- **Select** — Season mode (Eté/Hiver/OFF). Only *Winter* is label-confirmed; the
  other options use the observed raw values — relabel in `entities.py` once mapped.
- **Switches** — anti-legionella, internet time, internet weather.
- **Sensors** — Wi-Fi signal, anti-legionella frequency, and live temperatures
  (zone 1 room temp, zone 1 active setpoint, boiler flow temp) read from the `T_`
  technical channel.
- **Energy sensors** — heating and DHW gas energy (kWh) from the `Stat/cWh`
  channel, mapping confirmed against the app's energy screen (see
  [docs/06](../../docs/06-roadmap.md)):
  - **"… energy total"** (heating, DHW) — monotonic lifetime counters
    (`total_increasing`); add *these* to the **HA Energy dashboard** (cost is
    left to the dashboard's tariff config). The gateway has no lifetime
    odometer, so the integration builds the total by folding the this-month
    accumulator into a running total that only ever rises and persists it across
    restarts; counting starts when the integration is first added.
  - **"… this month"** and **"… (trailing year)"** — plain diagnostic read-outs
    of the device's own windowed figures. The trailing-year window's sum can
    *decrease* as old months roll off, which a `total_increasing` statistic
    mis-reads as a reset — so these are deliberately not statistics and are not
    offered to the Energy dashboard.
- **Binary sensors** — burner/flame (`running`), holiday mode (with an `until`
  attribute), and per-zone "override active" with `target_temperature` and
  `until` attributes.
- **Date/time** — "Holiday until": set it to turn on holiday/absence mode until
  that moment (`Func/SpecF`); it reads back the active end. Pairs with the
  "Clear holiday" button, so holiday is fully editable from a standard card — no
  custom card needed.
- **Number (HA-side)** — "Override duration" (hours): how long a temperature set
  on the climate card holds before auto-reverting. Restored across restarts.
- **Buttons** — "Clear holiday" and per-zone "Zone N clear override".
- **Time program** (diagnostic) — one sensor per schedule (`Func/TProg` id 1 and
  7); state is the comfort/eco pair. A legible `schedule` attribute lists each
  day-group (e.g. `Mon–Fri: 06:30–08:30, 17:30–23:30`); `profiles` keeps the
  structured form. The boiler owns the schedule and runs it even if HA is down —
  this is a reflection of it, polled every 5 min so a thermostat-side edit shows
  up here. To *change* it, use the `set_time_program` service (read-modify-write,
  confirmed on hardware) or the weekly-grid **Lovelace card** in [`lovelace/`](../../lovelace/).

## Services

- `ariston_local.set_override` — hold a zone at a temperature for a duration
  (default 2 h), native dérogation via `Func/Der`; the boiler reverts on its own.
- `ariston_local.clear_override` — cancel a zone's override now.
- `ariston_local.set_time_program` — change a weekly schedule (`Func/TProg`):
  set `comfort`/`eco` and/or replace the `profiles` (day-group → comfort windows,
  `HH:MM` snapped to 15-min slots). Read-modify-write, confirmed on hardware.
- `ariston_local.set_holiday` (until) / `ariston_local.clear_holiday` — native
  holiday/absence mode (`Func/SpecF`); the boiler returns to normal at the date.

Add more by mapping the code in `menu_params.json` and adding a spec to
`entities.py` — no other code changes needed.

## Safety

Writes change real settings on a real boiler. The integration only writes `U_`
(user) parameters, refuses the `DEPLOY-V2` channel, and the connectivity-enable
parameter (`6.16.0`) is deliberately **not** exposed (disabling it would cut the
gateway off). Values are range-checked by each entity. A write returns the
device's `response.code` (200 = accepted), logged on anything else.

## Tests

The protocol layer is pure Python (no HA, no paho):

```bash
python3 -I custom_components/ariston_local/tests/test_protocol.py
```

## Status

See [`CHANGELOG.md`](../../CHANGELOG.md) for the full per-version history.

Version 0.13.0 — lifetime energy totals (monotonic `total_increasing` counters)
for the Energy dashboard; the month/trailing-year figures become diagnostic
read-outs (the rolling year window is no longer mis-fed as a counter).

Version 0.12.0 — holiday mode and the temporary override are now editable from
the UI via native entities (a "Holiday until" datetime, an "Override duration"
number, and clear buttons) — no custom card required. Switching a zone back to
Auto also cancels any active override.

Version 0.11.1 — fix the switch platform failing to load.

Version 0.11.0 — an **active-zones** option (hide zones 2/3 on a single-zone
system; they stop being polled and shown).

Version 0.10.0 — local-first weekly schedule: faster `Func/TProg` poll (5 min), a
legible `schedule` attribute, read-back verify on writes, and a weekly-grid
Lovelace card in [`lovelace/`](../../lovelace/). Single "Wi-Fi signal" sensor.

Version 0.9.0 — adds native holiday/absence mode (Func/SpecF) services + a Holiday binary sensor, plus a water-pressure sensor, on top of 0.8.x burner/Wi-Fi sensors.

Version 0.5.0 — adds a per-zone `climate` entity (zone 1) on top of 0.4.0's live
temperature sensors and confirmed enum labels. Current temp + active setpoint come
from the `T_` reads; target changes go through the native override. Zones 2/3
await a capture of their `T_` codes.
