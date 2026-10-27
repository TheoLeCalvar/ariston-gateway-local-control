# 06 — Roadmap: override, holidays, energy

Implementation plan for the features in [`TODO.md`](../TODO.md). Each one has a
**native** path (drive a function the device already has) and an **HA-side** path
(do it with the setpoint writes we already have). Native is preferred — it shows
on the thermostat, auto-reverts on the device, and survives a Home Assistant
restart — but each native function still needs one targeted capture to map,
because we only caught them idle (see below).

## What the capture already tells us

From `mitm.jsonl`, correlating requests to replies:

| resource | request params | reply (captured) | reading |
|---|---|---|---|
| `Func/Der`   | `Z` (zone) | `response.code` only | **dérogation** = temporary override; empty = none active |
| `Func/SpecF` | — | `Mode=1, T=0` | **special function** (holiday/absence); Mode selects it, T a time/temp |
| `Stat/cWh`   | `Typ=6,20,9,21` | timestamp + **data in the body** | **energy stats**; decoded (see §3) — the values are there |

So the protocol and plumbing are done; what's missing is the *content* of these
three functions when active. One capture each (feature on, in the app, via the
MITM + `analyze_capture.py --follow`) fills that in.

A shared prerequisite: `gateway.py` currently exposes only `Menu/Par` read/write.
These features use other resources (`Func/Der`, `Func/SpecF`, `Stat/cWh`) with
their own request params (`Z`, `Typ`, …) and reply bundles, so first **expose the
generic `_request(verb, resource, metrics)`** as a public `request()` and add thin
helpers per resource. Small, low-risk refactor.

---

## 1. Temporary temperature override — DONE (native `Func/Der`)

**Confirmed format** (data in the body, field 5001), one entry per zone:

```
entry { 1: zone, 2: enable(1/0), 3: temp*10, 4: end-epoch (absolute seconds) }
```

Captured write of "zone 1, 18 °C": body `0a0d 0801 1001 18b401 20a0cd83d706`
(zone=1, enable=1, temp=180, end=1793124000) — reproduced byte-for-byte. The GET
(metric `Z=<zone>`) reads it back in the same shape; the device reverts on its own
at the end epoch (standard Unix time, so `end = now + duration`).

**Implemented** (`custom_components/ariston_local/`):
- `override.py`: build/parse the Func/Der body.
- `gateway.read_override / set_override / clear_override` (PUT carries a body now).
- `AristonOverrideCoordinator` + a **binary_sensor** per zone ("Zone N override
  active", with `target_temperature` and `until` attributes).
- HA **services** `ariston_local.set_override` (zone, temperature, duration=2h) and
  `ariston_local.clear_override` (see `services.yaml`).

Remaining: a `clear` capture to confirm enable=0 is the right disable (we infer
it); and a `climate` `PRESET_BOOST` once the climate entity exists.

## 2. Holidays / absence mode

Hold a fixed (low) setpoint until a chosen date.

**Native (preferred) — `Func/SpecF`.** Capture: enable holiday/absence mode with an
end date in the app; diff `Mode` and `T` against the idle `Mode=1, T=0`. Likely
`Mode=<holiday>` and `T=<end-date or days>`, possibly with a setpoint. Then:
- `gateway.set_special_function(mode, until)` / clear,
- expose a **switch** "Holiday mode" + a **datetime** entity for the end date (or a
  single service `ariston_local.set_holiday(until, temperature=None)`).

**HA-side fallback.** Service writes a fixed eco setpoint to all zones (or sets
each zone mode) and schedules restoration at the date; same persistence handling
as override.

**Decision:** prefer `Func/SpecF`; it's the device's own absence function.

## 3. Energy / gas consumption (kWh + cost)

**The data is available locally and decoded.** It is NOT in the reply metrics
(which is why it looked empty at first) — it is in the KuraPayload **body**
(field 5001), a nested protobuf of repeated entries `{1: idx, 2: Typ, 3: packed
varint values}`. `tools/stat_decode.py` decodes it; `tools/stat_decode.py --mitm
mitm.jsonl` dumps every series from a capture.

A GET to `Stat/cWh` requests a `Typ` set (observed `6, 9, 20, 21`); the reply
returns, per Typ, one series per `idx`. The idx is a period granularity (by series
length): **idx=1 → 24 (hourly/today)**, **idx=4 → 13 (monthly/year)**, idx=2 → 20
(window TBC). Decoded sample (from the capture):

```
Typ= 6 idx=4 [monthly]  [0,124652,487552,727825,756030,497308,50028,5850,0,0,0,0,0]
                        sum ≈ 2 649 000  -> ~2649 kWh if Wh (winter-peaked)
Typ= 9 idx=4 [monthly]  [37963,38358,50675,56911,...]  (year-round, non-seasonal)
Typ=20 idx=4 [monthly]  [0,694,2722,3877,4025,2705,258,30,0,...]
Typ=21 idx=4 [monthly]  [197,219,272,288,230,297,120,266,211,127,50,58,127]
```

**Confirmed against the app's energy screen** (category GAZ, split Chauffage / Eau
chaude sanitaire):

| Typ | quantity | unit | check |
|---|---|---|---|
| **6**  | **Heating** (chauffage) | Wh | `idx=2` (this month) sums to **4100** = app's Oct **4.1 kWh** ✓ |
| **9**  | **DHW** (eau chaude sanitaire) | Wh | `idx=4` ≈ **456 kWh** ≈ app annual DHW ✓ |
| 20 | heating burner runtime | min? | ≈ Typ6 ÷ 188 (≈ Wh/min at modulated power) — hypothesis |
| 21 | DHW burner runtime | min? | ≈ Typ9 ÷ 188 — hypothesis |

Periods: **idx=1** hourly (today), **idx=2** daily (this month), **idx=4** monthly
(trailing ~13 months). Values are gas energy in **Wh**.

**Implemented** (`custom_components/ariston_local/`):
- `energy.py`: ported Stat/cWh body decoder → `{(typ, idx): [Wh]}`.
- `gateway.read_stats()` (GET `Stat/cWh`, `Typ=6,9`) + a slow `AristonStatsCoordinator`.
- 4 energy **sensors** (kWh, `device_class=energy`, `state_class=total_increasing`):
  heating/DHW this month (sum of daily idx=2) and trailing year (sum of idx=4) —
  these feed the **HA Energy dashboard**.
- **cost** stays in HA: the Energy dashboard applies the tariff; the integration
  provides kWh only (tariff-agnostic). The app's own € toggle confirms it's just a
  price multiplier.

Remaining: confirm the Typ 20/21 runtime hypothesis, and verify the `total_
increasing` reset behaviour at month/year boundaries against a long run.

---

## Suggested order

1. **Energy** — read-only, lowest risk, high everyday value; good first capture.
2. **Temporary override** — highest control value; native `Func/Der`.
3. **Holidays** — reuses the override/service + scheduling patterns.

Each lands as: capture → extend `menu_params`/a new resource map → gateway helper →
HA entity/service → pure-Python protocol test. Keep writes behind the existing
safety rails (U_ codes only, no `DEPLOY-V2`, response.code check).
