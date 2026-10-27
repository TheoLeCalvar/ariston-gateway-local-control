"""Heating time program (weekly schedule) via the Func/TProg resource.

The schedule lives in the KuraPayload body (field 5001) and decodes as (confirmed
against the app, and byte-for-byte reproduced by build_tprog_body, docs/05):

    program {
      1: id
      2: slot_minutes          # 15 -> slots are 15 min
      3: profile {             # repeated, one per day-group
           1: days             # packed day numbers, 1=Mon .. 7=Sun
           2: { 1: slot, 2: state }   # repeated; state 1=comfort, 0=eco
         }
      4: comfort*10
      5: eco*10
      6..9: 0                  # reserved (zero in every capture)
    }

A transition slot * slot_minutes is the time of day, e.g. {28,1} with slot=15 ->
07:00 switch to comfort. The day list in profile.1 is what maps profiles to days:
[7,6] = weekend, [1,2,3,4,5] = weekdays (owner-confirmed).

decode_tprog_body() keeps the raw transitions and days (so a program round-trips
through build_tprog_body) and also derives human comfort windows. Writing to the
device is a PUT Func/TProg with a built body -- NOT yet exercised on hardware;
build_tprog_body only guarantees we can reconstruct what we read.
"""
from __future__ import annotations

try:  # package submodule (HA runtime)
    from . import kura
except ImportError:  # top-level import (standalone tests)
    import kura


def _hhmm(slot: int, step: int) -> str:
    m = int(slot) * int(step)
    return f"{m // 60:02d}:{m % 60:02d}"


def _windows(transitions, step):
    """Fold (slot, state) transitions into [start, end] comfort windows."""
    out = []
    start = None
    for slot, state in transitions:
        if state == 1:
            start = slot
        elif state == 0 and start is not None:
            out.append([_hhmm(start, step), _hhmm(slot, step)])
            start = None
    return out


def decode_tprog_body(body: bytes) -> list[dict]:
    """Decode a Func/TProg body into a list of programs (lossless + human view)."""
    programs = []
    for f, w, v in kura._read_fields(body):
        if f != 1 or w != 2:
            continue
        pid = step = comfort = eco = None
        reserved = {}
        profiles = []
        for pf, pw, pv in kura._read_fields(v):
            if pw == 0 and pf == 1:
                pid = pv
            elif pw == 0 and pf == 2:
                step = pv
            elif pw == 0 and pf == 4:
                comfort = pv
            elif pw == 0 and pf == 5:
                eco = pv
            elif pw == 0 and pf >= 6:
                reserved[pf] = pv
            elif pf == 3 and pw == 2:  # a day-group profile
                days = []
                transitions = []
                for tf, tw, tv in kura._read_fields(pv):
                    if tf == 1 and tw == 2:
                        days = list(bytes(tv))
                    elif tf == 2 and tw == 2:
                        sub = {sf: sv for sf, sw, sv in kura._read_fields(tv)
                               if sw == 0}
                        transitions.append([sub.get(1), sub.get(2)])
                profiles.append({"days": days, "transitions": transitions})
        step = step or 15
        for p in profiles:
            p["windows"] = _windows(p["transitions"], step)
        programs.append({
            "id": pid,
            "slot_minutes": step,
            "comfort": round(comfort / 10.0, 1) if comfort is not None else None,
            "eco": round(eco / 10.0, 1) if eco is not None else None,
            "profiles": profiles,
            "reserved": reserved,
        })
    return programs


def build_tprog_body(program: dict) -> bytes:
    """Rebuild a Func/TProg body from a decoded program (inverse of decode)."""
    inner = kura._key(1, 0) + kura._varint(int(program["id"]))
    inner += kura._key(2, 0) + kura._varint(int(program["slot_minutes"]))
    for p in program["profiles"]:
        prof = kura._ld(1, bytes(p["days"]))
        for slot, state in p["transitions"]:
            t = (kura._key(1, 0) + kura._varint(int(slot))
                 + kura._key(2, 0) + kura._varint(int(state)))
            prof += kura._ld(2, t)
        inner += kura._ld(3, prof)
    inner += kura._key(4, 0) + kura._varint(int(round(program["comfort"] * 10)))
    inner += kura._key(5, 0) + kura._varint(int(round(program["eco"] * 10)))
    for pf in sorted(program.get("reserved", {})):
        inner += kura._key(pf, 0) + kura._varint(int(program["reserved"][pf]))
    return kura._ld(1, inner)


def hhmm_to_slot(hhmm: str, step: int) -> int:
    """'06:30', 15 -> 26 (nearest slot)."""
    h, m = (int(x) for x in str(hhmm).split(":"))
    return int(round((h * 60 + m) / step))


def windows_to_transitions(windows, step: int) -> list[list[int]]:
    """[['06:30','08:30'], …] -> [[slot,1],[slot,0], …], sorted by time."""
    transitions = []
    for start, end in windows:
        transitions.append([hhmm_to_slot(start, step), 1])
        transitions.append([hhmm_to_slot(end, step), 0])
    transitions.sort(key=lambda t: t[0])
    return transitions


def apply_changes(program: dict, *, comfort=None, eco=None, profiles=None) -> dict:
    """Return a copy of a decoded program with the given changes applied.

    profiles is a list of {days: [1..7], windows: [[start, end], …]}; the windows
    are converted to transitions at the program's slot resolution."""
    out = {
        "id": program["id"],
        "slot_minutes": program["slot_minutes"],
        "comfort": program["comfort"],
        "eco": program["eco"],
        "profiles": [dict(p) for p in program["profiles"]],
        "reserved": dict(program.get("reserved", {})),
    }
    if comfort is not None:
        out["comfort"] = float(comfort)
    if eco is not None:
        out["eco"] = float(eco)
    if profiles is not None:
        step = out["slot_minutes"]
        out["profiles"] = [
            {"days": list(p["days"]),
             "transitions": windows_to_transitions(p["windows"], step)}
            for p in profiles
        ]
    return out


def decode_reply_body_hex(body_hex: str) -> dict | None:
    """Decode one program from a reply body; None if empty."""
    if not body_hex:
        return None
    progs = decode_tprog_body(bytes.fromhex(body_hex))
    return progs[0] if progs else None
