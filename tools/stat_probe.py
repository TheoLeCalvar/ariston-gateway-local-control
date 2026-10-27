#!/usr/bin/env python3
"""Probe the gateway's `Stat/cWh` for *all* energy series, to find new Typ codes.

Read-only: it only sends GET Stat/cWh (never PUT/EXEC). We already know the gas
series (Typ 6 = heating, Typ 9 = DHW, Wh; see docs/06). This sweeps a range of
Typ codes and decodes whatever the device returns, so we can spot the
*electricity* series (and anything else) and match them to the app's energy
screen. For each (Typ, idx) it prints the series length, its sum, a kWh guess
(sum/1000), and the first values.

This gateway is picky about Stat/cWh requests:
  * it does NOT answer a single-Typ request (even a valid one like 6), and
  * it drops the whole reply if the request names any Typ it doesn't have.
So we probe with **base + one candidate**: a known-good set (default 6,9,20,21,
the exact set the app uses) plus one Typ under test. A valid candidate comes
back as an extra series; an invalid one makes the gateway drop the reply (seen
as no answer). Absent codes each wait out the timeout, so a full sweep takes a
couple of minutes.

Usage (against the gateway on your local broker, Mode A):
    MQTT_HOST=127.0.0.1 MQTT_PORT=8883 tools/stat_probe.py
    tools/stat_probe.py --client AABBCCDDEEFF --typs 1-40 --batch 8
    tools/stat_probe.py --cafile broker/certs/ca.crt        # if the broker cert is verified

Then open the app's energy screen (GAZ + ELECTRICITE, Chauffage / Eau chaude),
note the kWh it shows, and match them to the Typ/idx below. idx 1 = hourly
(today, 24), idx 2 = daily (this month), idx 4 = monthly (trailing ~13).
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_client
import stat_decode

KNOWN = {6: "heating gas (Wh)", 9: "DHW gas (Wh)",
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
    ap.add_argument("--timeout", type=int, default=15, help="per-Typ reply wait")
    ap.add_argument("--retries", type=int, default=3,
                    help="retries when a reply comes back empty (stats are occasional)")
    ap.add_argument("--debug", action="store_true",
                    help="print each reply's status/body for diagnosis")
    args = ap.parse_args()

    base = _parse_typs(args.base)
    typs = _parse_typs(args.typs)
    found: dict = {}  # (typ, idx) -> values

    def _query_once(typ_list):
        """Return (status, series). status: 'none' (no MQTT reply), 'empty'
        (reply but no series), 'ok' (series present)."""
        typ_str = ",".join(str(t) for t in typ_list)
        res = kura_client.send_request(
            "get", "Stat/cWh", {"Typ": ("STRING", typ_str)},
            host=args.host, port=args.port, account=args.account,
            client=args.client, app=args.app, cafile=args.cafile,
            timeout=args.timeout)
        reply = res.get("reply")
        if not reply:
            if args.debug:
                print(f"    [{typ_str}] no MQTT reply ({res.get('note', '')})", file=sys.stderr)
            return "none", []
        body = reply.get("body", "")
        series = [s for s in stat_decode.decode_stat_body(bytes.fromhex(body))
                  if s["typ"] is not None and s["idx"] is not None]
        if args.debug:
            rc = (reply.get("metrics") or {}).get("response.code")
            print(f"    [{typ_str}] rc={rc} body={len(body)//2}B series={len(series)}"
                  f" typs={sorted({s['typ'] for s in series})}", file=sys.stderr)
        return ("ok" if series else "empty"), series

    def _query(typ_list):
        """Retry while the gateway answers but with an empty body."""
        for attempt in range(args.retries + 1):
            status, series = _query_once(typ_list)
            if status == "ok":
                return status, series
            if status == "none" and attempt == 0:
                # a dropped reply (invalid Typ) won't become non-empty; one more
                # try guards a genuine miss, then give up.
                pass
            time.sleep(2)
        return status, series

    # Self-test: the base alone must reply with series, or nothing below works.
    base_set = set(base)
    status, probe0 = _query(base)
    if status != "ok":
        print(f"Base {args.base} returned '{status}' (not series). The gateway "
              f"answers Stat/cWh only intermittently; re-run, or raise --retries. "
              f"Confirm manually: kura_client.py get Stat/cWh -m Typ:STRING={args.base}",
              file=sys.stderr)
        return 1
    for s in probe0:
        found[(s["typ"], s["idx"])] = s["values"]
    print(f"  base {args.base}: {len(probe0)} series ok", file=sys.stderr)

    for typ in typs:
        if typ in base_set:
            continue
        try:
            status, got = _query(base + [typ])
        except Exception as err:  # noqa: BLE001
            print(f"  +Typ {typ}: request failed: {err}", file=sys.stderr)
            continue
        if status == "none":
            print(f"  +Typ {typ}: dropped (Typ invalid)", file=sys.stderr)
            continue
        if status == "empty":
            print(f"  +Typ {typ}: empty reply (inconclusive, re-probe)", file=sys.stderr)
            continue
        new = sorted({s["typ"] for s in got} - base_set)
        tag = f"NEW {new}" if new else "only base"
        print(f"  +Typ {typ}: {len(got)} series  {tag}", file=sys.stderr)
        for s in got:
            found[(s["typ"], s["idx"])] = s["values"]

    if not found:
        print("No series returned. Is the gateway connected to the broker "
              "(Mode A, power-cycled)?", file=sys.stderr)
        return 1

    print(f"\n{len(found)} series from Stat/cWh "
          f"(Typ {min(t for t, _ in found)}..{max(t for t, _ in found)}):\n")
    print(f"{'Typ':>4} {'idx':>4} {'len':>4} {'sum':>12} {'~kWh':>9}  values (head)   note")
    print("-" * 92)
    for (typ, idx) in sorted(found):
        vals = found[(typ, idx)]
        total = sum(vals)
        head = ", ".join(str(v) for v in vals[:8]) + (" …" if len(vals) > 8 else "")
        note = KNOWN.get(typ, "?")
        print(f"{typ:>4} {idx:>4} {len(vals):>4} {total:>12} {total / 1000.0:>9.3f}  "
              f"[{head}]   {note}")
    print("\nMatch the ~kWh (or sum) against the app's energy screen to label each "
          "Typ (electricity vs gas, heating vs DHW). New codes -> docs/06 + energy.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
