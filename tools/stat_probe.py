#!/usr/bin/env python3
"""Probe the gateway's `Stat/cWh` for *all* energy series, to find new Typ codes.

Read-only: it only sends GET Stat/cWh (never PUT/EXEC). We already know the gas
series (Typ 6 = heating, Typ 9 = DHW, Wh; see docs/06). This sweeps a range of
Typ codes and decodes whatever the device returns, so we can spot the
*electricity* series (and anything else) and match them to the app's energy
screen.

Transport: it shells out to `kura_client.py` once per request -- the exact
invocation that works by hand (a fresh process per request). Driving
send_request in a loop inside one process proved unreliable against this
gateway; a fresh kura_client process per request does not.

This gateway is fussy about Stat/cWh:
  * it does NOT answer a single-Typ request (even a valid one), and
  * it drops the whole reply if the request names a Typ it doesn't have, and
  * its reply is intermittent -- sometimes an empty body until stats are ready.
So we probe with **base + one candidate**: a known-good set (default 6,9) plus
one Typ under test. A valid candidate comes back as an extra series; an invalid
one drops the reply (no answer); an empty body is retried.

Usage (gateway on your local broker, Mode A):
    MQTT_HOST=192.168.42.20 tools/stat_probe.py --typs 1-40 --cafile broker/server.crt

idx 1 = hourly (today), idx 2 = daily (this month), idx 4 = monthly (~13 months).
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kura_client
import stat_decode

KURA = os.path.join(HERE, "kura_client.py")
# Confirmed by a full 1-40 sweep: only two distinct series exist, both gas, each
# under several alias Typ codes; 20/21 are burner runtime. No electricity series.
KNOWN = {1: "heating gas (Wh) [=6]", 6: "heating gas (Wh)", 30: "heating gas (Wh) [=6]",
         2: "DHW gas (Wh) [=9]", 9: "DHW gas (Wh)", 31: "DHW gas (Wh) [=9]",
         20: "heating burner runtime? (min)", 21: "DHW burner runtime? (min)"}


def _parse_typs(spec: str):
    out = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    return out


def _kura_get_stat(typ_str, args):
    """Run kura_client.py get Stat/cWh (a fresh process, as by hand). Returns the
    decoded reply dict, or None if there was no reply."""
    cmd = [sys.executable, KURA, "get", "Stat/cWh",
           "-m", f"Typ:STRING={typ_str}",
           "--host", str(args.host), "--port", str(args.port),
           "--client", args.client, "--account", args.account, "--app", args.app,
           "--timeout", str(args.timeout)]
    if args.cafile:
        cmd += ["--cafile", args.cafile]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    out = proc.stdout.strip()
    if not out:
        return None
    # kura_client prints one JSON object (possibly after stray warnings).
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line).get("reply")
            except json.JSONDecodeError:
                continue
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--typs", default="1-40", help="Typ codes to sweep, e.g. '1-40' or '6,9,12,16'")
    ap.add_argument("--base", default="6,9",
                    help="known-good Typs sent with every probe (the gateway needs them to reply)")
    ap.add_argument("--host", default=kura_client.DEFAULTS["host"])
    ap.add_argument("--port", default=kura_client.DEFAULTS["port"])
    ap.add_argument("--account", default=kura_client.DEFAULTS["account"])
    ap.add_argument("--client", default=kura_client.DEFAULTS["client"], help="device MAC / client id")
    ap.add_argument("--app", default=kura_client.DEFAULTS["app"])
    ap.add_argument("--cafile", default=None, help="broker cert (adds --insecure)")
    ap.add_argument("--timeout", type=int, default=15, help="per-request reply wait")
    ap.add_argument("--retries", type=int, default=3,
                    help="retries when a reply comes back empty (stats are intermittent)")
    ap.add_argument("--debug", action="store_true",
                    help="print each reply's status/body for diagnosis")
    args = ap.parse_args()

    base = _parse_typs(args.base)
    base_set = set(base)
    typs = _parse_typs(args.typs)
    found: dict = {}  # (typ, idx) -> values

    def _query_once(typ_list):
        """Return (status, series). status: 'none' (no reply), 'invalid' (the
        gateway answered rc=500 -- a Typ it doesn't have), 'empty' (rc=200 but no
        series yet -- stats not ready), 'ok' (series present)."""
        typ_str = ",".join(str(t) for t in typ_list)
        reply = _kura_get_stat(typ_str, args)
        if not reply:
            if args.debug:
                print(f"    [{typ_str}] no reply", file=sys.stderr)
            return "none", []
        rc = (reply.get("metrics") or {}).get("response.code")
        rc = rc.get("value") if isinstance(rc, dict) else rc
        body = reply.get("body", "")
        series = [s for s in stat_decode.decode_stat_body(bytes.fromhex(body))
                  if s["typ"] is not None and s["idx"] is not None] if body else []
        if args.debug:
            print(f"    [{typ_str}] rc={rc} body={len(body)//2}B series={len(series)}"
                  f" typs={sorted({s['typ'] for s in series})}", file=sys.stderr)
        if series:
            return "ok", series
        if rc == 500:
            return "invalid", []  # the gateway rejected a Typ it doesn't have
        return "empty", []

    def _query(typ_list):
        status, series = "none", []
        for _ in range(args.retries + 1):
            status, series = _query_once(typ_list)
            if status in ("ok", "invalid"):  # both are definitive, no retry
                return status, series
            time.sleep(1.5)
        return status, series

    status, probe0 = _query(base)
    if status != "ok":
        print(f"Base {args.base} returned '{status}' (not series). Confirm the "
              f"gateway answers by hand: kura_client.py get Stat/cWh "
              f"-m Typ:STRING={args.base} --cafile {args.cafile}", file=sys.stderr)
        return 1
    for s in probe0:
        found[(s["typ"], s["idx"])] = s["values"]
    print(f"  base {args.base}: {len(probe0)} series ok", file=sys.stderr)

    for typ in typs:
        if typ in base_set:
            continue
        status, got = _query(base + [typ])
        if status == "invalid":
            print(f"  +Typ {typ}: absent (rc 500)", file=sys.stderr)
            continue
        if status == "none":
            print(f"  +Typ {typ}: no reply (inconclusive)", file=sys.stderr)
            continue
        if status == "empty":
            print(f"  +Typ {typ}: empty rc 200 (inconclusive, re-probe)", file=sys.stderr)
            continue
        new = sorted({s["typ"] for s in got} - base_set)
        print(f"  +Typ {typ}: {len(got)} series  "
              f"{'NEW ' + str(new) if new else 'only base'}", file=sys.stderr)
        for s in got:
            found[(s["typ"], s["idx"])] = s["values"]

    if not found:
        print("No series returned.", file=sys.stderr)
        return 1

    print(f"\n{len(found)} series from Stat/cWh "
          f"(Typ {min(t for t, _ in found)}..{max(t for t, _ in found)}):\n")
    print(f"{'Typ':>4} {'idx':>4} {'len':>4} {'sum':>12} {'~kWh':>9}  values (head)   note")
    print("-" * 92)
    for (typ, idx) in sorted(found):
        vals = found[(typ, idx)]
        total = sum(vals)
        head = ", ".join(str(v) for v in vals[:8]) + (" …" if len(vals) > 8 else "")
        note = KNOWN.get(typ, "? (NEW)")
        print(f"{typ:>4} {idx:>4} {len(vals):>4} {total:>12} {total / 1000.0:>9.3f}  "
              f"[{head}]   {note}")
    print("\nMatch the ~kWh (or sum) against the app's energy screen to label each "
          "Typ. New codes -> docs/06 + energy.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
