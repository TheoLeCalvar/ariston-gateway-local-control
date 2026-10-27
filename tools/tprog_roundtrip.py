#!/usr/bin/env python3
"""Reversible round-trip write test for the heating time program (Func/TProg).

Confirms the gateway accepts a PUT Func/TProg, without needing an app capture:

  1. GET the program (save the original body to a backup file).
  2. PUT it back UNCHANGED  -> expect response.code 200 (proves the PUT path).
  3. (optional) PUT a one-slot change, read back, show the diff.
  4. RESTORE the original body, read back, confirm it matches.

Dry-run by default: it prints the bodies it WOULD send and sends nothing until
--send. Even with --send it always restores the original last. This writes to a
real boiler; the change is a single 15-min nudge and is reverted immediately.

Examples:
  tools/tprog_roundtrip.py --id 1 --host 192.168.42.20 --port 8883 \
      --cafile broker/server.crt                      # dry run, shows plan
  tools/tprog_roundtrip.py --id 1 --host 192.168.42.20 --port 8883 \
      --cafile broker/server.crt --send               # identity write only
  tools/tprog_roundtrip.py --id 1 ... --send --change # + a reverted 15-min nudge
"""
import argparse
import copy
import sys
import time

import kura_client
import tprog

RESOURCE = "Func/TProg"


def _get(args, prog_id):
    r = kura_client.send_request(
        "GET", RESOURCE, {"Id": ("STRING", str(prog_id))},
        host=args.host, port=args.port, account=args.account,
        client=args.client, app=args.app, cafile=args.cafile,
        timeout=args.timeout, timestamp=False)
    reply = r.get("reply")
    if not reply:
        sys.exit(f"no reply to GET {RESOURCE} Id={prog_id} ({r.get('note','')})")
    body_hex = reply.get("body") or ""
    if not body_hex:
        sys.exit(f"GET {RESOURCE} Id={prog_id} returned no body")
    return body_hex


def _put_body(args, body_hex):
    r = kura_client.send_request(
        "PUT", RESOURCE, {}, host=args.host, port=args.port,
        account=args.account, client=args.client, app=args.app,
        cafile=args.cafile, timeout=args.timeout, timestamp=False,
        body=bytes.fromhex(body_hex))
    reply = r.get("reply") or {}
    rc = (reply.get("metrics") or {}).get("response.code")
    return rc.get("value") if isinstance(rc, dict) else rc


def _summ(prog):
    return "; ".join(
        f"days{p['days']} " + ",".join(f"{a}-{b}" for a, b in p["windows"])
        for p in prog["profiles"])


def _nudged(prog):
    """Return a copy with the first profile's first transition moved +1 slot."""
    out = copy.deepcopy(prog)
    for p in out["profiles"]:
        if p["transitions"]:
            p["transitions"][0][0] += 1  # +1 slot = +15 min
            p["windows"] = tprog._windows(p["transitions"], out["slot_minutes"])
            return out
    sys.exit("no transition to nudge")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", type=int, required=True, help="time-program Id (e.g. 1)")
    ap.add_argument("--host", default=kura_client.DEFAULTS["host"])
    ap.add_argument("--port", default="8883")
    ap.add_argument("--account", default=kura_client.DEFAULTS["account"])
    ap.add_argument("--client", default=kura_client.DEFAULTS["client"],
                    help="gateway MAC / client id")
    ap.add_argument("--app", default=kura_client.DEFAULTS["app"])
    ap.add_argument("--cafile", default=None, help="broker cert (adds --insecure)")
    ap.add_argument("--timeout", type=int, default=10)
    ap.add_argument("--send", action="store_true", help="actually write (else dry-run)")
    ap.add_argument("--change", action="store_true",
                    help="also do a reverted one-slot change (needs --send)")
    args = ap.parse_args()

    # 1. read + backup
    original = _get(args, args.id)
    prog = tprog.decode_tprog_body(bytes.fromhex(original))[0]
    backup = f"tprog-backup-{args.id}-{int(time.time())}.hex"
    with open(backup, "w") as fh:
        fh.write(original + "\n")
    print(f"[read]    Id={args.id}: {_summ(prog)}")
    print(f"          comfort={prog['comfort']} eco={prog['eco']}  "
          f"original body saved to {backup}")

    rebuilt = tprog.build_tprog_body(prog).hex()
    if rebuilt != original:
        sys.exit(f"ABORT: encoder does not reproduce this body exactly\n"
                 f"  orig: {original}\n  back: {rebuilt}")
    print("[check]   encoder reproduces the body byte-for-byte")

    changed = _nudged(prog)
    if not args.send:
        print("\n-- DRY RUN (no writes). Would send: --")
        print(f"  identity PUT body = {rebuilt}")
        if args.change:
            print(f"  changed  PUT body = {tprog.build_tprog_body(changed).hex()}")
            print(f"  change   = first window start +15 min -> {_summ(changed)}")
        print("\nRe-run with --send to perform the (reversible) write.")
        return

    # 2. identity write
    rc = _put_body(args, rebuilt)
    print(f"[put =]   identity PUT -> response.code={rc}")
    if rc not in (200, None):
        sys.exit(f"identity write rejected (code {rc}); nothing changed")
    if _get(args, args.id) != original:
        print("[warn]    read-back differs after identity write (see backup)")

    # 3. optional one-slot change + read-back
    if args.change:
        rc = _put_body(args, tprog.build_tprog_body(changed).hex())
        print(f"[put Δ]   changed PUT  -> response.code={rc}")
        back = tprog.decode_tprog_body(bytes.fromhex(_get(args, args.id)))[0]
        print(f"[read]    now: {_summ(back)}")

    # 4. restore
    rc = _put_body(args, original)
    ok = _get(args, args.id) == original
    print(f"[restore] original PUT -> response.code={rc}  restored={ok}")
    if not ok:
        print(f"[warn]    restore mismatch — original body is in {backup}:")
        print(f"          tools/tprog_roundtrip.py (or kura_client PUT) that hex")


if __name__ == "__main__":
    main()
