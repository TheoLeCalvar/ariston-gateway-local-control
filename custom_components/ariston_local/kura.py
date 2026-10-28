"""Eurotech Kura/Kapua payload codec (self-contained, stdlib only).

The gateway speaks Google-Protobuf `KuraPayload` messages. The decoder is
tolerant of the field-encoding variants seen on real traffic (e.g. int metrics
sent as plain varints rather than sfixed32). The encoder writes ints as plain
varints (what the schema's int32/int64 fields mean on the wire), so encoded
payloads look like the device's own.

This is the single copy of the codec: the repo's stdlib CLI tools
(tools/kura_{encode,decode}.py) import it rather than carrying their own.
"""
from __future__ import annotations

import gzip
import struct
import time

# KuraMetric.ValueType enum
_VT = {0: "DOUBLE", 1: "FLOAT", 2: "INT64", 3: "INT32", 4: "BOOL", 5: "STRING", 6: "BYTES"}
_TYPES = {"DOUBLE": 0, "FLOAT": 1, "INT64": 2, "INT32": 3, "BOOL": 4, "STRING": 5, "BYTES": 6}


# --------------------------------------------------------------------------- decode
def _read_varint(buf, i):
    shift = result = 0
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
    i, n = 0, len(buf)
    while i < n:
        try:
            tag, i = _read_varint(buf, i)
        except (IndexError, ValueError):
            return
        field, wire = tag >> 3, tag & 0x7
        if wire == 0:
            try:
                val, i = _read_varint(buf, i)
            except (IndexError, ValueError):
                return
            yield field, wire, val
        elif wire == 1:
            if i + 8 > n:
                return
            yield field, wire, buf[i:i + 8]
            i += 8
        elif wire == 2:
            try:
                ln, i = _read_varint(buf, i)
            except (IndexError, ValueError):
                return
            if i + ln > n:
                return
            yield field, wire, buf[i:i + ln]
            i += ln
        elif wire == 5:
            if i + 4 > n:
                return
            yield field, wire, buf[i:i + 4]
            i += 4
        else:
            return


def _signed_varint(v):
    # protobuf int32/int64 negatives are sign-extended to a 64-bit varint
    return v - (1 << 64) if v >= (1 << 63) else v


def _interpret_value(vtype, raw):
    def getraw(*fields):
        for f in fields:
            if f in raw:
                return raw[f]
        return None

    if vtype == 0:
        r = getraw(3)
        if r and r[0] == 1:
            return struct.unpack("<d", r[1])[0]
    if vtype == 1:
        r = getraw(4)
        if r and r[0] == 5:
            return struct.unpack("<f", r[1])[0]
    if vtype in (2, 3):
        r = getraw(5, 6)
        if r:
            if r[0] == 0:
                return _signed_varint(r[1])
            if r[0] == 1:
                return struct.unpack("<q", r[1])[0]
            if r[0] == 5:
                return struct.unpack("<i", r[1])[0]
    if vtype == 4:
        r = getraw(7)
        if r and r[0] == 0:
            return bool(r[1])
    if vtype == 5:
        r = getraw(8)
        if r and r[0] == 2:
            return r[1].decode("utf-8", "replace")
    if vtype == 6:
        r = getraw(9)
        if r and r[0] == 2:
            return r[1].hex()
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


def _decode_metric(buf):
    name = vtype = None
    raw = {}
    for field, wire, val in _read_fields(buf):
        if field == 1 and wire == 2:
            name = val.decode("utf-8", "replace")
        elif field == 2 and wire == 0:
            vtype = val
        elif field in (3, 4, 5, 6, 7, 8, 9):
            raw[field] = (wire, val)
    return name, _VT.get(vtype, vtype), _interpret_value(vtype, raw)


def decode_payload(data: bytes) -> dict:
    """Decode a KuraPayload into {timestamp, metrics{name: {type, value}}, body?}."""
    if data[:2] == b"\x1f\x8b":
        try:
            data = gzip.decompress(data)
        except OSError:
            pass
    out = {"timestamp": None, "metrics": {}}
    for field, wire, val in _read_fields(data):
        if field == 1 and wire == 0:
            out["timestamp"] = val
        elif field == 1 and wire == 1:
            out["timestamp"] = struct.unpack("<q", val)[0]
        elif field == 5000 and wire == 2:
            name, tname, value = _decode_metric(val)
            if name is not None:
                out["metrics"][name] = {"type": tname, "value": value}
        elif field == 5001 and wire == 2:
            out["body"] = val.hex()
        elif field == 2 and wire == 2:  # position (rarely used here)
            out["position"] = val.hex()
    return out


# --------------------------------------------------------------------------- encode
def _varint(v):
    if v < 0:
        v += 1 << 64
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        if v:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _key(field, wire):
    return _varint((field << 3) | wire)


def _ld(field, data):
    return _key(field, 2) + _varint(len(data)) + data


def _infer_type(value):
    if isinstance(value, bool):
        return "BOOL"
    if isinstance(value, int):
        return "INT32" if -(1 << 31) <= value < (1 << 31) else "INT64"
    if isinstance(value, float):
        return "DOUBLE"
    if isinstance(value, (bytes, bytearray)):
        return "BYTES"
    if isinstance(value, str):
        return "STRING"
    raise TypeError(f"cannot infer Kura type for {value!r}")


def _encode_metric(name, value, vtype=None):
    vtype = vtype or _infer_type(value)
    out = _ld(1, name.encode("utf-8")) + _key(2, 0) + _varint(_TYPES[vtype])
    if vtype == "DOUBLE":
        out += _key(3, 1) + struct.pack("<d", float(value))
    elif vtype == "FLOAT":
        out += _key(4, 5) + struct.pack("<f", float(value))
    elif vtype == "INT64":
        out += _key(5, 0) + _varint(int(value))
    elif vtype == "INT32":
        out += _key(6, 0) + _varint(int(value))
    elif vtype == "BOOL":
        out += _key(7, 0) + _varint(1 if value else 0)
    elif vtype == "STRING":
        out += _ld(8, str(value).encode("utf-8"))
    elif vtype == "BYTES":
        out += _ld(9, bytes(value))
    return out


def encode_payload(metrics=None, timestamp=None, body=None) -> bytes:
    """Encode {name: value | (TYPE, value)} into a KuraPayload. timestamp=False omits it."""
    out = b""
    if timestamp is None:
        timestamp = int(time.time() * 1000)
    if timestamp is not False:
        out += _key(1, 0) + _varint(int(timestamp))
    for name, value in (metrics or {}).items():
        vtype = None
        if isinstance(value, tuple):
            vtype, value = value
        out += _ld(5000, _encode_metric(name, value, vtype))
    if body is not None:
        out += _ld(5001, bytes(body))
    return out
