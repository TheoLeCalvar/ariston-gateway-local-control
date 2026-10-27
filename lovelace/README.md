# Ariston schedule card

A dependency-free Lovelace card that shows one heating time program
(`Func/TProg`) as a weekly **comfort / eco grid** and writes edits back to the
boiler through the `ariston_local.set_time_program` service.

It is **local-first and boiler-owned**: the card never stores the schedule. It
renders whatever the integration last read from the boiler (the *Time program*
sensor) and, on **Save**, pushes your changes down. Edit the schedule on the
thermostat itself and the card catches up on the next poll (≈5 min). If HA is
off, the boiler keeps running its own schedule — nothing here changes that.

![grid: shaded = comfort, blank = eco, click/drag to edit]

## Install

1. Copy `ariston-schedule-card.js` into `config/www/` (so it is served at
   `/local/ariston-schedule-card.js`).
2. **Settings → Dashboards → ⋮ → Resources → Add resource**
   - URL: `/local/ariston-schedule-card.js`
   - Type: **JavaScript module**
   (Or, in YAML mode, add it under `lovelace.resources`.)
3. Add the card to a dashboard (manual card):

   ```yaml
   type: custom:ariston-schedule-card
   entity: sensor.ariston_gateway_aabbccddeeff_time_program_1
   program_id: 1
   title: Heating schedule — zone 1
   ```

   `entity` is the *Time program* diagnostic sensor the integration creates (one
   per `Func/TProg` id). `program_id` is that id (defaults to the trailing number
   of the entity id if omitted). Add a second card with `program_id: 7` for the
   other program.

## Using it

- **Shaded** cell = comfort period, **blank** = eco. Click a cell to toggle, or
  click-and-drag to paint a range.
- Set **Comfort °C / Eco °C** for the program.
- **Save to boiler** calls `set_time_program`: the integration reads the current
  program, applies your grid, and writes it back (read-modify-write, so it never
  pushes a stale copy), then re-reads to confirm it landed.
- **Revert** discards unsaved edits and re-syncs from the boiler.

## Resolution

The grid is **30-minute** (48 cells/day) — real Ariston schedules sit on
`:00`/`:30` boundaries, so this covers normal use. The underlying program and
service support 15-minute slots; for a finer edit, call the service directly:

```yaml
service: ariston_local.set_time_program
data:
  id: 1
  comfort: 20
  eco: 16
  profiles:
    - days: [1, 2, 3, 4, 5]
      windows: [["06:45", "08:30"], ["17:30", "23:30"]]
    - days: [6, 7]
      windows: [["07:00", "23:00"]]
```

## Status

A functional starting point, not a polished HACS card: plain `HTMLElement`, no
build step. Extend as needed (per-day copy, presets, a 15-min toggle).
