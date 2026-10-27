#!/usr/bin/env python3
"""Write Ariston installer-menu parameters over the LOCAL broker (Mode A).

Matches the app's captured write: a PUT to Menu/Par carrying the parameter code
as the metric NAME with an INT32 value, e.g.  U_3 = 1  (Eté/Hiver/OFF -> Hiver).

You give human values; scaling from menu_params is applied automatically, so
`6.3.1.0.0=19.5` is sent as U_6.3.1.0.0 = 195 (x10). One PUT per parameter, like
the app. **This changes real settings on a real boiler**, so it is dry-run until
--send and confirms each write (unless --yes).

    # preview (sends nothing): show code, scaling, raw value
    tools/write_menu.py 6.3.1.0.0=19.5 3=1

    # write for real (gateway must be on the local broker; prompts to confirm)
    tools/write_menu.py 6.3.1.0.0=19.5 --cafile broker/server.crt --send

    # write and read the value back to confirm it took
    tools/write_menu.py 3=1 --cafile broker/server.crt --send --verify

Safety: writes only U_ codes (user params); refuses DEPLOY-V2; warns if a value
is outside the known settable set; dry-run + confirmation by default.
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_client
import menu_params


def parse_assignment(arg, prefix, raw_mode):
    """'6.3.1.0.0=19.5' -> (code, raw_int, human, known, warning)."""
    if "=" not in arg:
        sys.exit(f"bad assignment {arg!r}: expected MENU=VALUE")
    menu, _, value = arg.partition("=")
    menu = menu.strip()
    known = menu_params.PARAMS.get(menu, {})
    scale = known.get("scale")
    warning = None

    if raw_mode or scale in (None, "enum"):
        try:
            raw = int(value, 0)
        except ValueError:
            sys.exit(f"{menu}: value {value!r} must be an integer "
                     f"({'raw mode' if raw_mode else 'enum/unknown scale'})")
        human = raw
    else:
        raw = menu_params.to_raw(value, scale)
        human = value

    vals = known.get("values")
    if vals and raw not in vals:
        warning = f"value {raw} not in known settable set {vals}"
    code = f"{prefix}{menu}"
    return code, raw, human, known, warning


def _describe(code, raw, human, known, warning):
    label = known.get("label", "unknown parameter")
    unit = f" {known['unit']}" if known.get("unit") else ""
    human_s = f"{human}{unit}" if str(human) != str(raw) else f"{raw}"
    line = f"  {code:16} = {raw:<6} ({label}: {human_s})"
    if warning:
        line += f"   ⚠ {warning}"
    return line


def confirm(writes, assume_yes):
    sys.stderr.write("About to write to a REAL boiler:\n")
    for w in writes:
        sys.stderr.write(_describe(*w) + "\n")
    if assume_yes:
        return
    if not sys.stdin.isatty():
        sys.exit("refusing to write without --yes in non-interactive mode")
    if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("aborted")


def verify(code, args):
    """Read the code back via Menu/Par and print its current value."""
    res = kura_client.send_request(
        "GET", "Menu/Par", {"P1": ("STRING", code)}, host=args.host, port=args.port,
        account=args.account, client=args.client, app=args.app,
        cafile=args.cafile, timeout=args.timeout, timestamp=False)
    reply = res.get("reply")
    if reply and reply.get("metrics"):
        menu_params._enrich_obj(reply)
        m = reply["metrics"].get(code, {})
        note = m.get("menu_param", {}) if isinstance(m, dict) else {}
        shown = note.get("enum_label", note.get("scaled_value", m.get("value")))
        print(f"  verify {code} -> {m.get('value')} ({shown})")
    else:
        print(f"  verify {code} -> (no reply)")


def main():
    ap = argparse.ArgumentParser(
        description="Write Ariston installer-menu parameters (PUT Menu/Par, Mode A)",
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("assignments", nargs="+", metavar="MENU=VALUE",
                    help="e.g. 6.3.1.0.0=19.5  3=1")
    ap.add_argument("--prefix", default="U_", help="code prefix (default U_)")
    ap.add_argument("--raw", action="store_true",
                    help="values are raw device integers (skip scaling)")
    ap.add_argument("--send", action="store_true",
                    help="actually write (default: dry-run, print only)")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--verify", action="store_true",
                    help="read each value back after writing")

    conn = ap.add_argument_group("connection")
    conn.add_argument("--host", default=kura_client.DEFAULTS["host"])
    conn.add_argument("--port", default=kura_client.DEFAULTS["port"])
    conn.add_argument("--account", default=kura_client.DEFAULTS["account"])
    conn.add_argument("--client", default=kura_client.DEFAULTS["client"])
    conn.add_argument("--app", default=kura_client.DEFAULTS["app"])
    conn.add_argument("--cafile", default=None, help="broker cert (adds --insecure)")
    conn.add_argument("--timeout", type=int, default=5)
    conn.add_argument("--delay", type=float, default=0.5, help="seconds between writes")
    args = ap.parse_args()

    if not args.prefix.startswith("U_"):
        sys.exit("refusing to write a non-U_ code (T_ are technical reads); "
                 "pass --prefix U_ to write user parameters")

    writes = [parse_assignment(a, args.prefix, args.raw) for a in args.assignments]

    if not args.send:
        print("# DRY-RUN (nothing sent) -- add --send to write", file=sys.stderr)
        for w in writes:
            code, raw, _human, _known, warning = w
            if warning:
                print(f"# ⚠ {code}: {warning}", file=sys.stderr)
            dr = kura_client.send_request(
                "PUT", "Menu/Par", {code: ("INT32", raw)}, host=args.host, port=args.port,
                account=args.account, client=args.client, app=args.app,
                dry_run=True, timestamp=False)
            print(json.dumps({"put": code, "raw": raw, "req_topic": dr["req_topic"],
                              "payload_hex": dr["payload_hex"]}, ensure_ascii=False))
        return

    confirm(writes, args.yes)
    for i, (code, raw, human, known, warning) in enumerate(writes, 1):
        try:
            res = kura_client.send_request(
                "PUT", "Menu/Par", {code: ("INT32", raw)}, host=args.host, port=args.port,
                account=args.account, client=args.client, app=args.app,
                cafile=args.cafile, timeout=args.timeout, timestamp=False)
        except (ValueError, RuntimeError) as e:
            print(f"  {code}: ERROR {e}", file=sys.stderr)
            continue
        reply = res.get("reply") or {}
        code_ok = (reply.get("metrics", {}).get("response.code", {}) or {})
        rc = code_ok.get("value") if isinstance(code_ok, dict) else None
        print(f"  wrote {code} = {raw}  (response.code={rc})")
        if args.verify:
            time.sleep(0.3)
            verify(code, args)
        if i < len(writes):
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
