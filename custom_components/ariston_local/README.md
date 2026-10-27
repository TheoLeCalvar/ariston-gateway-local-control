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

Polling interval is configurable under the integration's **Options** (default 60 s).

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
- **Sensors** — Wi-Fi signal strength, Wi-Fi link quality, anti-legionella
  frequency, and live temperatures (zone 1 room temp, zone 1 active setpoint,
  boiler flow temp) read from the `T_` technical channel.
- **Energy sensors** — heating and DHW gas energy (kWh), this month and trailing
  year, from the `Stat/cWh` channel. `device_class=energy` / `state_class=
  total_increasing`, so they plug straight into the **HA Energy dashboard** (cost
  is left to the dashboard's tariff config). Mapping confirmed against the app's
  energy screen (see [docs/06](../../docs/06-roadmap.md)).
- **Binary sensors** — burner/flame (`running`), holiday mode (with an `until`
  attribute), and per-zone "override active" with `target_temperature` and
  `until` attributes.
- **Time program** (diagnostic) — one read-only sensor per schedule (`Func/TProg`
  id 1 and 7); state is the comfort/eco pair, and the decoded day profiles
  (comfort windows as `HH:MM` ranges) are attributes. Read-only — writing the
  schedule isn't implemented yet (needs a capture of a schedule edit).

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

Version 0.9.0 — adds native holiday/absence mode (Func/SpecF) services + a Holiday binary sensor, plus a water-pressure sensor, on top of 0.8.x burner/Wi-Fi sensors.

Version 0.5.0 — adds a per-zone `climate` entity (zone 1) on top of 0.4.0's live
temperature sensors and confirmed enum labels. Current temp + active setpoint come
from the `T_` reads; target changes go through the native override. Zones 2/3
await a capture of their `T_` codes.
