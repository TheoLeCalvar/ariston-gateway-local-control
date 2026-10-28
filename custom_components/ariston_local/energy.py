"""Decode the Ariston Stat/cWh energy reply (self-contained; tools/stat_decode.py is its CLI).

The energy data is in the KuraPayload body (field 5001): repeated entries
{1: idx, 2: Typ, 3: packed varint values}. One series per (Typ, idx).

Confirmed by matching a capture to the app's energy screen (see docs/06):
  Typ 6  = heating (chauffage) energy, Wh
  Typ 9  = DHW (eau chaude sanitaire) energy, Wh
  Typ 20 = heating burner runtime (minutes, ~188 Wh/min) [hypothesis]
  Typ 21 = DHW burner runtime (minutes) [hypothesis]

The idx selects a rolling window, oldest value first (worked out on 2026-10-28
by lining a live read up against the burner's on/off history in HA, with exact
cross-checks between the series):
  idx 1 = 24 two-hour blocks (48 h), newest last -- the only series with today
  idx 2 = recent *completed* days (20 seen), newest = yesterday
  idx 4 = 13 *completed* months, newest = last month
So the day and month series only take a period in once it has ended; a shower
this morning shows in idx 1 within its 2-hour block, and in idx 2 tomorrow.
"""
from __future__ import annotations

try:  # package submodule (HA runtime)
    from . import kura
except ImportError:  # top-level import (standalone tests)
    import kura

# Typ -> (key, human label). Energy types only (Wh); runtime types omitted.
TYP_HEATING = 6
TYP_DHW = 9

IDX_BLOCKS = 1   # 24 x 2-hour blocks, rolling 48 h (includes today)
IDX_DAILY = 2    # recent completed days, newest = yesterday
IDX_MONTHLY = 4  # 13 completed months, newest = last month

BLOCK_SECONDS = 2 * 3600  # one idx-1 block


def _varints(buf):
    vals, i = [], 0
    while i < len(buf):
        v, i = kura._read_varint(buf, i)
        vals.append(v)
    return vals


def decode_stat_series(body: bytes) -> list[dict]:
    """Return every [{"typ", "idx", "values"}] entry of a Stat/cWh body, in order.

    Lossless view (keeps entries with a missing Typ/idx and duplicates), as the
    tools print it; decode_stat_body() is the keyed view the component uses."""
    out = []
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
            out.append({"typ": typ, "idx": idx, "values": _varints(arr)})
    return out


def decode_stat_body(body: bytes) -> dict:
    """Return {(typ, idx): [values]} from a Stat/cWh body."""
    return {(s["typ"], s["idx"]): s["values"] for s in decode_stat_series(body)
            if s["typ"] is not None and s["idx"] is not None}


def decode_stat_payload(data: bytes) -> list[dict]:
    """Decode a full Stat/cWh reply payload (body in field 5001) into its series."""
    return decode_stat_series(bytes.fromhex(kura.decode_payload(data).get("body", "")))


def decode_reply_body_hex(body_hex: str) -> dict:
    """Decode the hex 'body' field from a decoded Stat/cWh reply."""
    if not body_hex:
        return {}
    return decode_stat_body(bytes.fromhex(body_hex))


def kwh(values) -> float:
    """Sum a Wh series into kWh (rounded)."""
    return round(sum(values) / 1000.0, 3)


def block_number(when) -> int:
    """The idx-1 block a local datetime falls in (blocks start on even local hours)."""
    offset = when.utcoffset()
    local = when.timestamp() + (offset.total_seconds() if offset else 0)
    return int(local // BLOCK_SECONDS)


def fold_blocks(prev, new, elapsed):
    """Energy added between two reads of the rolling 2-hour-block window.

    The device has no lifetime odometer, so the lifetime total is built from
    the idx-1 window: line the new read up with the previous one and add only
    what grew or is new. `elapsed` is how many blocks the clock says have
    passed since `prev` was read; the alignment tried is that, then one block
    fewer (the gateway rolls a little after the clock) or one more. An
    alignment is valid when no overlapping block shrank.

    Returns `(added_wh, shift)`, where `shift` is the number of blocks the
    window actually moved, or `(0, None)` when no alignment fits (a glitchy
    or partial reply: skip it and keep `prev`). With no `prev` (first read)
    the new window is only a baseline: `(0, 0)`.
    """
    n = len(new)
    if prev is None or len(prev) != n:
        return 0, 0
    elapsed = max(int(elapsed), 0)  # the local clock steps back at DST end
    if elapsed >= n + 1:  # gap longer than the window: everything is new
        return sum(new), elapsed
    for shift in dict.fromkeys((elapsed, max(elapsed - 1, 0), elapsed + 1)):
        if shift >= n:
            return sum(new), shift
        overlap = range(n - shift)
        if all(new[i] >= prev[i + shift] for i in overlap):
            grown = sum(new[i] - prev[i + shift] for i in overlap)
            return grown + sum(new[n - shift:]), shift
    return 0, None
