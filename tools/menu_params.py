#!/usr/bin/env python3
"""Decode Ariston installer-menu parameter codes (U_/T_/C_ + menu number).

The gateway's real config channel is the `Menu/Par` resource: a GET carries up to
16 parameter codes as metrics P1..P16, and the reply returns each code with its
integer value. Codes look like:

    U_6.16.0      user parameter at menu 6.16.0 (the value shown in the app)
    T_8.4.0       technical / sensor read at menu 8.4.0
    C_U_6.9.0     the "C_" variant of a code (returns the same value here)

`custom_components/ariston_local/menu_params.json` maps the menu number to a
label, unit, scaling and enum, worked out by matching a capture against the web
installer menu. The parsing and scaling live in the component's menu.py; this
module adds the lookup/enrichment helpers and the CLI.

Library:
    from menu_params import parse_code, annotate, batch_metrics
    annotate("U_6.3.1.0.0")        # -> {..., "menu": "6.3.1.0.0", "known": {...}}
    list(batch_metrics(["U_0.0", "U_3"]))   # -> [{"P1": ("STRING","U_0.0"), ...}]

CLI:
    menu_params.py U_6.16.0 C_U_6.9.0       # explain codes
    menu_params.py --list                   # dump the parameter map
    kura_decode.py reply.bin | menu_params.py --enrich   # annotate a reply
"""
import os
import sys
import json
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _component  # noqa: E402,F401
# parsing, scaling and the parameter map live in the component (menu.py)
from menu import (  # noqa: E402,F401
    CODE_RE, PARAMS, _KIND as KIND, load_params, parse_code, apply_scale, to_raw,
    batch_metrics, decode,
)


def annotate(name):
    """parse_code() plus the parameter-map match (label/unit/scale/enum)."""
    p = parse_code(name)
    if p is None:
        return None
    known = PARAMS.get(p["menu"])
    if known:
        p["known"] = known
    return p


def _enrich_obj(obj):
    """Add a `menu` block to each recognized U_/T_/C_ metric in a payload."""
    metrics = obj.get("metrics")
    if not isinstance(metrics, dict):
        return obj
    for name, m in list(metrics.items()):
        info = annotate(name)
        if not info:
            continue
        note = {"menu": info["menu"], "kind": info["kind"]}
        if info["variant"]:
            note["variant"] = info["variant"]
        known = info.get("known")
        if known:
            note["label"] = known["label"]
            if known.get("fr"):
                note["fr"] = known["fr"]
            if known.get("unit"):
                note["unit"] = known["unit"]
            if isinstance(m, dict):
                scaled, enum_label = decode(info["menu"], m.get("value"))
                if scaled is not None and scaled != m.get("value"):
                    note["scaled_value"] = scaled
                if enum_label is not None:
                    note["enum_label"] = enum_label
        if isinstance(m, dict):
            m["menu_param"] = note
        else:
            metrics[name] = {"value": m, "menu_param": note}
    return obj


def all_codes(prefix="U_"):
    """Every menu number in the map, as codes with the given prefix."""
    return [f"{prefix}{menu}" for menu in PARAMS]


def main():
    ap = argparse.ArgumentParser(description="Decode Ariston installer-menu parameter codes")
    ap.add_argument("codes", nargs="*", help="codes to explain, e.g. U_6.16.0")
    ap.add_argument("--enrich", action="store_true",
                    help="read kura_decode JSON lines on stdin, annotate menu codes")
    ap.add_argument("--list", action="store_true", help="print the parameter map")
    args = ap.parse_args()

    if args.list:
        for menu, p in sorted(PARAMS.items(), key=lambda kv: _menukey(kv[0])):
            unit = f" [{p['unit']}]" if p.get("unit") else ""
            extra = f"  ({p.get('scale')})" if p.get("scale") else ""
            print(f"{menu:12} {p['label']}{unit}{extra}  — {p.get('fr', '')}")
        return

    if args.enrich:
        import kura_decode  # noqa: F401 (only to confirm availability if piped raw)
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

    if not args.codes:
        ap.error("give one or more codes, or use --enrich / --list")
    for name in args.codes:
        info = annotate(name)
        print(json.dumps(info or {"code": name, "error": "not a menu code"},
                         ensure_ascii=False))


def _menukey(menu):
    return [int(x) for x in menu.split(".")]


if __name__ == "__main__":
    main()
