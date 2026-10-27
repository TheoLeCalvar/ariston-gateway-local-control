"""Constants for the Ariston local (Menu/Par over local MQTT) integration."""
from __future__ import annotations

DOMAIN = "ariston_local"

CONF_HOST = "host"
CONF_PORT = "port"
CONF_MAC = "mac"
CONF_CAFILE = "cafile"
CONF_SCAN_INTERVAL = "scan_interval"

DEFAULT_PORT = 8883
DEFAULT_SCAN_INTERVAL = 60  # seconds

# Setting a temperature from the climate card issues a timed hold (dérogation)
# that the boiler auto-reverts; this is its default length. For a different
# duration, use the ariston_local.set_override service.
DEFAULT_CLIMATE_HOLD_HOURS = 3

# Time-program ids observed on the wire (Func/TProg). 1 = zone 1 heating program
# (weekday + weekend profiles), 7 = a second program. Read-only.
TPROG_IDS = (1, 7)

# Special function (Func/SpecF). Mode 4 = holiday/vacances (hold until T, an
# absolute end epoch); Mode 1 = idle/none (observed when nothing is active).
SPECF_HOLIDAY_MODE = 4
SPECF_IDLE_MODE = 1

MANUFACTURER = "Ariston"
