#!/usr/bin/env python3
"""Read Ariston installer-menu parameters over the LOCAL broker (Mode A).

Batches parameter codes into Menu/Par GETs (up to 16 per request, as the app
does), collects the replies, and prints each code decoded via menu_params.
Read-only; dry-run until --send.

    # preview the requests for every mapped parameter (sends nothing)
    tools/read_menu.py --all

    # read specific codes for real (gateway must be on the local broker)
    tools/read_menu.py U_6.3.1.0.0 U_6.9.0 --cafile broker/server.crt --send

    # read everything, user + technical variants, to a file
    tools/read_menu.py --all --prefix U_ --prefix T_ --cafile broker/server.crt \\
        --send --out menu.jsonl
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_client
import menu_params


def collect_codes(args):
    codes, seen = [], set()

    def add(c):
        if c and c not in seen:
            seen.add(c)
            codes.append(c)

    for c in args.codes:
        add(c)
    if args.file:
        with open(args.file) as f:
            for line in f:
                line = line.split("#", 1)[0].strip()
                if line:
                    add(line)
    if args.all:
        for prefix in (args.prefix or ["U_"]):
            for c in menu_params.all_codes(prefix):
                add(c)
    return codes


def _fmt(name, m):
    note = m.get("menu_param") if isinstance(m, dict) else None
    raw = m.get("value") if isinstance(m, dict) else m
    if not note:
        return f"    {name} = {raw}"
    disp = note.get("enum_label")
    if disp is None:
        disp = note.get("scaled_value", raw)
    unit = f" {note['unit']}" if note.get("unit") else ""
    label = note.get("label", note.get("menu", ""))
    return f"    {name:14} = {disp}{unit:3}  ({label})"


def run(args):
    codes = collect_codes(args)
    if not codes:
        sys.exit("no codes: pass codes, --file, or --all")

    batches = list(menu_params.batch_metrics(codes))
    mode = "LIVE" if args.send else "DRY-RUN (nothing sent)"
    print(f"# reading {len(codes)} codes in {len(batches)} Menu/Par request(s) "
          f"on {args.host}:{args.port} -- {mode}", file=sys.stderr)
    if not args.send:
        print("# add --send once the gateway is on the local broker (Mode A)",
              file=sys.stderr)

    out = open(args.out, "a") if (args.out and args.send) else None
    try:
        for bi, metrics in enumerate(batches, 1):
            if not args.send:
                dr = kura_client.send_request(
                    "GET", "Menu/Par", metrics, host=args.host, port=args.port,
                    account=args.account, client=args.client, app=args.app, dry_run=True)
                asked = [v[1] for v in metrics.values()]
                print(json.dumps({"batch": bi, "codes": asked,
                                  "req_topic": dr["req_topic"],
                                  "payload_hex": dr["payload_hex"]}, ensure_ascii=False))
                continue

            try:
                result = kura_client.send_request(
                    "GET", "Menu/Par", metrics, host=args.host, port=args.port,
                    account=args.account, client=args.client, app=args.app,
                    cafile=args.cafile, timeout=args.timeout)
            except (ValueError, RuntimeError) as e:
                print(f"[batch {bi}] ERROR {e}", file=sys.stderr)
                continue

            reply = result.get("reply")
            if not (reply and reply.get("metrics")):
                print(f"[batch {bi}] (no reply)", file=sys.stderr)
                continue
            menu_params._enrich_obj(reply)
            print(f"[batch {bi}]")
            for name, m in reply["metrics"].items():
                if name in ("response.code",):
                    continue
                print(_fmt(name, m))
            if out:
                out.write(json.dumps(reply, ensure_ascii=False, default=str) + "\n")
                out.flush()
            if bi < len(batches):
                time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n# interrupted", file=sys.stderr)
    finally:
        if out:
            out.close()


def main():
    ap = argparse.ArgumentParser(
        description="Read Ariston installer-menu parameters (Menu/Par, Mode A)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("codes", nargs="*", help="parameter codes, e.g. U_6.9.0 T_8.4.0")
    ap.add_argument("--file", help="file of codes, one per line (# comments)")
    ap.add_argument("--all", action="store_true", help="every code in the map")
    ap.add_argument("--prefix", action="append",
                    help="prefix(es) for --all (default U_; repeatable, e.g. U_ T_)")

    conn = ap.add_argument_group("connection")
    conn.add_argument("--host", default=kura_client.DEFAULTS["host"])
    conn.add_argument("--port", default=kura_client.DEFAULTS["port"])
    conn.add_argument("--account", default=kura_client.DEFAULTS["account"])
    conn.add_argument("--client", default=kura_client.DEFAULTS["client"])
    conn.add_argument("--app", default=kura_client.DEFAULTS["app"])
    conn.add_argument("--cafile", default=None, help="broker cert (adds --insecure)")

    run_g = ap.add_argument_group("run")
    run_g.add_argument("--send", action="store_true",
                       help="actually send (default: dry-run, print only)")
    run_g.add_argument("--delay", type=float, default=0.5,
                       help="seconds between batches (default 0.5)")
    run_g.add_argument("--timeout", type=int, default=5,
                       help="seconds to wait for each reply (default 5)")
    run_g.add_argument("--out", default=None, help="append replies as JSONL to this file")
    args = ap.parse_args()

    run(args)


if __name__ == "__main__":
    main()
