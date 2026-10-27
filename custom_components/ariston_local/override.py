"""Temporary temperature override (dérogation) via the Func/Der resource.

Captured format (data in the KuraPayload body, field 5001), one entry per zone:

    entry { 1: zone, 2: enable(1/0), 3: temp*10, 4: end-epoch (absolute, seconds) }

e.g. override zone 1 to 18.0 °C until epoch 1793124000:
    body = 0a0d 0801 1001 18b401 20a0cd83d706   (reproduced byte-for-byte)

GET Func/Der (with metric Z=<zone>) reads the current override back in the same
shape; the device reverts to the schedule on its own at the end epoch.
"""
from __future__ import annotations

try:  # package submodule (HA runtime)
    from . import kura
except ImportError:  # top-level import (standalone tests)
    import kura


def build_der_body(zone: int, enable: bool, temp_c: float, end_epoch: int) -> bytes:
    """Build the Func/Der body for one zone's override."""
    inner = (kura._key(1, 0) + kura._varint(int(zone))
             + kura._key(2, 0) + kura._varint(1 if enable else 0)
             + kura._key(3, 0) + kura._varint(int(round(temp_c * 10)))
             + kura._key(4, 0) + kura._varint(int(end_epoch)))
    return kura._ld(1, inner)


def parse_der_body(body: bytes) -> dict | None:
    """Parse the first entry of a Func/Der body into a dict, or None if empty."""
    for f, w, v in kura._read_fields(body):
        if f == 1 and w == 2:
            sub = {}
            for sf, sw, sv in kura._read_fields(v):
                if sw == 0:
                    sub[sf] = sv
            return {
                "zone": sub.get(1),
                "enabled": bool(sub.get(2)),
                "temp": round(sub.get(3, 0) / 10.0, 1) if sub.get(3) else None,
                "end": sub.get(4) or None,
            }
    return None


def parse_reply_body_hex(body_hex: str) -> dict | None:
    if not body_hex:
        return None
    return parse_der_body(bytes.fromhex(body_hex))
