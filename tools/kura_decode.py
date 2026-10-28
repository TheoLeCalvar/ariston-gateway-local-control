#!/usr/bin/env python3
"""Decode Eurotech Kura/Kapua MQTT payloads (as used by the Ariston gateway).

The gateway publishes Google-Protobuf `KuraPayload` messages. This decoder is
dependency-free (stdlib only) and tolerant of the field-encoding variants seen
on real traffic (e.g. int metrics sent as plain varints rather than sfixed32).
The codec itself is custom_components/ariston_local/kura.py; this is its CLI.

Usage:
    # decode a raw payload file
    kura_decode.py sample.bin

    # decode hex from stdin (one message per line, optional "topic | " prefix)
    mosquitto_sub ... -F '%t | %x' | kura_decode.py --hex

    # decode a single hex string
    kura_decode.py --hex 08f89f...

Output is one JSON object per message: {topic?, timestamp, metrics{...}, body?}.
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _component  # noqa: E402,F401
# the codec lives in the component; re-exported for the tools that import it
from kura import (  # noqa: E402,F401
    _VT as VT, _read_varint, _read_fields, _decode_metric, _interpret_value,
    _signed_varint, decode_payload,
)


def _emit(topic, data):
    obj = decode_payload(data)
    if topic is not None:
        obj = {"topic": topic, **obj}
    print(json.dumps(obj, ensure_ascii=False, default=str))


def main():
    ap = argparse.ArgumentParser(description="Decode Eurotech Kura/Kapua MQTT payloads")
    ap.add_argument("input", nargs="?", help="raw payload file, or hex string with --hex")
    ap.add_argument("--hex", action="store_true",
                    help="treat input / stdin lines as hex (stdin: optional 'topic | ' prefix)")
    args = ap.parse_args()

    if args.hex and not args.input:
        # stream: one message per line, optional "topic | hex"
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            topic = None
            hexstr = line
            if " | " in line:
                topic, hexstr = line.split(" | ", 1)
            hexstr = hexstr.strip().replace(" ", "")
            if not hexstr:
                # zero-length payload (e.g. LWT / retained clear)
                print(json.dumps({"topic": topic, "empty": True}, ensure_ascii=False))
                continue
            try:
                data = bytes.fromhex(hexstr)
            except ValueError:
                sys.stderr.write(f"skip non-hex line: {line[:60]}\n")
                continue
            _emit(topic, data)
    elif args.hex:
        _emit(None, bytes.fromhex(args.input.replace(" ", "")))
    elif args.input:
        with open(args.input, "rb") as f:
            _emit(None, f.read())
    else:
        # raw bytes from stdin, single message
        _emit(None, sys.stdin.buffer.read())


if __name__ == "__main__":
    main()
