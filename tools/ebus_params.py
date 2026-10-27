#!/usr/bin/env python3
"""Decode the gateway's hex-coded eBUS parameter IDs into eBUS registers.

The gateway names metrics with 5-byte hex strings like `106800FE01`. We worked
out their structure by matching known community BridgeNet registers (see
docs/04-ebus-parameters.md):

    byte 0-1 : register, little-endian  -> community "eBUS ID" is the byte-swap
               (10 68  ->  6810 heating flow temp;  4b c0  ->  c04b boiler status)
    byte 2   : flag / sub-index (0x00 and 0x82 seen; meaning not yet confirmed)
    byte 3   : 0xFE  -- the eBUS broadcast address
    byte 4   : zone / instance index (0x00, 0x01 seen)

`tools/ebus_registers.json` holds the (extensible) register dictionary. This
module parses an ID, looks the register up, and can enrich a stream of decoded
kura_decode JSON by annotating every hex metric name it recognizes.

Library:
    from ebus_params import parse_id, annotate
    parse_id("106800FE01")   # -> structured dict
    annotate("106800FE01")   # -> dict with the register match merged in

CLI:
    ebus_params.py 106800FE01 4BC000FE01        # explain IDs
    kura_decode.py cap.bin | ebus_params.py --enrich   # annotate decoded JSON
    ebus_params.py --list                              # dump the register map
"""
import os
import re
import sys
import json
import argparse

_HERE = os.path.dirname(os.path.abspath(__file__))
_MAP_PATH = os.path.join(_HERE, "ebus_registers.json")

HEXID_RE = re.compile(r"^[0-9A-Fa-f]{10}$")  # exactly 5 bytes


def load_registers(path=_MAP_PATH):
    try:
        with open(path) as f:
            return json.load(f).get("registers", {})
    except (OSError, json.JSONDecodeError):
        return {}


REGISTERS = load_registers()


def ebus_id(raw_bytes):
    """Community eBUS ID = byte-swap of the little-endian 2-byte register."""
    return f"{raw_bytes[1]:02x}{raw_bytes[0]:02x}"


def build_name(ebus_id_str, flag=0x00, zone=0x00, src=0xFE):
    """Inverse of parse_id: build the 5-byte hex metric name for a register.

    >>> build_name("6810", flag=0x00, zone=0x01)
    '106800FE01'
    """
    e = bytes.fromhex(f"{int(ebus_id_str, 16):04x}")  # 2 bytes, high then low
    raw = bytes([e[1], e[0], flag & 0xFF, src & 0xFF, zone & 0xFF])
    return raw.hex().upper()


def iter_ebus_ids(start, end, step=1):
    """Yield 4-hex eBUS IDs over an inclusive integer range (hex-friendly)."""
    for v in range(int(start) if isinstance(start, int) else int(start, 16),
                    (int(end) if isinstance(end, int) else int(end, 16)) + 1, step):
        yield f"{v:04x}"


def parse_id(name):
    """Parse a 5-byte hex metric name into its eBUS fields, or return None."""
    if not isinstance(name, str) or not HEXID_RE.match(name):
        return None
    b = bytes.fromhex(name)
    return {
        "raw": name.upper(),
        "register_le": f"{b[0]:02x}{b[1]:02x}",
        "ebus_id": ebus_id(b),
        "flag": b[2],
        "src": b[3],          # 0xFE = eBUS broadcast
        "zone": b[4],
        "is_broadcast": b[3] == 0xFE,
    }


def annotate(name):
    """parse_id() plus the register-map match (name/meaning/unit/scale), if known."""
    p = parse_id(name)
    if p is None:
        return None
    reg = REGISTERS.get(p["ebus_id"])
    if reg:
        p["known"] = reg
    return p


def apply_scale(value, scale):
    """Best-effort physical value for the common scalings. Returns value unchanged
    for enum/unknown scalings (the caller still has the raw)."""
    try:
        if scale == "x10":
            return round(value / 10.0, 1)
        if scale == "x100":
            return value * 100
    except (TypeError, ValueError):
        pass
    return value


def _enrich_obj(obj):
    """Add an `ebus` block to each recognized hex metric in a decoded payload."""
    metrics = obj.get("metrics")
    if not isinstance(metrics, dict):
        return obj
    for name, m in list(metrics.items()):
        info = annotate(name)
        if not info:
            continue
        known = info.get("known")
        note = {"ebus_id": info["ebus_id"], "zone": info["zone"], "flag": info["flag"]}
        if known:
            note["name"] = known["name"]
            note["meaning"] = known["meaning"]
            if known.get("unit"):
                note["unit"] = known["unit"]
            if isinstance(m, dict) and known.get("scale"):
                note["scaled_value"] = apply_scale(m.get("value"), known["scale"])
        if isinstance(m, dict):
            m["ebus"] = note
        else:
            metrics[name] = {"value": m, "ebus": note}
    return obj


def main():
    ap = argparse.ArgumentParser(description="Decode Ariston eBUS parameter IDs")
    ap.add_argument("ids", nargs="*", help="5-byte hex metric names to explain")
    ap.add_argument("--enrich", action="store_true",
                    help="read kura_decode JSON lines on stdin, annotate hex metrics")
    ap.add_argument("--list", action="store_true", help="print the register map")
    args = ap.parse_args()

    if args.list:
        for eid, reg in sorted(REGISTERS.items()):
            unit = f" [{reg['unit']}]" if reg.get("unit") else ""
            print(f"{eid}  {reg['name']:22}{unit:6}  {reg['meaning']}  ({reg.get('confidence')})")
        return

    if args.enrich:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                sys.stderr.write(f"skip non-JSON line: {line[:60]}\n")
                continue
            print(json.dumps(_enrich_obj(obj), ensure_ascii=False, default=str))
        return

    if not args.ids:
        ap.error("give one or more hex IDs, or use --enrich / --list")
    for name in args.ids:
        info = annotate(name)
        if info is None:
            print(json.dumps({"raw": name, "error": "not a 5-byte hex id"}))
        else:
            print(json.dumps(info, ensure_ascii=False))


if __name__ == "__main__":
    main()
