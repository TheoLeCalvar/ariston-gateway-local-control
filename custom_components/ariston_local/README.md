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

- **Numbers** — zone comfort/eco setpoints, zone flow setpoints, DHW comfort
  setpoint (°C, written with the correct ×10 scaling).
- **Select** — Season mode (Eté/Hiver/OFF). Only *Winter* is label-confirmed; the
  other options use the observed raw values — relabel in `entities.py` once mapped.
- **Switches** — anti-legionella, internet time, internet weather.
- **Sensors** — Wi-Fi signal strength, anti-legionella frequency.

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

First version (0.1.0). Not yet a `climate` entity — that needs a confirmed
current-temperature source per zone; the setpoints are exposed as numbers for now.
