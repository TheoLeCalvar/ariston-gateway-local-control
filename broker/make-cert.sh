#!/usr/bin/env bash
# Generate the self-signed server cert the local broker/MITM presents to the
# gateway. Whether it is accepted depends on the gateway: the everyware-cloud
# gateway does NOT validate it, so the right CN/SAN is enough. An AWS-IoT-era
# gateway may validate against a baked-in CA, in which case this won't be
# accepted (see docs/08).
#
# Usage: make-cert.sh [HOSTNAME] [OUT_PREFIX]
#   make-cert.sh                                   -> server.crt/key, CN broker-ari.everyware-cloud.com
#   make-cert.sh connect.iot.remotethermo.com connect-iot  -> connect-iot.crt/key
set -euo pipefail
cd "$(dirname "$0")"

HOST="${1:-broker-ari.everyware-cloud.com}"
PREFIX="${2:-server}"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout "${PREFIX}.key" -out "${PREFIX}.crt" -days 825 \
  -subj "/C=IT/O=TestBroker/CN=${HOST}" \
  -addext "subjectAltName=DNS:${HOST}"

echo "Wrote ${PREFIX}.crt / ${PREFIX}.key for ${HOST}"
openssl x509 -in "${PREFIX}.crt" -noout -subject -ext subjectAltName
