#!/usr/bin/env python3
"""Actively sweep the device over the LOCAL broker (Mode A) and record replies.

A real captured GET shows that this gateway addresses **named resources**, not
individual registers:

    topic    $EDC/ari/<MAC>/ar1/GET/Func/TProg
    metrics  request.id, requester.client.id (both added automatically),
             and  Id = "1"  (STRING -- selects the zone/instance)

Each reply bundles many hex-named register metrics, which ebus_params decodes.
So the useful unit to sweep is the **resource**, not the register.

Workflow: capture the app's GETs with the MITM + `analyze_capture.py --follow`
to learn the resource names, put them in a file (one per line), then sweep them
here. GET is read-only; this never writes. It is dry-run until --send.

    # read one resource right now, enriched with eBUS meanings:
    tools/kura_client.py get Func/TProg -m Id:STRING=1 --cafile broker/server.crt \\
        | tools/ebus_params.py --enrich

    # preview a resource sweep (sends nothing):
    tools/sweep_registers.py --resources Func/TProg,Func/Zone1

    # sweep for real once the gateway is on the local broker (Mode A):
    tools/sweep_registers.py --resources-file resources.txt \\
        --cafile broker/server.crt --send --out sweep.jsonl

A legacy register mode (--known/--ids/--range -> GET {hexname}) is kept in case
some resource turns out to be register-addressed, but the captured traffic uses
named resources, so prefer --resources / --resources-file.

Safety: GET only; refuses DEPLOY-V2 (via kura_client); --delay rate-limits;
dry-run unless --send, so you confirm the target list first.
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_client
import kura_encode
import ebus_params


def render(template, eid, flag, zone):
    """Fill a register-mode template with the available placeholders."""
    ctx = {
        "ebus_id": eid,
        "reg_le": ebus_params.build_name(eid)[:4].lower(),
        "hexname": ebus_params.build_name(eid, flag, zone),
        "zone": zone,
        "flag": flag,
    }
    return template.format(**ctx)


def _resource_names(args):
    names = []
    for item in args.resources:
        names += [r.strip() for r in item.split(",") if r.strip()]
    if args.resources_file:
        with open(args.resources_file) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    names.append(line)
    return names


def _resource_targets(args):
    targets = []
    for res in _resource_names(args):
        metrics = {}
        if not args.no_id:
            metrics["Id"] = ("STRING", str(args.id))
        for m in args.req_metric:
            name, value = kura_encode._parse_metric_arg(m)
            metrics[name] = value
        targets.append({"label": res, "resource": res, "metrics": metrics})
    return targets


def _register_ids(args):
    ids, seen = [], set()

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


def _register_targets(args):
    targets = []
    for eid in _register_ids(args):
        resource = render(args.resource, eid, args.flag, args.zone)
        metrics = {}
        for m in args.req_metric:
            name, _, tpl = m.partition("=")
            metrics[name] = render(tpl, eid, args.flag, args.zone)
        label = ebus_params.REGISTERS.get(eid, {}).get("name", "?")
        targets.append({"label": f"{eid} {label}", "resource": resource, "metrics": metrics})
    return targets


def build_targets(args):
    """Return an ordered list of {label, resource, metrics} to GET."""
    if args.resources or args.resources_file:
        return _resource_targets(args)
    return _register_targets(args)


def _summarize_reply(reply):
    """One-line 'name=value(hint)' summary of an enriched reply's metrics."""
    parts = []
    for n, m in reply.get("metrics", {}).items():
        v = m.get("value") if isinstance(m, dict) else m
        hint = ""
        if isinstance(m, dict) and isinstance(m.get("ebus"), dict) and m["ebus"].get("name"):
            hint = f"({m['ebus']['name']})"
        parts.append(f"{n}={v}{hint}")
    return ", ".join(parts)


def sweep(args):
    targets = build_targets(args)
    if not targets:
        sys.exit("no targets: use --resources/--resources-file (preferred), "
                 "or --known/--ids/--range")
    if args.limit:
        targets = targets[:args.limit]

    mode = "LIVE (sending GETs)" if args.send else "DRY-RUN (nothing sent)"
    print(f"# sweeping {len(targets)} targets on {args.host}:{args.port} -- {mode}",
          file=sys.stderr)
    if not args.send:
        print("# add --send once the gateway is on the local broker (Mode A)",
              file=sys.stderr)

    out = open(args.out, "a") if (args.out and args.send) else None
    answered = 0
    try:
        for i, t in enumerate(targets, 1):
            if not args.send:
                dr = kura_client.send_request(
                    "GET", t["resource"], t["metrics"], host=args.host, port=args.port,
                    account=args.account, client=args.client, app=args.app, dry_run=True)
                print(json.dumps({"label": t["label"], "req_topic": dr["req_topic"],
                                  "payload_hex": dr["payload_hex"]}, ensure_ascii=False))
                continue

            try:
                result = kura_client.send_request(
                    "GET", t["resource"], t["metrics"], host=args.host, port=args.port,
                    account=args.account, client=args.client, app=args.app,
                    cafile=args.cafile, timeout=args.timeout)
            except (ValueError, RuntimeError) as e:
                print(f"[{i}/{len(targets)}] {t['label']}: ERROR {e}", file=sys.stderr)
                continue

            reply = result.get("reply")
            rec = {"label": t["label"], "resource": result["resource"], "reply": reply}
            if reply and reply.get("metrics"):
                ebus_params._enrich_obj(reply)
                answered += 1
                print(f"[{i}/{len(targets)}] {t['label']}: {_summarize_reply(reply)}")
            else:
                print(f"[{i}/{len(targets)}] {t['label']}: (no reply)", file=sys.stderr)
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
        print(f"# done: {answered}/{len(targets)} targets answered", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(
        description="Active resource/register sweep over the local broker (Mode A)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)

    res = ap.add_argument_group("resource targets (preferred -- matches real GETs)")
    res.add_argument("--resources", action="append", default=[],
                     help="named resources, comma-separated (e.g. Func/TProg,Func/Zone1)")
    res.add_argument("--resources-file", help="file of resource names, one per line (# comments)")
    res.add_argument("--id", default="1", help="the Id request metric, STRING (default '1')")
    res.add_argument("--no-id", action="store_true", help="omit the Id request metric")

    reg = ap.add_argument_group("register targets (legacy -- GET {hexname})")
    reg.add_argument("--known", action="store_true",
                     help="probe every register in ebus_registers.json")
    reg.add_argument("--ids", action="append", default=[],
                     help="explicit eBUS IDs, comma-separated (e.g. 6810,6910,c04b)")
    reg.add_argument("--range", nargs=2, metavar=("START", "END"),
                     help="inclusive eBUS ID range, hex (e.g. 6800 6a00)")
    reg.add_argument("--step", type=int, default=1, help="range step (default 1)")
    reg.add_argument("--resource", default="{hexname}",
                     help="register-mode topic resource template (default '{hexname}')")
    reg.add_argument("--flag", type=lambda x: int(x, 0), default=0x00,
                     help="flag/sub-index byte for {hexname} (default 0x00)")
    reg.add_argument("--zone", type=lambda x: int(x, 0), default=0x01,
                     help="zone/instance byte for {hexname} (default 0x01)")

    ap.add_argument("--req-metric", action="append", default=[],
                    help="extra request metric name=value or name:TYPE=value (repeatable)")
    ap.add_argument("--limit", type=int, help="cap the number of probes")

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
