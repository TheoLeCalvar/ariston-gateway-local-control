#!/usr/bin/env python3
"""Actively sweep eBUS registers over the LOCAL broker (Mode A) and record which
ones answer and with what value. GET-only, rate-limited, dry-run by default.

This probes the device directly, so it only makes sense in Mode A (the gateway
is connected to your local broker, the Ariston cloud is not in the loop). It does
NOT write anything: it sends GET requests, waits for the matching REPLY, decodes
it, and annotates recognized registers via ebus_params.

    !!  You MUST tell it how a GET request is shaped on this device  !!

We know the hex metric-name layout (register LE + flag + 0xFE + zone), but not
how the cloud expresses "read register X" as a GET: the resource path and/or the
request metrics. Capture one real GET with the MITM proxy (docs/03), then set:

    --resource  TEMPLATE   the topic resource after .../GET/  (default "{hexname}")
    --req-metric name=TPL  request metric(s) to include       (repeatable)

Templates may use the placeholders  {hexname} {ebus_id} {reg_le} {zone} {flag}.
Example, once you know the format:

    tools/sweep_registers.py --known --cafile broker/server.crt \\
        --resource '{hexname}' --send

Safety:
  * GET only; PUT/EXEC are not reachable from here.
  * refuses anything touching the DEPLOY-V2 firmware channel (via kura_client).
  * dry-run unless --send is given, so you confirm the request shape first.
  * --delay rate-limits; --limit caps the number of probes.
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_client
import ebus_params


def build_targets(args):
    """Return an ordered, de-duplicated list of eBUS IDs to probe."""
    ids = []
    seen = set()

    def add(eid):
        eid = eid.lower()
        if eid not in seen:
            seen.add(eid)
            ids.append(eid)

    if args.known:
        for eid in sorted(ebus_params.REGISTERS):
            add(eid)
    for item in args.ids:
        for eid in item.split(","):
            if eid.strip():
                add(f"{int(eid, 16):04x}")
    if args.range:
        for eid in ebus_params.iter_ebus_ids(args.range[0], args.range[1], args.step):
            add(eid)
    return ids


def render(template, eid, flag, zone):
    ctx = {
        "ebus_id": eid,
        "reg_le": ebus_params.build_name(eid)[:4].lower(),
        "hexname": ebus_params.build_name(eid, flag, zone),
        "zone": zone,
        "flag": flag,
    }
    return template.format(**ctx)


def sweep(args):
    targets = build_targets(args)
    if not targets:
        sys.exit("no registers selected: use --known, --ids, and/or --range")
    if args.limit:
        targets = targets[:args.limit]

    mode = "DRY-RUN (nothing sent)" if not args.send else "LIVE (sending GETs)"
    print(f"# sweeping {len(targets)} registers on {args.host}:{args.port} -- {mode}",
          file=sys.stderr)
    if not args.send:
        print("# add --send once you've confirmed --resource/--req-metric against "
              "a real captured GET", file=sys.stderr)

    out = open(args.out, "a") if (args.out and args.send) else None
    answered = 0
    try:
        for i, eid in enumerate(targets, 1):
            resource = render(args.resource, eid, args.flag, args.zone)
            metrics = {}
            for m in args.req_metric:
                name, _, tpl = m.partition("=")
                metrics[name] = render(tpl, eid, args.flag, args.zone)

            known = ebus_params.REGISTERS.get(eid, {})
            label = known.get("name", "?")

            if not args.send:
                dr = kura_client.send_request(
                    "GET", resource, metrics, host=args.host, port=args.port,
                    account=args.account, client=args.client, app=args.app,
                    dry_run=True)
                print(json.dumps({"ebus_id": eid, "name": label,
                                  "req_topic": dr["req_topic"],
                                  "payload_hex": dr["payload_hex"]}, ensure_ascii=False))
                continue

            try:
                result = kura_client.send_request(
                    "GET", resource, metrics, host=args.host, port=args.port,
                    account=args.account, client=args.client, app=args.app,
                    cafile=args.cafile, timeout=args.timeout)
            except (ValueError, RuntimeError) as e:
                print(f"[{i}/{len(targets)}] {eid} {label}: ERROR {e}", file=sys.stderr)
                continue

            reply = result.get("reply")
            rec = {"ebus_id": eid, "name": label, "resource": result["resource"],
                   "reply": reply}
            if reply and reply.get("metrics"):
                ebus_params._enrich_obj(reply)
                answered += 1
                vals = ", ".join(
                    f"{n}={m.get('value') if isinstance(m, dict) else m}"
                    for n, m in reply["metrics"].items())
                print(f"[{i}/{len(targets)}] {eid} {label}: {vals}")
            else:
                print(f"[{i}/{len(targets)}] {eid} {label}: (no reply)", file=sys.stderr)
            if out:
                out.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                out.flush()

            if i < len(targets):
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n# interrupted", file=sys.stderr)
    finally:
        if out:
            out.close()

    if args.send:
        print(f"# done: {answered}/{len(targets)} registers answered", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Active eBUS register sweep over the local broker (Mode A)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    sel = ap.add_argument_group("register selection")
    sel.add_argument("--known", action="store_true",
                     help="probe every register in ebus_registers.json")
    sel.add_argument("--ids", action="append", default=[],
                     help="explicit eBUS IDs, comma-separated (e.g. 6810,6910,c04b)")
    sel.add_argument("--range", nargs=2, metavar=("START", "END"),
                     help="inclusive eBUS ID range, hex (e.g. 6800 6a00)")
    sel.add_argument("--step", type=int, default=1, help="range step (default 1)")
    sel.add_argument("--limit", type=int, help="cap the number of probes")

    req = ap.add_argument_group("request shape (set from a real captured GET)")
    req.add_argument("--resource", default="{hexname}",
                     help="topic resource after .../GET/ (default '{hexname}')")
    req.add_argument("--req-metric", action="append", default=[],
                     help="request metric name=template (repeatable)")
    req.add_argument("--flag", type=lambda x: int(x, 0), default=0x00,
                     help="flag/sub-index byte for {hexname} (default 0x00)")
    req.add_argument("--zone", type=lambda x: int(x, 0), default=0x01,
                     help="zone/instance byte for {hexname} (default 0x01)")

    conn = ap.add_argument_group("connection")
    conn.add_argument("--host", default=kura_client.DEFAULTS["host"])
    conn.add_argument("--port", default=kura_client.DEFAULTS["port"])
    conn.add_argument("--account", default=kura_client.DEFAULTS["account"])
    conn.add_argument("--client", default=kura_client.DEFAULTS["client"])
    conn.add_argument("--app", default=kura_client.DEFAULTS["app"])
    conn.add_argument("--cafile", default=None, help="broker cert (adds --insecure)")

    run = ap.add_argument_group("run")
    run.add_argument("--send", action="store_true",
                     help="actually send GETs (default: dry-run, print only)")
    run.add_argument("--delay", type=float, default=0.5,
                     help="seconds between probes (default 0.5)")
    run.add_argument("--timeout", type=int, default=5,
                     help="seconds to wait for each reply (default 5)")
    run.add_argument("--out", default=None, help="append results as JSONL to this file")
    args = ap.parse_args()

    sweep(args)


if __name__ == "__main__":
    main()
