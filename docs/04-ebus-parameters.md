# 04 — eBUS parameter IDs

The gateway names its metrics with 5-byte hex strings (e.g. `106800FE01`). This
is how we decode them into meaningful quantities. It talks the **Ariston BUS
BridgeNet** protocol, which rides the **eBUS** link layer with a vendor-specific
application layer; the community has mapped many registers (see Sources).

## ID structure

Each 5-byte hex name splits as:

```
  10   68   00   FE   01
  └─┬────┘  │    │    │
    │       │    │    └─ zone / instance index (00, 01 seen)
    │       │    └────── 0xFE  = eBUS broadcast address
    │       └─────────── flag / sub-index (00 and 82 seen; meaning TBC)
    └─ register, little-endian 2 bytes
       byte-swap → community "eBUS ID"  (10 68 → 6810)
```

`tools/ebus_params.py` parses this and looks the register up in
`tools/ebus_registers.json`.

### How we know

Reading the first two bytes little-endian and byte-swapping them lands exactly on
registers the community has already identified:

| gateway metric | bytes | eBUS ID | meaning (community) | confidence |
|---|---|---|---|---|
| `106800FE01` | `10 68` | **6810** | heating flow temperature | matched |
| `106900FE01` | `10 69` | **6910** | heating return temperature | matched |
| `4BC000FE01` | `4b c0` | **c04b** | boiler status | matched |

Three independent matches on the same byte layout, plus byte 3 being `0xFE` (the
eBUS broadcast address), make the structure solid. The scaling convention
(temperatures as tenths, e.g. `450` = 45.0 °C) is the usual BridgeNet one but is
**not yet confirmed on this device** — confirm it by correlating a GET reply with
the value shown in the official app.

### Still open

- The **flag byte** (byte 2): `00` on the confirmed temperature/status reads,
  `82` on two log-sourced IDs (`900982FE00` → `0x0990`, `966982FE00` → `0x6996`)
  that don't match a named register. Likely a data-type or message-class marker;
  the `82` pair may be log/error event codes rather than register reads.
- Per-register **scaling and enum tables** (what each `boiler_status` value means).
- The full register set this specific model exposes.

Fill these in from captures — see below.

## Tooling

```bash
# explain one or more IDs
tools/ebus_params.py 106800FE01 4BC000FE01
# -> {"raw":"106800FE01","ebus_id":"6810","zone":1,...,"known":{"name":"heating_flow_temp",...}}

# dump the current register map
tools/ebus_params.py --list

# annotate a decoded capture: raw decode, then enrich with eBUS meanings
python3 -I tools/kura_decode.py capture.bin | python3 -I tools/ebus_params.py --enrich
```

The enrich step adds an `ebus` block to each recognized hex metric, including a
`scaled_value` where a scaling is known:

```json
{"metrics": {"106800FE01": {"type": "INT32", "value": 452,
  "ebus": {"ebus_id": "6810", "zone": 1, "name": "heating_flow_temp",
           "meaning": "Heating flow (outgoing CH) temperature",
           "unit": "C", "scaled_value": 45.2}}}}
```

## How a GET actually works (from a captured request)

A real GET captured from the cloud settles the request format:

```
topic    $EDC/ari/<MAC>/ar1/GET/Func/TProg
metrics  request.id            (STRING)
         requester.client.id   (STRING)
         Id = "1"              (STRING — selects the zone/instance)
```

So the device is read by **named resource** (e.g. `Func/TProg`), *not* by a
per-register hex ID. The hex register IDs decoded above are the **metrics inside
the reply bundle**: one GET to a resource returns many registers at once. The
device confirms this by subscribing to `$EDC/ari/<MAC>/ar1/GET/#` (plus `PUT/#`,
`EXEC/#`, `DEPLOY-V2/#`, `REPLY/#`) on connect.

Read one resource directly and enrich it:

```bash
tools/kura_client.py get Func/TProg -m Id:STRING=1 --cafile broker/server.crt \
    | tools/ebus_params.py --enrich
```

## Active resource sweep (Mode A)

Once the gateway is on your **local broker** (Mode A, docs/03), `tools/sweep_
registers.py` GETs a list of named resources, collects the bundled replies, and
annotates recognized registers. GET-only, rate-limited, **dry-run until `--send`**.

First learn the resource names: run the MITM, drive the app through every screen,
and watch `analyze_capture.py mitm.jsonl --follow` for the `+ GET` lines — each
one is a resource name. Put them in a file (one per line), then:

```bash
# preview (sends nothing)
tools/sweep_registers.py --resources Func/TProg,Func/Zone1

# sweep for real once the gateway is on the local broker
tools/sweep_registers.py --resources-file resources.txt \
    --cafile broker/server.crt --send --out sweep.jsonl
```

`--id N` sets the `Id` metric (default `"1"`); `--no-id` omits it. Each answered
resource prints its metrics with eBUS hints; unmapped registers in a reply are
new discoveries to add to the map below. (A legacy register mode —
`--known`/`--ids`/`--range` → `GET {hexname}` — remains for the unlikely case a
resource turns out to be register-addressed.)

## Device (from the BIRTH certificate)

The gateway's `MQTT/BIRTH` message identifies it:

| field | value |
|---|---|
| `display_name` | `REM4_OT_ZB` (REM4 OpenTherm + Zigbee gateway) |
| `serial_number` | `292221G20182` |
| `firmware_version` / `kura_version` | `105.15.15b` (Eurotech ESF/Kura 105) |
| `connection_interface` | `9C05D613C8AD-Octarine` |
| `custom_attribute2` (ZB_FwVer) | `10.00.26` (Zigbee firmware) |
| `application_ids` | `DEPLOY-V2` |

## Extending the map

`tools/ebus_registers.json` is keyed by the community eBUS ID (the byte-swapped
form). To confirm or add a register:

1. Capture the official app with the MITM proxy (docs/03) and roll it up:
   `tools/analyze_capture.py mitm.jsonl` — this lists every metric name and the
   values seen per GET/PUT resource.
2. Note the value the app displays for that parameter at the same moment; divide
   to find the scale; add a `{name, meaning, unit, scale, confidence}` entry.
3. Re-run the enrich step to see it applied.

Keep `confidence` honest: `observed-on-device` once you've matched it against the
app on this unit, `community` for an unverified map import.

## Sources

Community reverse-engineering of Ariston/Chaffoteaux BridgeNet over eBUS:

- [kredow/ariston-ebus-prococol](https://github.com/kredow/ariston-ebus-prococol) — Ariston BridgeNet register findings and command IDs.
- [ysard/ebusd_configuration_chaffoteaux_bridgenet](https://github.com/ysard/ebusd_configuration_chaffoteaux_bridgenet) — ebusd config for Chaffoteaux (same BridgeNet family).
- [wrongisthenewright/ebusd-configuration-ariston-bridgenet](https://github.com/wrongisthenewright/ebusd-configuration-ariston-bridgenet) — Ariston-specific ebusd config.
- [Exploring Ariston BUS BridgeNet Protocol (elektroda.com)](https://www.elektroda.com/rtvforum/topic3415927.html) — forum thread with register IDs (6810, 6910, 6210, c04b).
- [Ariston Group integration via eBusd (Home Assistant Community)](https://community.home-assistant.io/t/ariston-group-integration-via-ebusd/738887) — HA/MQTT bridging discussion.

Register tables are **model-dependent** — treat imported entries as hypotheses
until matched against this device.
