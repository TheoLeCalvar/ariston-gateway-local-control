# Ariston schedule card

A dependency-free Lovelace card that shows one heating time program
(`Func/TProg`) as a weekly **comfort / eco** overview and edits it, writing back
to the boiler through the `ariston_local.set_time_program` service.

It is **local-first and boiler-owned**: the card never stores the schedule. It
renders whatever the integration last read from the boiler (the *Time program*
sensor) and, on **Save**, pushes your changes down. Edit the schedule on the
thermostat itself and the card catches up on the next poll (≈5 min). If HA is
off, the boiler keeps running its own schedule — nothing here changes that.

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
   title: Programme chauffage   # optional
   show_setpoints: true         # optional, see below
   ```

   `entity` is the *Time program* sensor the integration creates (one per
   `Func/TProg` id). `program_id` defaults to the entity id's trailing number;
   set it explicitly if yours differs. Add a second card for program 7.

   **Updating:** after copying a new version over the old file, browsers may
   keep the cached one — reload with cache bypass, or bump the resource URL
   (`/local/ariston-schedule-card.js?v=2`).

## Using it

- **Overview** — one bar per day: comfort periods in the heating colour, eco
  as the faint background, with the times written underneath. Today is
  highlighted with a "now" marker, and the header says what's running
  ("Confort jusqu'à 08:30" / "Éco jusqu'à 17:30").
- **Edit a day** — tap it. Each comfort period is a start → end pair of time
  pickers (15-minute steps, touch-friendly); ✕ removes one, **Ajouter une
  plage** adds 2 h in the day's largest free gap. Overlapping periods merge
  when you leave the field.
- **Copy** the open day to Mon–Fri, the weekend, the whole week, or a single
  day.
- **Setpoints** — comfort/eco steppers (0.5 °C) for the program's
  temperatures. Set `show_setpoints: false` to hide them, e.g. when the zone's
  comfort/eco number entities sit next to the card.
- **Save / Cancel** appear only once something changed (edited days get a dot).
  Save calls `set_time_program` (read-modify-write: the integration reads the
  current program, applies your week, writes it back and re-reads it); the card
  keeps showing your version until the boiler reports it, then confirms. If the
  schedule changes on the boiler while you edit, the card says so: Save
  overwrites it, Cancel reloads the boiler's version.

Text follows the HA language (French or English).

## Limits

- Periods end at **23:45** at the latest: a period running to midnight would
  need a slot-96 transition that hasn't been confirmed on hardware.
- The card edits the 7-day week as a whole; days with identical periods are
  sent as one profile, as the boiler stores them.

For scripted changes, call the service directly:

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
