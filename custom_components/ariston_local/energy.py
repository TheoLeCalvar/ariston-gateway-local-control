"""Decode the Ariston Stat/cWh energy reply (self-contained; see tools/stat_decode.py).

The energy data is in the KuraPayload body (field 5001): repeated entries
{1: idx, 2: Typ, 3: packed varint values}. One series per (Typ, idx).

Confirmed by matching a capture to the app's energy screen (see docs/06):
  Typ 6  = heating (chauffage) energy, Wh
  Typ 9  = DHW (eau chaude sanitaire) energy, Wh
  Typ 20 = heating burner runtime (minutes, ~188 Wh/min) [hypothesis]
  Typ 21 = DHW burner runtime (minutes) [hypothesis]
  idx 1 = hourly (today), idx 2 = daily (this month), idx 4 = monthly (trailing year)
"""
from __future__ import annotations

try:  # package submodule (HA runtime)
    from . import kura
except ImportError:  # top-level import (standalone tests)
    import kura

# Typ -> (key, human label). Energy types only (Wh); runtime types omitted.
TYP_HEATING = 6
TYP_DHW = 9

IDX_HOURLY = 1   # 24 buckets, today
IDX_DAILY = 2    # ~month of daily buckets
IDX_MONTHLY = 4  # ~13 monthly buckets, trailing year


def _varints(buf):
    vals, i = [], 0
    while i < len(buf):
        v, i = kura._read_varint(buf, i)
        vals.append(v)
    return vals


def decode_stat_body(body: bytes) -> dict:
    """Return {(typ, idx): [values]} from a Stat/cWh body."""
    out: dict = {}
    for f, w, v in kura._read_fields(body):
        if f == 1 and w == 2:
            idx = typ = None
            arr = b""
            for sf, sw, sv in kura._read_fields(v):
                if sf == 1 and sw == 0:
                    idx = sv
                elif sf == 2 and sw == 0:
                    typ = sv
                elif sf == 3 and sw == 2:
                    arr = sv
            if typ is not None and idx is not None:
                out[(typ, idx)] = _varints(arr)
    return out


def decode_reply_body_hex(body_hex: str) -> dict:
    """Decode the hex 'body' field from a decoded Stat/cWh reply."""
    if not body_hex:
        return {}
    return decode_stat_body(bytes.fromhex(body_hex))


def kwh(values) -> float:
    """Sum a Wh series into kWh (rounded)."""
    return round(sum(values) / 1000.0, 3)


def fold_total(total, last, last_month, current, now_month):
    """Advance a monotonic lifetime total from a within-month accumulator.

    The device has no lifetime odometer; the finest reliable series we poll is
    the *this-month* accumulator (idx 2), which grows through the month and
    resets to ~0 when a new month starts. We turn that into a counter that only
    ever increases (what the HA Energy dashboard needs):

    - first reading: adopt it as the baseline, add nothing;
    - same calendar month and the reading went up: add the increment;
    - same month and the reading dipped: ignore it (a glitchy/partial reply),
      keep the high-water `last` so a recovery only adds what is genuinely new;
    - the calendar month changed: a real reset, so add the whole new-month
      figure. (The only loss is energy used between the last poll of the old
      month and the rollover — a few minutes at a 10-min poll.)

    `now_month` / `last_month` are (year, month) tuples. Returns the updated
    `(total, last, last_month)`.
    """
    if current is None:
        return total, last, last_month
    if last is None:
        return total, current, now_month
    if last_month == now_month:
        if current > last:
            total += current - last
            last = current
    else:  # calendar month rolled over -> device reset to the new month
        total += current
        last, last_month = current, now_month
    return round(total, 6), last, last_month
