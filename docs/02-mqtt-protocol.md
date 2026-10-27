# 02 — MQTT protocol

What the gateway speaks once it connects to the (local or cloud) broker.

## Connection

- Transport: MQTT 3.1.1 over TLS 1.2, cipher `AES256-GCM-SHA384`.
- Client ID: the MAC, `AABBCCDDEEFF`.
- Username: `AABBCCDDEEFF-XXXXXXXXXX`. (Password present in CONNECT; see
  [03](03-local-broker-and-bridge.md) for recovering it.)
- Keepalive: 30 s (PINGREQ).
- **The gateway does not validate the broker's TLS certificate.**

## Topic namespace (Eurotech Kapua / Kura)

Account = `ari`, asset/client = `<MAC>` = `AABBCCDDEEFF`, application = `ar1`.

| Direction | Topic | Purpose |
|---|---|---|
| device → broker | `$EDC/ari/<MAC>/MQTT/BIRTH` | birth certificate (capabilities), ~496 B |
| device → broker | `$EDC/ari/<MAC>/MQTT/LWT`   | last-will (published on ungraceful disconnect) |
| device → broker | `$EDC/ari/<MAC>/ar1/REPLY/#` | responses to GET/PUT/EXEC requests |
| device → broker | `ari/<MAC>/ar1/Log/log`      | event log entries |
| device → broker | `ari/<MAC>/ar1/Err/ErrListRst` | error list |
| device → broker | `ari/<MAC>/Debug/wifi`       | wifi/ebus debug status |
| broker → device | `$EDC/ari/<MAC>/ar1/GET/#`   | read requests (cloud → device) |
| broker → device | `$EDC/ari/<MAC>/ar1/PUT/#`   | **writes / setpoints** |
| broker → device | `$EDC/ari/<MAC>/ar1/EXEC/#`  | exec commands |
| broker → device | `$EDC/ari/<MAC>/DEPLOY-V2/#` | firmware deployment — **do not publish here** |

On connect the device SUBSCRIBEs to the four `$EDC/.../{GET,PUT,EXEC,DEPLOY-V2}`
topics and publishes BIRTH, then an initial burst of log/error/debug telemetry.
Steady-state traffic is sparse and **event-driven** (plus keepalive pings), so to
learn the command vocabulary you must watch the app drive it — see the MITM proxy
in [03](03-local-broker-and-bridge.md).

> Note: the MQTT `#` wildcard does **not** match topics beginning with `$`, so
> subscribe to `$EDC/#` explicitly to see the control-plane topics.

## Payload format: Kura protobuf

Payloads are Google-Protobuf `KuraPayload` messages (not gzipped on this device;
the decoder handles gzip too, just in case):

```
KuraPayload {
  1:    timestamp (ms since epoch; varint here, sfixed64 in some variants)
  2:    position (GPS; unused here)
  5000: repeated KuraMetric
  5001: body (opaque bytes)
}
KuraMetric {
  1: name   (string)
  2: type   (enum: 0 DOUBLE 1 FLOAT 2 INT64 3 INT32 4 BOOL 5 STRING 6 BYTES)
  3..9: value field, selected by type
}
```

Firmware quirk: integer metrics are sent as plain varints (wire type 0) rather
than the schema's `sfixed32`. `tools/kura_decode.py` interprets the value by the
declared `type` and the actual wire type, so both forms decode correctly.
Negative integers follow protobuf's rule — sign-extended to a full 64-bit
two's-complement varint even for `INT32` — so the decoder reads the varint as a
signed 64-bit value. `tools/kura_encode.py` is the exact inverse and
round-trips every type (see `tools/test_kura.py`).

### Real decoded example

Raw payload (28 bytes) on `ari/AABBCCDDEEFF/ar1/Log/log`:

```
08 f8 9f df e8 97 34  c2 b8 02  10  0a 0a 39 30 30 39 38 32 46 45 30 30 10 03 30 00
```

Decoded by `kura_decode.py`:

```json
{"timestamp": 1793100075000, "metrics": {"900982FE00": {"type": "INT32", "value": 0}}}
```

### Observed metric names (incomplete — to be expanded)

- `ar1/Err/ErrListRst`: `N`, `T`, `FIdx`, `Mult`, `Own`
- `Debug/wifi`: `StatusCode`, `EventCode`, `T`, `ARES Code`, `EbusRST`
- `ar1/Log/log`: hex-coded parameter IDs, e.g. `900982FE00`, `106800FE01`,
  `4BC000FE01`, `966982FE00`, `106900FE01`

The hex-coded names look like Ariston/eBUS register identifiers. Mapping them to
meaningful quantities (temperatures, modes, setpoints) is the next reverse-
engineering step and is best done by correlating with the app's GET/PUT traffic.
