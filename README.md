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
- ✅ Tooling: stdlib payload decoder, live monitor, transparent MITM proxy, bridge config.
- ⬜ Build the command dictionary (`GET`/`PUT`/`EXEC` resources) by capturing the official app via the MITM proxy.
- ⬜ Map the hex-coded eBUS parameter IDs to meaningful quantities.
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
broker/
  make-cert.sh               generate the self-signed server cert
  mosquitto.conf             Mode A: fully local broker
  mosquitto-bridge.conf      Mode C: local broker bridged to the real cloud
tools/
  kura_decode.py             stdlib Eurotech Kura/Kapua payload decoder
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
