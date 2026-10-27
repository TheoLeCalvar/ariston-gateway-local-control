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

MANUFACTURER = "Ariston"
