"""Installer-menu parameter map and scaling (self-contained, stdlib only).

Decodes U_/T_/C_ codes, applies/undoes scaling, and batches codes into Menu/Par
P1..P16 request metrics. menu_params.json (next to this file) is the one copy of
the map; tools/menu_params.py is its CLI.
"""
from __future__ import annotations

import json
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_MAP_PATH = os.path.join(_HERE, "menu_params.json")

CODE_RE = re.compile(r"^(C_)?([UT])_([0-9]+(?:\.[0-9]+)*)$")
_KIND = {"U": "user", "T": "technical"}
SENTINEL_NA = 32767  # 0x7FFF


def load_params(path: str = _MAP_PATH) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("parameters", {})
    except (OSError, json.JSONDecodeError):
        return {}


PARAMS = load_params()


def parse_code(name: str):
    if not isinstance(name, str):
        return None
    m = CODE_RE.match(name)
    if not m:
        return None
    cvar, kind, menu = m.groups()
    return {"code": name, "variant": "C" if cvar else "",
            "kind": _KIND.get(kind, kind), "menu": menu}


def meta(menu: str) -> dict:
    return PARAMS.get(menu, {})


def apply_scale(value, scale):
    try:
        if scale == "x10":
            return round(value / 10.0, 1)
        if scale == "x100":
            return value * 100
        if scale == "div60":
            return round(value / 60.0, 2)
    except (TypeError, ValueError):
        pass
    return value


def to_raw(value, scale) -> int:
    if scale == "x10":
        return int(round(float(value) * 10))
    if scale == "x100":
        return int(round(float(value) / 100))
    if scale == "div60":
        return int(round(float(value) * 60))
    return int(value)


def decode(menu: str, raw):
    """Return (value, enum_label) for a raw device value at a menu code."""
    m = meta(menu)
    if raw == SENTINEL_NA:
        return None, "N/A"
    scale = m.get("scale")
    if scale == "enum":
        return raw, (m.get("enum") or {}).get(str(raw))
    return apply_scale(raw, scale), None


def code_for(menu: str, prefix: str = "U_") -> str:
    return f"{prefix}{menu}"


def batch_metrics(codes, per: int = 16):
    """Yield {"P1": ("STRING", code), ...} dicts, <=16 codes each (for GET Menu/Par)."""
    codes = list(codes)
    for i in range(0, len(codes), per):
        chunk = codes[i:i + per]
        yield {f"P{j + 1}": ("STRING", c) for j, c in enumerate(chunk)}
