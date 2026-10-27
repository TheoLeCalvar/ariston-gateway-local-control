#!/usr/bin/env bash
# Resolve the real cloud broker IP via a public resolver, bypassing any local
# DNS override you've set for broker-ari.everyware-cloud.com.
set -euo pipefail
HOST="${1:-broker-ari.everyware-cloud.com}"
RESOLVER="${2:-1.1.1.1}"

if command -v dig >/dev/null; then
  dig +short "@$RESOLVER" "$HOST" A | grep -E '^[0-9.]+$'
elif command -v nslookup >/dev/null; then
  nslookup "$HOST" "$RESOLVER" | awk '/^Address: /{print $2}'
else
  python3 - "$HOST" "$RESOLVER" <<'PY'
import sys, os, socket, struct
name, resolver = sys.argv[1], sys.argv[2]
q = os.urandom(2) + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
for p in name.split("."): q += bytes([len(p)]) + p.encode()
q += b"\x00\x00\x01\x00\x01"
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.settimeout(4)
s.sendto(q, (resolver, 53)); data,_ = s.recvfrom(2048)
i = 12
while data[i]: i += 1 + data[i]
i += 5
for _ in range(struct.unpack(">H", data[6:8])[0]):
    if data[i] & 0xC0 == 0xC0: i += 2
    else:
        while data[i]: i += 1 + data[i]
        i += 1
    rtype,_,_,rdlen = struct.unpack(">HHIH", data[i:i+10]); i += 10
    if rtype == 1: print(".".join(str(b) for b in data[i:i+4]))
    i += rdlen
PY
fi
