"""Decode/encode the Func/TProg body (heating weekly schedule).

Mirror of custom_components/ariston_local/tprog.py for the stdlib tools. Uses the
kura_decode field reader and the kura_encode protobuf writers.

    program { 1:id  2:slot_minutes  3:profile{ 1:days  2:{1:slot,2:state} … } …
              4:comfort*10  5:eco*10  6..9:0 }

profile.1 is the day group: packed day numbers, 1=Mon..7=Sun. A transition
{slot,state} is slot*slot_minutes into the day, state 1=comfort / 0=eco.
build_tprog_body() is the exact inverse of decode_tprog_body() (round-trips the
captured bodies byte-for-byte).
"""
from __future__ import annotations

import kura_decode
import kura_encode


def _hhmm(slot, step):
    m = int(slot) * int(step)
    return f"{m // 60:02d}:{m % 60:02d}"


def _windows(transitions, step):
    out, start = [], None
    for slot, state in transitions:
        if state == 1:
            start = slot
        elif state == 0 and start is not None:
            out.append([_hhmm(start, step), _hhmm(slot, step)])
            start = None
    return out


def decode_tprog_body(body: bytes) -> list[dict]:
    programs = []
    for f, w, v in kura_decode._read_fields(body):
        if f != 1 or w != 2:
            continue
        pid = step = comfort = eco = None
        reserved, profiles = {}, []
        for pf, pw, pv in kura_decode._read_fields(v):
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
            elif pf == 3 and pw == 2:
                days, transitions = [], []
                for tf, tw, tv in kura_decode._read_fields(pv):
                    if tf == 1 and tw == 2:
                        days = list(bytes(tv))
                    elif tf == 2 and tw == 2:
                        sub = {sf: sv for sf, sw, sv in kura_decode._read_fields(tv)
                               if sw == 0}
                        transitions.append([sub.get(1), sub.get(2)])
                profiles.append({"days": days, "transitions": transitions})
        step = step or 15
        for p in profiles:
            p["windows"] = _windows(p["transitions"], step)
        programs.append({
            "id": pid, "slot_minutes": step,
            "comfort": round(comfort / 10.0, 1) if comfort is not None else None,
            "eco": round(eco / 10.0, 1) if eco is not None else None,
            "profiles": profiles, "reserved": reserved,
        })
    return programs


def build_tprog_body(program: dict) -> bytes:
    k, vi, ld = kura_encode._key, kura_encode._varint, kura_encode._ld
    inner = k(1, 0) + vi(int(program["id"]))
    inner += k(2, 0) + vi(int(program["slot_minutes"]))
    for p in program["profiles"]:
        prof = ld(1, bytes(p["days"]))
        for slot, state in p["transitions"]:
            prof += ld(2, k(1, 0) + vi(int(slot)) + k(2, 0) + vi(int(state)))
        inner += ld(3, prof)
    inner += k(4, 0) + vi(int(round(program["comfort"] * 10)))
    inner += k(5, 0) + vi(int(round(program["eco"] * 10)))
    for pf in sorted(program.get("reserved", {})):
        inner += k(pf, 0) + vi(int(program["reserved"][pf]))
    return ld(1, inner)
