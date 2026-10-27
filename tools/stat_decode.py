#!/usr/bin/env python3
"""Decode the Ariston `Stat/cWh` energy-statistics reply.

Unlike Menu/Par (values in metrics), the energy reply carries its data in the
KuraPayload *body* (field 5001): a nested protobuf of repeated entries, each

    { 1: idx (varint), 2: Typ (varint), 3: values (packed varints) }

A GET to Stat/cWh requests a set of `Typ` codes (observed: 6, 20, 9, 21); the
reply returns, for each Typ, one series per `idx`. The idx selects the period
granularity (by series length): 24 ≈ hourly, 13 ≈ monthly, others still to be
confirmed against the app. The integer values look like energy in Wh for the
consumption series (e.g. Typ=6 idx=4 sums to a plausible annual heating kWh).

Usage:
    tools/stat_decode.py reply.bin           # a raw Stat/cWh reply payload
    tools/stat_decode.py --hex 08f0...        # hex of one payload
    tools/stat_decode.py --mitm mitm.jsonl    # pull Stat/cWh replies from a capture

See docs/06 for the Typ/idx semantics still being mapped.
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_decode as kd

# length of a series -> a human guess at its period (confirm against the app)
PERIOD_BY_LEN = {24: "hourly(24)", 13: "monthly(13)", 12: "monthly(12)", 7: "daily(7)"}


def _fields(buf):
    return list(kd._read_fields(buf))


def _varints(buf):
    vals, i = [], 0
    while i < len(buf):
        v, i = kd._read_varint(buf, i)
        vals.append(v)
    return vals


def decode_stat_body(body: bytes) -> list:
    """Return [{"typ", "idx", "values"}] from a Stat/cWh body."""
    out = []
    for f, w, v in _fields(body):
        if f == 1 and w == 2:
            idx = typ = None
            arr = b""
            for sf, sw, sv in _fields(v):
                if sf == 1 and sw == 0:
                    idx = sv
                elif sf == 2 and sw == 0:
                    typ = sv
                elif sf == 3 and sw == 2:
                    arr = sv
            out.append({"typ": typ, "idx": idx, "values": _varints(arr)})
    return out


def decode_stat_payload(data: bytes) -> list:
    """Decode a full Stat/cWh reply payload into its per-Typ/idx series."""
    body = None
    for f, w, v in _fields(data):
        if f == 5001 and w == 2:
            body = v
    return decode_stat_body(body) if body else []


def _report(series, as_json):
    if as_json:
        print(json.dumps(series, ensure_ascii=False))
        return
    for s in sorted(series, key=lambda x: (x["typ"] or 0, x["idx"] or 0)):
        vals = s["values"]
        nz = [v for v in vals if v]
        period = PERIOD_BY_LEN.get(len(vals), f"len{len(vals)}")
        total = sum(vals)
        extra = f"  sum={total} (~{total/1000:.1f}k)" if nz else "  (all zero)"
        print(f"  Typ={s['typ']:>2} idx={s['idx']} [{period}]{extra}")
        if nz:
            print(f"      {vals}")


def _from_mitm(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    reqs = {}
    for o in rows:
        t = o.get("topic", "")
        m = (o.get("decoded") or {}).get("metrics") or {}
        rid = m.get("request.id", {})
        rid = rid.get("value") if isinstance(rid, dict) else rid
        if "/GET/" in t and rid is not None:
            reqs[str(rid)] = t.split("/ar1/", 1)[-1]
    found = []
    for o in rows:
        t = o.get("topic", "")
        if "/REPLY/" in t and reqs.get(t.rsplit("/", 1)[-1], "").startswith("GET/Stat"):
            found.append(bytes.fromhex(o["payload_hex"]))
    return found


def main():
    ap = argparse.ArgumentParser(description="Decode Ariston Stat/cWh energy replies")
    ap.add_argument("input", nargs="?", help="raw reply payload file")
    ap.add_argument("--hex", help="hex of one reply payload")
    ap.add_argument("--mitm", help="extract Stat/cWh replies from an mqtt_mitm log")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    payloads = []
    if args.mitm:
        payloads = _from_mitm(args.mitm)
    elif args.hex:
        payloads = [bytes.fromhex(args.hex.replace(" ", ""))]
    elif args.input:
        with open(args.input, "rb") as f:
            payloads = [f.read()]
    else:
        ap.error("give a payload file, --hex, or --mitm")

    if not payloads:
        print("no Stat/cWh replies found", file=sys.stderr)
        return
    for i, data in enumerate(payloads, 1):
        series = decode_stat_payload(data)
        if not args.json:
            print(f"=== Stat/cWh reply {i} ({len(series)} series) ===")
        _report(series, args.json)


if __name__ == "__main__":
    main()
