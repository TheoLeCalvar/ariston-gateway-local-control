#!/usr/bin/env python3
"""Encode Eurotech Kura/Kapua MQTT payloads (the inverse of kura_decode).

Dependency-free (stdlib only). Integers are written as plain varints, which is
what the Ariston gateway itself sends (and what the Kura schema's int32/int64
fields mean on the wire), so encoded payloads look like the device's own.
The codec itself is custom_components/ariston_local/kura.py; this is its CLI.

Library use:
    from kura_encode import encode_payload
    data = encode_payload({"request.id": "abc", "T": 21.5, "On": True})
    # force a type: {"X": ("INT64", 5)}

CLI use (writes raw bytes to stdout, or hex with --hex):
    kura_encode.py --metric request.id=abc --metric T:DOUBLE=21.5 > p.bin
    kura_encode.py --json '{"request.id": "abc"}' --hex
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _component  # noqa: E402,F401
# the codec lives in the component; re-exported for the tools that import it
from kura import (  # noqa: E402,F401
    _TYPES as TYPES, _varint, _key, _ld, _infer_type,
    _encode_metric as encode_metric, encode_payload,
)


def _parse_metric_arg(arg):
    """name=value or name:TYPE=value. Without a type, ints/floats/bools are inferred."""
    name, _, raw = arg.partition("=")
    vtype = None
    if ":" in name:
        name, vtype = name.rsplit(":", 1)
        vtype = vtype.upper()
        if vtype not in TYPES:
            raise SystemExit(f"unknown type {vtype} (one of {', '.join(TYPES)})")
    if vtype in ("INT32", "INT64"):
        return name, (vtype, int(raw, 0))
    if vtype in ("DOUBLE", "FLOAT"):
        return name, (vtype, float(raw))
    if vtype == "BOOL":
        return name, (vtype, raw.lower() in ("1", "true", "yes", "on"))
    if vtype == "BYTES":
        return name, (vtype, bytes.fromhex(raw))
    if vtype == "STRING":
        return name, (vtype, raw)
    for conv in (int, float):
        try:
            return name, conv(raw)
        except ValueError:
            pass
    if raw.lower() in ("true", "false"):
        return name, raw.lower() == "true"
    return name, raw


def main():
    ap = argparse.ArgumentParser(description="Encode a Eurotech Kura/Kapua MQTT payload")
    ap.add_argument("--metric", "-m", action="append", default=[],
                    help="name=value or name:TYPE=value (repeatable)")
    ap.add_argument("--json", help="metrics as a JSON object")
    ap.add_argument("--body-hex", help="opaque body bytes, hex")
    ap.add_argument("--timestamp", type=int, help="ms since epoch (default: now)")
    ap.add_argument("--no-timestamp", action="store_true")
    ap.add_argument("--hex", action="store_true", help="print hex instead of raw bytes")
    args = ap.parse_args()

    metrics = json.loads(args.json) if args.json else {}
    for m in args.metric:
        k, v = _parse_metric_arg(m)
        metrics[k] = v
    ts = False if args.no_timestamp else args.timestamp
    body = bytes.fromhex(args.body_hex) if args.body_hex else None
    data = encode_payload(metrics, ts, body)
    if args.hex:
        print(data.hex())
    else:
        sys.stdout.buffer.write(data)


if __name__ == "__main__":
    main()
