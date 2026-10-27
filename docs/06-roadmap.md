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

## 1. Temporary temperature override

Change a zone to a target for N hours (default 2), then revert.

**Native (preferred) — `Func/Der`.** Capture: in the app, set a temporary comfort
override on zone 1 for 2 h, grab the `PUT Func/Der` (and the `GET` reply once
active). Expected fields: target temperature, duration or end-time, zone `Z`. Then:
- add `gateway.set_derogation(zone, temp, duration)` / `clear_derogation(zone)`,
- expose an HA **service** `ariston_local.temporary_override(zone, temperature,
  duration="2:00")`, and surface the active override as a read-only sensor
  (remaining time / target).
- Bonus: model it as a **climate preset** (`PRESET_BOOST`) once the climate entity
  exists.

**HA-side fallback (works today).** Service that: reads the zone's current comfort
setpoint, writes the new one, and schedules the revert with
`async_track_point_in_time`. Persist the pending revert (entry runtime data + a
restored `datetime`) so an HA restart during the window still reverts. Downsides:
nothing shown on the thermostat; revert logic lives in HA.

**Decision:** use `Func/Der` if the capture shows a clean temp+duration write;
otherwise ship the HA-side timer.

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

Strong hypothesis: **Typ=6 = central-heating energy in Wh** (the winter-peaked
series sums to a plausible ~2649 kWh/yr). Typ 9/20/21 are likely DHW energy, gas
input, and burner starts/hours in some order — **unconfirmed**. (This REM4 is an
OpenTherm gateway, so any "gas" figure is the boiler's own estimate.)

**Still needed — one correlation, not a new capture:** read the app's energy
screen and match its displayed categories + numbers to the decoded series, to pin
each `Typ`, its unit, and what `idx` periods are.

**Implement (once Typ semantics are known):**
- `gateway.read_stats(typs)` (GET `Stat/cWh`, `Typ` list), decode the body with the
  ported stat decoder, polled on a **slow** interval (e.g. hourly),
- the arrays are per-period buckets, not a running counter. Expose, per quantity:
  "this month" (current monthly bucket) and "this year" (sum of the monthly
  series) as **sensor**s. For the **HA Energy dashboard** (which wants a
  monotonic `total_increasing`), expose the year-to-date cumulative sum with a
  `last_reset` at the year boundary, or let a `utility_meter` helper accumulate.
- **cost** stays in HA: the Energy dashboard applies the tariff; the integration
  provides kWh only (tariff-agnostic).

---

## Suggested order

1. **Energy** — read-only, lowest risk, high everyday value; good first capture.
2. **Temporary override** — highest control value; native `Func/Der`.
3. **Holidays** — reuses the override/service + scheduling patterns.

Each lands as: capture → extend `menu_params`/a new resource map → gateway helper →
HA entity/service → pure-Python protocol test. Keep writes behind the existing
safety rails (U_ codes only, no `DEPLOY-V2`, response.code check).
