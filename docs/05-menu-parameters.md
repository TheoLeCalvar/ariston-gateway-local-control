# 05 — Installer-menu parameters (the real config channel)

This is how the app actually reads and writes settings — and the most useful
mapping for a Home Assistant integration. It was worked out by capturing the app
with the MITM proxy (docs/03) and matching the values against the **web installer
menu** (the numbered tree: `6.3.0.0 Température départ zone 1`, etc.).

## The `Menu/Par` resource

Config parameters are **not** addressed by the hex eBUS IDs (those are in docs/04
and show up inside telemetry/log bundles). They go through one resource:

```
GET  $EDC/ari/<MAC>/ar1/Menu/Par
```

carrying up to **16 parameter codes** as metrics `P1`…`P16` (STRING), e.g.
`P1 = "U_6.16.0"`. The reply returns each code with its integer value plus a
`response.code` (200 = OK).

### Code format

```
  [C_] <U|T> _ <menu-number>
   │     │        └─ the installer menu number: 6.16.0, 6.3.1.0.0, 3, 0.0 …
   │     └────────── U = user parameter (the value shown in the app)
   │                 T = technical / sensor read
   └──────────────── optional "C_" variant (returns the same value here)
```

So `U_6.16.0` is the user value of menu **6.16.0**, `T_8.4.0` a technical read of
menu **8.4.0**, `C_U_6.9.0` the `C_` variant of `U_6.9.0`.

### Scaling and values

- **Temperatures are ×10**: raw `185` = 18.5 °C, `450` = 45.0 °C, `500` = 50.0 °C.
  Confirmed by matching the capture to the installer menu.
- **Toggles are enums**: `0` = Arrêt/Désactivée, `1` = Marche; mode `3` =
  "Chauffage programmé"; `U_3` (Eté/Hiver/OFF) `1` = Hiver.
- **`32767` (0x7FFF) is the not-available sentinel.**
- A few are plain integers (`U_6.16.5` signal strength = 70) or other units
  (`U_6.9.5.1` anti-legionella frequency: raw 28800 → 480 h, i.e. ÷60).

`tools/menu_params.json` holds the map (menu number → label, French label, unit,
scale, enum), seeded from the capture + screenshot. Extend it as you confirm more.

## Reading parameters (Mode A)

With the gateway on the local broker (Mode A, docs/03):

```bash
# decode/explain a code
tools/menu_params.py U_6.3.1.0.0 C_U_6.9.0
tools/menu_params.py --list                 # the whole map

# read specific parameters (dry-run first, then --send)
tools/read_menu.py U_6.3.1.0.0 U_6.9.0 --cafile broker/server.crt --send

# read every mapped parameter, batched 16 per Menu/Par GET
tools/read_menu.py --all --cafile broker/server.crt --send --out menu.jsonl

# or do it by hand and enrich the reply
tools/kura_client.py get Menu/Par -m P1:STRING=U_6.9.0 --cafile broker/server.crt \
    | tools/menu_params.py --enrich
```

`read_menu.py` prints each code with its label and decoded value, e.g.:

```
    U_6.3.1.0.0    = 18.5 C    (Comfort setpoint zone 1)
    U_6.9.0        = 45.0 C    (DHW comfort setpoint)
    U_3            = Hiver      (Summer/Winter/OFF)
```

## Writing parameters (PUT Menu/Par)

A captured write settles the format. The **installer web portal** (not the main
consumer page) issues writes; a change is a PUT to the same resource:

```
PUT  $EDC/ari/<MAC>/ar1/PUT/Menu/Par
metric   <code> = <value>     (INT32 — the code is the metric NAME, value direct)
         request.id, requester.client.id
```

Note the asymmetry with reads: a GET wraps codes as `P1 = "U_3"`, but a **PUT
names the metric after the code** — `U_3 = 1`. One PUT per parameter, no
timestamp (matching the cloud). Example captured: `U_3 = 1` sets Eté/Hiver/OFF to
Hiver; the raw packet is exactly `c2b802090a03555f3310033001` plus the request
metrics, which `kura_encode.encode_payload({"U_3": ("INT32", 1)}, timestamp=False)`
reproduces byte-for-byte.

