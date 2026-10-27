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

## Writing parameters — not yet done

Setpoint changes almost certainly go through a **PUT** to `Menu/Par` with the
code and new value, but no PUT has been captured yet, so the exact write shape is
unconfirmed. Capture one setpoint change in the app (MITM + `analyze_capture.py
--follow`) before attempting a write. `kura_client.py put` already refuses
`DEPLOY-V2` and confirms before sending; treat the first real PUT with caution and
know how to revert it from the boiler panel.

## Other resources seen

Besides `Menu/Par`, the app uses: `Func/TProg` (time program, takes `Id`),
`Func/Der` (takes `Z` = zone), `Func/SpecF` (special functions), and `Stat/cWh`
(energy stats, takes `Typ`). These return their own metric bundles; map them the
same way — capture, correlate, extend.
