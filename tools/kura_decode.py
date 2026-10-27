#!/usr/bin/env python3
"""Decode Eurotech Kura/Kapua MQTT payloads (as used by the Ariston gateway).

The gateway publishes Google-Protobuf `KuraPayload` messages. This decoder is
dependency-free (stdlib only) and tolerant of the field-encoding variants seen
on real traffic (e.g. int metrics sent as plain varints rather than sfixed32).

Usage:
    # decode a raw payload file
    kura_decode.py sample.bin

    # decode hex from stdin (one message per line, optional "topic | " prefix)
    mosquitto_sub ... -F '%t | %x' | kura_decode.py --hex

    # decode a single hex string
    kura_decode.py --hex 08f89f...

Output is one JSON object per message: {topic?, timestamp, metrics{...}, body?}.
"""
import sys
import json
import gzip
import struct
import argparse

# KuraMetric.ValueType enum
VT = {0: "DOUBLE", 1: "FLOAT", 2: "INT64", 3: "INT32", 4: "BOOL", 5: "STRING", 6: "BYTES"}


def _read_varint(buf, i):
    shift = 0
    result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("varint too long")


def _read_fields(buf):
    """Yield (field_number, wire_type, value) for a protobuf message.

    value is: int (varint), bytes (len-delimited), int (32/64-bit raw little-endian).
    Trailing/garbage bytes stop iteration quietly.
    """
    i = 0
    n = len(buf)
    while i < n:
        try:
            tag, i = _read_varint(buf, i)
        except (IndexError, ValueError):
            return
        field = tag >> 3
        wire = tag & 0x7
        if wire == 0:  # varint
            try:
                val, i = _read_varint(buf, i)
            except (IndexError, ValueError):
                return
            yield field, wire, val
        elif wire == 1:  # 64-bit
            if i + 8 > n:
                return
            yield field, wire, buf[i:i + 8]
            i += 8
        elif wire == 2:  # length-delimited
            try:
                ln, i = _read_varint(buf, i)
            except (IndexError, ValueError):
                return
            if i + ln > n:
                return
            yield field, wire, buf[i:i + ln]
            i += ln
        elif wire == 5:  # 32-bit
            if i + 4 > n:
                return
            yield field, wire, buf[i:i + 4]
            i += 4
        else:  # 3/4 = deprecated groups, unsupported
            return


def _decode_metric(buf):
    name = None
    vtype = None
    raw = {}  # field_number -> (wire, value)
    for field, wire, val in _read_fields(buf):
        if field == 1 and wire == 2:
            name = val.decode("utf-8", "replace")
        elif field == 2 and wire == 0:
            vtype = val
        elif field in (3, 4, 5, 6, 7, 8, 9):
            raw[field] = (wire, val)

    value = _interpret_value(vtype, raw)
    return name, VT.get(vtype, vtype), value


def _interpret_value(vtype, raw):
    # Prefer the value field that matches the declared type, but fall back to
    # whatever is present (firmware variants don't always use the "canonical" field).
    def getraw(*fields):
        for f in fields:
            if f in raw:
                return raw[f]
        return None

    if vtype == 0:  # DOUBLE (field 3, 64-bit)
        r = getraw(3)
        if r and r[0] == 1:
            return struct.unpack("<d", r[1])[0]
    if vtype == 1:  # FLOAT (field 4, 32-bit)
        r = getraw(4)
        if r and r[0] == 5:
            return struct.unpack("<f", r[1])[0]
    if vtype in (2, 3):  # INT64 / INT32
        r = getraw(5, 6)
        if r:
            if r[0] == 0:
                return _zigzag_or_plain(r[1])
            if r[0] == 1:
                return struct.unpack("<q", r[1])[0]
            if r[0] == 5:
                return struct.unpack("<i", r[1])[0]
    if vtype == 4:  # BOOL (field 7, varint)
        r = getraw(7)
        if r and r[0] == 0:
            return bool(r[1])
    if vtype == 5:  # STRING (field 8)
        r = getraw(8)
        if r and r[0] == 2:
            return r[1].decode("utf-8", "replace")
    if vtype == 6:  # BYTES (field 9)
        r = getraw(9)
        if r and r[0] == 2:
            return r[1].hex()

    # Unknown/mismatched: return the first present value in a best-effort form.
    for f in (8, 5, 6, 3, 4, 7, 9):
        if f in raw:
            wire, val = raw[f]
            if wire == 0:
                return val
            if wire == 2:
                try:
                    return val.decode("utf-8")
                except UnicodeDecodeError:
                    return val.hex()
            return val.hex() if isinstance(val, (bytes, bytearray)) else val
    return None


def _zigzag_or_plain(v):
    # Plain varint; Kura uses unsigned/int here (not zigzag) in practice.
    return v


def decode_payload(data):
    """Return a dict for one KuraPayload message (bytes)."""
    if data[:2] == b"\x1f\x8b":  # gzip magic
        try:
            data = gzip.decompress(data)
        except OSError:
            pass

    out = {"timestamp": None, "metrics": {}}
    for field, wire, val in _read_fields(data):
        if field == 1 and wire == 0:  # timestamp as varint (ms)
            out["timestamp"] = val
        elif field == 1 and wire == 1:  # timestamp as sfixed64
            out["timestamp"] = struct.unpack("<q", val)[0]
        elif field == 5000 and wire == 2:  # a metric
            name, tname, value = _decode_metric(val)
            if name is not None:
                out["metrics"][name] = {"type": tname, "value": value}
        elif field == 5001 and wire == 2:  # body
            out["body"] = val.hex()
        elif field == 2 and wire == 2:  # position (rarely used here)
            out["position"] = val.hex()
    return out


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
