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
| `Stat/cWh`   | `Typ=6,20,9,21` | `T=<timestamp>` only | **energy stats**; Typ selects a counter — captured idle |

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

**Source — `Stat/cWh`** with `Typ` selecting a counter (observed `6, 20, 9, 21`).
Capture: open the app's stats/energy screen and record the full `Stat/cWh` replies;
map each `Typ` to a quantity (likely heating vs DHW, gas vs electrical, per period)
and its unit/scaling. (This REM4 is an OpenTherm gateway, so "gas" is the boiler's
own estimate, not a meter.)

**Implement:**
- add `gateway.read_stats(typs)` (GET `Stat/cWh`, `Typ` list), polled on a **slow**
  interval (e.g. hourly) via a second coordinator or a throttled update,
- expose **sensor** entities with `device_class=energy`, `state_class=
  total_increasing`, `unit=kWh` so they feed the **HA Energy dashboard**,
- **cost** is left to HA: the Energy dashboard applies a tariff, or a user template
  sensor multiplies kWh by a price. The integration provides kWh only (keeps it
  tariff-agnostic).

If the counters turn out to be period totals (not monotonic), expose them as plain
measurement sensors and let a utility-meter helper accumulate.

---

## Suggested order

1. **Energy** — read-only, lowest risk, high everyday value; good first capture.
2. **Temporary override** — highest control value; native `Func/Der`.
3. **Holidays** — reuses the override/service + scheduling patterns.

Each lands as: capture → extend `menu_params`/a new resource map → gateway helper →
HA entity/service → pure-Python protocol test. Keep writes behind the existing
safety rails (U_ codes only, no `DEPLOY-V2`, response.code check).
