#!/usr/bin/env python3
"""Probe the gateway's `Stat/cWh` for *all* energy series, to find new Typ codes.

Read-only: it only sends GET Stat/cWh (never PUT/EXEC). We already know the gas
series (Typ 6 = heating, Typ 9 = DHW, Wh; see docs/06). This sweeps a range of
Typ codes and decodes whatever the device returns, so we can spot the
*electricity* series (and anything else) and match them to the app's energy
screen.

This gateway is fussy about Stat/cWh:
  * it does NOT answer a single-Typ request (even a valid one), and
  * it drops the whole reply if the request names a Typ it doesn't have, and
  * its reply is intermittent -- often an empty body until the stats are "ready".
It is also sensitive to churn: a fresh subscribe per request (what kura_client
does) frequently misses the reply. So this tool keeps **one persistent
subscriber** on the REPLY wildcard (a background mosquitto_sub + reader thread)
and only re-publishes the request per Typ -- the reliable pattern HA uses.

We probe with **base + one candidate**: a known-good set (default 6,9) plus one
Typ under test. A valid candidate comes back as an extra series; an invalid one
drops the reply (seen as no answer); an empty body is retried.

Usage (gateway on your local broker, Mode A):
    MQTT_HOST=192.168.42.20 tools/stat_probe.py --typs 1-40 --cafile broker/server.crt

Then match the printed ~kWh/sum against the app's energy screen. idx 1 = hourly
(today), idx 2 = daily (this month), idx 4 = monthly (trailing ~13).
"""
import argparse
import os
import subprocess
import sys
import threading
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_client
import kura_encode
import kura_decode
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


class ReplyBus:
    """One long-lived mosquitto_sub on the REPLY wildcard; replies land here by
    request.id as they arrive, so a per-request publish never misses its answer."""

    def __init__(self, *, host, port, cafile, account, app, requester):
        self._replies: dict[str, str] = {}
        self._lock = threading.Lock()
        wildcard = kura_client.topic(account, requester, app, "REPLY", "#")
        tls = kura_client._tls_list(cafile)
        self._sub = subprocess.Popen(
            ["mosquitto_sub", "-h", host, "-p", str(port), *tls,
             "-t", wildcard, "-F", "%t | %x"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self._reader = threading.Thread(target=self._loop, daemon=True)
        self._reader.start()

    def _loop(self):
        for line in self._sub.stdout:
            line = line.strip()
            if not line:
                continue
            topic, _, hexs = line.partition(" | ")
            reqid = topic.rsplit("/", 1)[-1]
            with self._lock:
                self._replies[reqid] = hexs.strip()

    def wait(self, reqid, timeout):
        end = time.time() + timeout
        while time.time() < end:
            with self._lock:
                if reqid in self._replies:
                    return self._replies.pop(reqid)
            time.sleep(0.1)
        return None

    def close(self):
        self._sub.terminate()
        try:
            self._sub.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self._sub.kill()


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
    ap.add_argument("--settle", type=float, default=2.0,
                    help="seconds to let the persistent subscriber connect first")
    ap.add_argument("--debug", action="store_true",
                    help="print each reply's status/body for diagnosis")
    args = ap.parse_args()

    base = _parse_typs(args.base)
    base_set = set(base)
    typs = _parse_typs(args.typs)
    found: dict = {}  # (typ, idx) -> values

    requester = f"local-{uuid.uuid4().hex[:8]}"
    req_topic = kura_client.topic(args.account, args.client, args.app, "get", "Stat/cWh")
    tls = kura_client._tls_list(args.cafile)
    bus = ReplyBus(host=args.host, port=args.port, cafile=args.cafile,
                   account=args.account, app=args.app, requester=requester)
    time.sleep(args.settle)  # let the subscriber connect + subscribe

    def _query_once(typ_list):
        """Return (status, series): 'none' (no reply), 'empty' (reply, no series),
        'ok' (series present)."""
        reqid = uuid.uuid4().hex[:16]
        typ_str = ",".join(str(t) for t in typ_list)
        payload = {"Typ": ("STRING", typ_str),
                   "request.id": reqid, "requester.client.id": requester}
        data = kura_encode.encode_payload(payload, timestamp=None)
        pub = subprocess.run(
            ["mosquitto_pub", "-h", args.host, "-p", str(args.port), *tls,
             "-t", req_topic, "-s"], input=data, stderr=subprocess.PIPE)
        if pub.returncode != 0:
            print(f"    publish failed: {pub.stderr.decode().strip()}", file=sys.stderr)
            return "none", []
        hexs = bus.wait(reqid, args.timeout)
        if hexs is None:
            if args.debug:
                print(f"    [{typ_str}] no reply", file=sys.stderr)
            return "none", []
        dec = kura_decode.decode_payload(bytes.fromhex(hexs))
        body = dec.get("body", "")
        series = [s for s in stat_decode.decode_stat_body(bytes.fromhex(body))
                  if s["typ"] is not None and s["idx"] is not None] if body else []
        if args.debug:
            rc = (dec.get("metrics") or {}).get("response.code")
            print(f"    [{typ_str}] rc={rc} body={len(body)//2}B series={len(series)}"
                  f" typs={sorted({s['typ'] for s in series})}", file=sys.stderr)
        return ("ok" if series else "empty"), series

    def _query(typ_list):
        status, series = "none", []
        for _ in range(args.retries + 1):
            status, series = _query_once(typ_list)
            if status == "ok":
                return status, series
            time.sleep(1.5)
        return status, series

    try:
        status, probe0 = _query(base)
        if status != "ok":
            print(f"Base {args.base} returned '{status}' (not series) even with "
                  f"a persistent subscriber. Is the gateway attached to this broker "
                  f"(Mode A)? Try --settle 3 / --retries 5.", file=sys.stderr)
            return 1
        for s in probe0:
            found[(s["typ"], s["idx"])] = s["values"]
        print(f"  base {args.base}: {len(probe0)} series ok", file=sys.stderr)

        for typ in typs:
            if typ in base_set:
                continue
            status, got = _query(base + [typ])
            if status == "none":
                print(f"  +Typ {typ}: dropped (Typ invalid)", file=sys.stderr)
                continue
            if status == "empty":
                print(f"  +Typ {typ}: empty (inconclusive)", file=sys.stderr)
                continue
            new = sorted({s["typ"] for s in got} - base_set)
            print(f"  +Typ {typ}: {len(got)} series  "
                  f"{'NEW ' + str(new) if new else 'only base'}", file=sys.stderr)
            for s in got:
                found[(s["typ"], s["idx"])] = s["values"]
    finally:
        bus.close()

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
