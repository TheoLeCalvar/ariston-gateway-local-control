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

MANUFACTURER = "Ariston"
