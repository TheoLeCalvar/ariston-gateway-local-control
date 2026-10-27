#!/usr/bin/env bash
# Generate the self-signed server cert the local broker presents to the gateway.
# The gateway does NOT validate the server certificate, so a self-signed cert
# with the right CN/SAN is enough for it to connect.
set -euo pipefail
cd "$(dirname "$0")"

HOST="broker-ari.everyware-cloud.com"

openssl req -x509 -newkey rsa:2048 -nodes \
  -keyout server.key -out server.crt -days 825 \
  -subj "/C=IT/O=TestBroker/CN=${HOST}" \
  -addext "subjectAltName=DNS:${HOST}"

echo "Wrote server.crt / server.key for ${HOST}"
openssl x509 -in server.crt -noout -subject -ext subjectAltName
