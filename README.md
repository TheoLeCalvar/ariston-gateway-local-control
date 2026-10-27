# Ariston gateway — local control

Reverse-engineering local access to the Ariston Wi-Fi + Zigbee gateway, which has
no local API and talks only to Eurotech's Everyware Cloud over MQTT/TLS. The goal
is local monitoring and control for a Home Assistant integration, without the
cloud round-trip.

## Status

- ✅ Established the gateway has **no local service** (full port scan, mDNS, SSDP, CoAP all negative).
- ✅ Identified the cloud endpoint: **`broker-ari.everyware-cloud.com:8883`** (Eurotech Everyware Cloud, MQTT/TLS).
- ✅ Confirmed the gateway **does not validate the broker TLS cert** → a local broker can take over the connection via a DNS override.
- ✅ Mapped the MQTT topic namespace and decoded the **Kura/Kapua protobuf** payload format.
- ✅ Tooling: stdlib payload **decoder + encoder**, capture analyzer, request/reply client, live monitor, transparent MITM proxy, bridge config.
- ✅ Tests: round-trip codec + analyzer + eBUS-parser unit tests (`tools/test_kura.py`, stdlib only).
- ✅ Decoded the hex metric-name structure as **BridgeNet-over-eBUS register IDs** (little-endian register + `0xFE` broadcast + zone), confirmed against community registers (`6810` flow, `6910` return, `c04b` status). See [docs/04](docs/04-ebus-parameters.md).
- ⬜ Build the command dictionary (`GET`/`PUT`/`EXEC` resources) by capturing the official app via the MITM proxy (use `tools/analyze_capture.py` to roll up the log).
- ✅ Found the real **config channel**: the `Menu/Par` resource reads/writes settings by installer-menu code (`U_6.3.1.0.0` = menu 6.3.1.0.0). Mapped the codes to the web installer menu and confirmed ×10 temperature scaling + enums. See [docs/05](docs/05-menu-parameters.md).
- ✅ Confirmed the **write** format from a captured PUT: `PUT Menu/Par` with the code as the metric name (`U_3 = 1`), no timestamp — reproduced byte-for-byte. `tools/write_menu.py` writes scaling-aware, dry-run + confirm by default.
- ✅ Tooling: `read_menu.py`/`write_menu.py` read and write menu params in Mode A; `menu_params.py`/`.json` decode and annotate them.
- ✅ **Full local control confirmed on hardware** (Mode A): read a parameter, wrote a setpoint, and the thermostat's own display updated — no cloud involved.
- ✅ **Home Assistant integration** (`custom_components/ariston_local/`): local-MQTT custom component exposing setpoints (numbers), season mode (select), toggles (switches) and sensors via the Menu/Par protocol. First version; see its [README](custom_components/ariston_local/README.md).
- ⬜ Add a `climate` entity per zone (needs a confirmed current-temperature source) and expand the parameter map.
- ⬜ Confirm per-register scaling/enums on this model and expand `tools/ebus_registers.json` from captures.
- ⬜ Drive the device from a local client / HA integration.

## Device under test

| | |
|---|---|
| IP | `192.168.42.114` |
| MAC / client-id | `AABBCCDDEEFF` (Espressif) |
| MQTT username | `AABBCCDDEEFF-XXXXXXXXXX` |
| Cloud broker | `broker-ari.everyware-cloud.com:8883` (AWS eu-west-1) |

## Repo layout

```
docs/
  01-discovery.md            how we found the cloud-only MQTT architecture
  02-mqtt-protocol.md        topics, connection, Kura payload format + examples
  03-local-broker-and-bridge.md  the three interception modes + safety
  04-ebus-parameters.md      decoding the hex metric names into eBUS registers
  05-menu-parameters.md      the Menu/Par config channel (U_/T_ codes ↔ app menu)
broker/
  make-cert.sh               generate the self-signed server cert
  mosquitto.conf             Mode A: fully local broker
  mosquitto-bridge.conf      Mode C: local broker bridged to the real cloud
custom_components/
  ariston_local/             Home Assistant integration (local MQTT, Menu/Par)
tools/
  kura_decode.py             stdlib Eurotech Kura/Kapua payload decoder
  kura_encode.py             stdlib Kura payload encoder (inverse of the decoder)
  kura_client.py             Mode A: send GET/PUT/EXEC, read the matching REPLY
  sweep_registers.py         Mode A: actively GET named resources (GET-only)
  read_menu.py               Mode A: read installer-menu params via Menu/Par
  write_menu.py              Mode A: write params (PUT Menu/Par), scaling-aware
  menu_params.py             decode U_/T_ menu codes + enrich (map in .json)
  menu_params.json           menu number → label/unit/scale/enum (from capture)
  analyze_capture.py         roll an mqtt_mitm log up into a command dictionary
  ebus_params.py             decode hex metric names into eBUS registers + enrich
  ebus_registers.json        extensible map of known eBUS registers → meaning
  test_kura.py               stdlib unit tests for the codec, analyzer + eBUS parser
  live_monitor.sh            subscribe to all topics, print decoded JSON
  mqtt_mitm.py               Mode B: transparent TLS proxy, logs every packet
  resolve_real.sh            resolve the real cloud IP past a DNS override
```

## Quick start

```bash
# 1. cert
cd broker && ./make-cert.sh && cd ..

# 2. capture the official app's commands (app stays online)
tools/resolve_real.sh
sudo python3 -I tools/mqtt_mitm.py \
  --cert broker/server.crt --key broker/server.key --log mitm.jsonl
#    ...then point the gateway's DNS at this host and power-cycle it (see docs/03)

# decode a raw payload at any time
python3 -I tools/kura_decode.py some-payload.bin
```

See [docs/03](docs/03-local-broker-and-bridge.md) for the DNS override, the three
modes, and safety notes (notably: never publish to the `DEPLOY-V2` firmware topic).

## Safety

`PUT` writes real setpoints on a real boiler. Prefer `GET` while mapping the
protocol, keep `server.key` out of git, and revert the DNS override if you take
the broker host down so the gateway can reach the cloud again.
