"""Heating time program (weekly schedule) via the Func/TProg resource.

Read-only for now. The schedule lives in the KuraPayload body (field 5001) and
decodes as (confirmed against the app, docs/05):

    program {
      1: id
      2: slot           # minute resolution of a slot (15 -> slots are 15 min)
      3: profile {      # repeated, one per day-type
           1: {}        # day assignment (empty in captures; mapping TBC)
           2: { 1: slot_index, 2: state }   # repeated; state 1=comfort, 0=eco
         }
      4: comfort*10
      5: eco*10
    }

A transition slot_index * slot minutes is the time of day, e.g. {28,1} with
slot=15 -> 07:00 switch to comfort. Writing is NOT implemented: we have only
captured reads, so the PUT body shape and the day->profile mapping are unconfirmed.
"""
from __future__ import annotations

try:  # package submodule (HA runtime)
    from . import kura
except ImportError:  # top-level import (standalone tests)
    import kura


def _hhmm(slot: int, step: int) -> str:
    m = int(slot) * int(step)
    return f"{m // 60:02d}:{m % 60:02d}"


def _subfields(buf):
    """{field_no: value} for the varint fields of a submessage."""
    return {f: v for f, w, v in kura._read_fields(buf) if w == 0}


def decode_tprog_body(body: bytes) -> list[dict]:
    """Decode a Func/TProg body into a list of programs with human times."""
    programs = []
    for f, w, v in kura._read_fields(body):
        if f != 1 or w != 2:
            continue
        pid = step = comfort = eco = None
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
            elif pf == 3 and pw == 2:  # a day-type profile
                transitions = []
                for tf, tw, tv in kura._read_fields(pv):
                    if tf == 2 and tw == 2:
                        sub = _subfields(tv)
                        transitions.append((sub.get(1), sub.get(2)))
                profiles.append(transitions)
        step = step or 15
        # Fold each profile's (slot, state) transitions into comfort windows.
        windows = []
        for transitions in profiles:
            comfort_windows = []
            start = None
            for slot, state in transitions:
                if state == 1:
                    start = slot
                elif state == 0 and start is not None:
                    comfort_windows.append([_hhmm(start, step), _hhmm(slot, step)])
                    start = None
            windows.append(comfort_windows)
        programs.append({
            "id": pid,
            "slot_minutes": step,
            "comfort": round(comfort / 10.0, 1) if comfort is not None else None,
            "eco": round(eco / 10.0, 1) if eco is not None else None,
            "profiles": windows,
        })
    return programs


def decode_reply_body_hex(body_hex: str) -> dict | None:
    """Decode one program from a reply body; None if empty."""
    if not body_hex:
        return None
    progs = decode_tprog_body(bytes.fromhex(body_hex))
    return progs[0] if progs else None