Mode `3` (Eté/Hiver/OFF) was observed to accept values **0, 1, 2, 5** (only
`1 = Hiver` is label-confirmed; set each and read the app to label the rest).

### Writing with the tool

`tools/write_menu.py` takes human values, applies the scaling, and PUTs one
parameter at a time. **It changes real settings on a real boiler**, so it is
dry-run until `--send` and confirms each write.

```bash
# preview (sends nothing): shows code, raw value, scaling
tools/write_menu.py 6.3.1.0.0=19.5 3=1

# write for real (gateway on the local broker; prompts to confirm), then read back
tools/write_menu.py 6.3.1.0.0=19.5 --cafile broker/server.crt --send --verify
```

It writes only `U_` codes, refuses `DEPLOY-V2`, and warns when a value is outside
a parameter's known settable set. The raw path is also available directly:
`kura_client.py put Menu/Par -m U_3:INT32=1 --cafile broker/server.crt`.

Treat the first real write with care and know how to revert it from the boiler
panel.

## Confirmed enum maps (from the installer capture)

Cycling each option in the installer portal and correlating the PUT value with
the `REPLY` id gives these byte-for-byte mappings:

| code | raw → label |
|---|---|
| `U_3` (Été/Hiver/OFF) | 0 = Été, 1 = Hiver, 2 = Chauffage seul, 5 = Arrêt |
| `U_0.0/0.1/0.2` (zone mode) | 2 = Manuel, 3 = Chauffage programmé |
| `U_0.6` (position sanitaire) | 0 = Désactivé, 1 = Temporisé, 2 = Toujours active |
| `U_6.9.2` (fonction COMFORT) | 0 = Désactivée, 1 = Temporisé, 2 = Toujours active |

## Live technical reads (`T_`)

The same capture, read while idle and during an override, pins down several live
measurements (×10, read via `T_<menu>`):

| code | observed | reading |
|---|---|---|
| `T_4.3.0` | 20.1–20.3 °C, drifting | **zone 1 room temperature** |
| `T_4.3.1` | 15.5 → **18.0** when the 18° override fired | **zone 1 active setpoint** |
| `T_8.4.0` | 39.9–42.2 °C, moving | **boiler flow temperature** |
| `T_8.3.0` | 50.0 °C (steady) | DHW temperature/setpoint (unconfirmed) |
| `T_8.3.5` | 32767 | DHW tank temp — no sensor (combi), N/A sentinel |

`T_4.3.0` + `T_4.3.1` are the room-temperature and current-target pair a per-zone
`climate` entity needs; the override's effect being visible on `T_4.3.1` confirms
the two are the live view of the same zone.

## Time program (`Func/TProg`) — read decoded

The weekly heating schedule. `GET Func/TProg` with metric `Id` (STRING) returns
the program in the body (field 5001). Observed ids: `1` (zone 1 heating —
weekday + weekend profiles, confirmed by the owner) and `7`. Structure:

```
program { 1:id  2:slot_minutes  3:profile{ 1:{} 2:{1:slot,2:state} … } …  4:comfort*10  5:eco*10 }
```

`slot_minutes` is 15, and a transition `{slot, state}` is `slot × 15 min` →
time-of-day with `state` 1=comfort, 0=eco. Decoded sample (id 1, comfort 18.5 /
eco 15.5): profile A = comfort 07:00–23:00; profile B = 06:30–08:30 + 17:30–23:30.

`tprog.decode_tprog_body()` folds this into comfort windows; the HA integration
exposes each program as a read-only diagnostic sensor. **Writing is not
implemented** — only reads are captured, so the PUT body shape and the
day→profile mapping (the empty `profile.1`) are unconfirmed. Capture one schedule
edit in the app to resolve both before adding a write path.

## Other resources seen

Besides `Menu/Par`, the app uses: `Func/TProg` (time program, takes `Id`),
`Func/Der` (takes `Z` = zone), `Func/SpecF` (special functions), and `Stat/cWh`
(energy stats, takes `Typ`). These return their own metric bundles; map them the
same way — capture, correlate, extend.
