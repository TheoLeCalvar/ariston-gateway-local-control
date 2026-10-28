# Changelog

All notable changes to the `ariston_local` Home Assistant integration. Versions
follow the `manifest.json` version. Format based on
[Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]
### Changed
- The repo's CLI tools (`tools/`) now import the integration's protocol modules
  (`kura`, `menu`, `energy`, `tprog`, and `menu_params.json`) instead of keeping
  their own copies. The Kura decoder now also surfaces a payload's `position`
  field, and `energy` gains the lossless `decode_stat_series()` /
  `decode_stat_payload()` views the tools print.

## [0.14.0] - 2026-10-28
Internal restructuring; entities and their ids are unchanged.
### Fixed
- A failed first poll during setup no longer leaks the MQTT connection: HA
  retries setup with a fresh client, and the previous one (plus its network
  thread) is now disconnected instead of piling up on every retry.
### Changed
- Per-entry state is a typed `entry.runtime_data` object (`runtime.py`) instead
  of an untyped `hass.data` dict, and it owns the single write path for
  override / holiday / time-program actions — the climate, buttons, datetime
  and services all share it (each write is checked for the device's
  `response.code` and refreshes what it changed).
- One generic polling coordinator replaces five near-identical ones; poll
  intervals are named constants.
- Services are registered once at integration setup (not per entry), and now
  report an error if no gateway is loaded instead of silently doing nothing.
- Setting or clearing an override also refreshes the zone's live setpoint, so
  the climate card updates right away whichever way the hold was set.
- Requires Home Assistant 2025.1 or newer.
### Added
- HA-level test suite (`tests/`, pytest-homeassistant-custom-component) that
  sets the integration up against a fake gateway: pins every entity unique id
  and covers each write path, unload, and setup retry.

## [0.13.2] - 2026-10-28
### Changed
- Energy sensors now use a **flame icon** (`mdi:fire`) to reflect the gas boiler.

## [0.13.1] - 2026-10-28
### Changed
- Renamed the integration from "Ariston Local (Menu/Par)" to just
  **"Ariston Local"** (manifest + HACS display name).

## [0.13.0] - 2026-10-28
### Added
- **Lifetime energy totals** ("Heating energy total" / "DHW energy total"):
  monotonic `total_increasing` kWh counters that only ever rise, built by
  folding the this-month accumulator into a stored running total (persisted
  across restarts). These are the sensors to add to the HA **Energy dashboard**.
### Changed
- The "this month" and "trailing year" energy sensors are now plain
  **diagnostic read-outs** (no longer `total_increasing`). The trailing-year
  figure is a rolling window whose sum can decrease as old months roll off,
  which the Energy dashboard mis-read as a counter reset — it no longer feeds
  statistics. Use the new lifetime totals for the dashboard.

## [0.12.0] - 2026-10-28
### Added
- Holiday mode is editable from the UI without a custom card: a **"Holiday
  until" datetime** entity (set it to enable holiday until that moment via
  `Func/SpecF`; it reads back the active end) and a **"Clear holiday" button**.
- Temporary override is UI-adjustable: an **"Override duration" number** (hours,
  restored across restarts) that the climate card's temperature change uses as
  its hold length, plus per-zone **"Zone N clear override" buttons**.
### Changed
- Switching a zone's climate back to **Auto** (Chauffage programmé) now also
  clears any active override, so the zone resumes its schedule immediately.

## [0.11.1] - 2026-10-27
### Fixed
- Switch platform crashed on setup (`AristonEntity.__init__() missing 'name'`);
  the anti-legionella / internet-time / internet-weather switches now load.

## [0.11.0] - 2026-10-27
### Added
- **Active-zones** option in the integration's Options: zone 1 is always on;
  zones 2 and 3 can be turned off on a single-zone system, which removes their
  setpoint/flow entities and stops polling their codes.

## [0.10.0] - 2026-10-27
### Added
- **Local-first weekly schedule**: the `Func/TProg` poll is now frequent (5 min)
  so a schedule edited on the thermostat shows up in HA; the time-program sensor
  exposes a legible `schedule` attribute (e.g. `Mon–Fri: 06:30–08:30`).
- A dependency-free **weekly comfort/eco grid Lovelace card** (`lovelace/`) that
  reads the sensor and writes edits back via `set_time_program`.
### Changed
- `set_time_program` reads the program back after writing and warns if it differs
  (a concurrent change on the boiler — the boiler's copy wins).
- Collapsed the two duplicate Wi-Fi sensors into a single "Wi-Fi signal".

## [0.9.0] - 2026-10-27
### Added
- Native **holiday / absence mode** (`Func/SpecF`): services `set_holiday` /
  `clear_holiday` and a "Holiday mode" binary sensor with an `until` attribute.

## [0.8.1] - 2026-10-27
### Added
- **Heating water pressure** sensor (`T_8.2.9`, bar).

## [0.8.0] - 2026-10-27
### Added
- Sensors identified by app correlation: burner **flame** (binary), **Wi-Fi link
  quality**, and the **heating flow setpoint**.

## [0.7.0] - 2026-10-27
### Added
- `set_time_program` service to **write** a weekly schedule (`Func/TProg`),
  confirmed on hardware via a reversible round-trip.

## [0.6.1] - 2026-10-27
### Added
- Time-program **day-group decoding** and a byte-exact encoder (round-trips the
  device's own schedule body).

## [0.6.0] - 2026-10-27
### Added
- Read-only **time-program sensors** (`Func/TProg`, diagnostic).

## [0.5.2] - 2026-10-27
### Fixed
- DHW comfort setpoint range corrected to the device bounds (40–65 °C).

## [0.5.1] - 2026-10-27
### Fixed
- Build the TLS context off the event loop (removes the HA blocking-call warning).

## [0.5.0] - 2026-10-27
### Added
- Per-zone **climate** entity (zone 1): current temperature and active setpoint
  from the live `T_` reads; setting a target issues a timed hold (dérogation).

## [0.4.0] - 2026-10-27
### Added
- Live **temperature sensors** (zone room temp, active setpoint, boiler flow) and
  confirmed enum labels for the season/zone/DHW mode selects.

## [0.3.0] - 2026-10-27
### Added
- Temporary **override** (dérogation, `Func/Der`): `set_override` /
  `clear_override` services and a per-zone "override active" binary sensor.

## [0.2.0] - 2026-10-27
### Added
- **Energy sensors** from `Stat/cWh` (heating and DHW gas, kWh) that feed the HA
  Energy dashboard.

## [0.1.0] - 2026-10-27
### Added
- Initial Home Assistant integration over local MQTT (reverse-engineered
  `Menu/Par` protocol): config flow, number / select / switch / sensor entities,
  and a batched polling coordinator.

[0.14.0]: https://github.com/TheoLeCalvar/ariston-gateway-local-control/releases/tag/v0.14.0
[0.13.2]: https://github.com/TheoLeCalvar/ariston-gateway-local-control/releases/tag/v0.13.2
[0.13.1]: https://github.com/TheoLeCalvar/ariston-gateway-local-control/releases/tag/v0.13.1
[0.13.0]: https://github.com/TheoLeCalvar/ariston-gateway-local-control/releases/tag/v0.13.0
[0.12.0]: https://github.com/TheoLeCalvar/ariston-gateway-local-control/releases/tag/v0.12.0
[0.11.1]: https://github.com/TheoLeCalvar/ariston-gateway-local-control/releases/tag/v0.11.1
