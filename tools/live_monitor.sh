#!/usr/bin/env bash
# Live, human-readable view of everything the gateway publishes to the local
# broker. Subscribes to all topics and pipes each payload through kura_decode.py.
#
# Usage: live_monitor.sh [broker_dir]
#   broker_dir: directory containing server.crt (default: ../broker)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
BROKER_DIR="${1:-$HERE/../broker}"
HOST="${MQTT_HOST:-127.0.0.1}"
PORT="${MQTT_PORT:-8883}"

exec mosquitto_sub \
  --cafile "$BROKER_DIR/server.crt" --insecure \
  -h "$HOST" -p "$PORT" \
  -t 'ari/#' -t '$EDC/#' \
  -F '%t | %x' \
  | python3 -I "$HERE/kura_decode.py" --hex
