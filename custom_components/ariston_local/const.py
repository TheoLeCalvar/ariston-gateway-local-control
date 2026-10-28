"""Constants for the Ariston local (Menu/Par over local MQTT) integration."""
from __future__ import annotations

DOMAIN = "ariston_local"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_MAC = "mac"
CONF_CAFILE = "cafile"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ACTIVE_ZONES = "active_zones"

DEFAULT_PORT = 8883
DEFAULT_SCAN_INTERVAL = 60  # seconds

# Which heating zones to expose. Zone 1 is always on (it carries the climate
# entity); zones 2/3 are opt-out for single-zone systems. Default is all three
# so an existing install keeps every entity until the user trims it in Options.
DEFAULT_ACTIVE_ZONES = (1, 2, 3)

# Setting a temperature from the climate card issues a timed hold (dérogation)
# that the boiler auto-reverts; this is its default length. For a different
# duration, use the ariston_local.set_override service.
DEFAULT_CLIMATE_HOLD_HOURS = 3

# Time-program ids observed on the wire (Func/TProg). 1 = zone 1 heating program
# (weekday + weekend profiles), 7 = a second program.
TPROG_IDS = (1, 7)

# The schedule lives on the boiler (source of truth, runs even if HA is down); HA
# just reflects it and writes edits back. Poll often enough that a schedule edited
# on the thermostat itself surfaces in HA within a few minutes.
TPROG_SCAN_INTERVAL = 300  # seconds

# The other resources change rarely (or are slow for the gateway to answer, like
# Stat/cWh), so they poll on their own slower clocks than the main Menu/Par read.
STATS_SCAN_INTERVAL = 600     # Stat/cWh energy
OVERRIDE_SCAN_INTERVAL = 120  # Func/Der per zone
HOLIDAY_SCAN_INTERVAL = 300   # Func/SpecF

# Special function (Func/SpecF). Mode 4 = holiday/vacances (hold until T, an
# absolute end epoch); Mode 1 = idle/none (observed when nothing is active).
SPECF_HOLIDAY_MODE = 4
SPECF_IDLE_MODE = 1

MANUFACTURER = "Ariston"
