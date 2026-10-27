#!/usr/bin/env python3
"""Drive the gateway from the local broker (Mode A): send GET/PUT/EXEC and read
the matching REPLY, using the Kapua request/response convention.

Dependency-free: shells out to mosquitto_pub / mosquitto_sub (already required by
the repo), encodes requests with kura_encode and decodes replies with kura_decode.

Kapua request/response works like this:
  * the requester subscribes to  $EDC/<acct>/<client>/<app>/REPLY/<request.id>
  * and publishes the request to  $EDC/<acct>/<client>/<app>/<VERB>/<resource>
    carrying metrics  request.id  and  requester.client.id
  * the device answers on the REPLY topic with a  response.code  metric.

Examples:
    # read a resource
    tools/kura_client.py get 310/0/0

    # read, adding request metrics
    tools/kura_client.py get 72/0/0 -m foo=1

    # write a setpoint (PUT) -- this changes the real boiler, so it asks first
    tools/kura_client.py put 310/0/0 -m value:DOUBLE=21.5

    # run an exec command
    tools/kura_client.py exec reboot

Safety: PUT and EXEC mutate the device. Both require --yes or an interactive
confirmation. There is a hard refusal to target the DEPLOY-V2 firmware channel.
"""
import os
import sys
import json
import uuid
import time
import shlex
import argparse
import subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kura_encode
import kura_decode

DEFAULTS = {
    "account": "ari",
    "client": "AABBCCDDEEFF",
    "app": "ar1",
    "host": os.environ.get("MQTT_HOST", "127.0.0.1"),
    "port": os.environ.get("MQTT_PORT", "8883"),
}


def topic(account, client, app, verb, resource=""):
    base = f"$EDC/{account}/{client}/{app}/{verb}"
    return f"{base}/{resource}" if resource else base


def _tls_args(args):
    out = []
    if args.cafile:
        out += ["--cafile", args.cafile, "--insecure"]
    return out


def request(args, verb, resource, metrics):
    if "DEPLOY-V2" in f"{verb}/{resource}".upper():
        sys.exit("refusing to target the DEPLOY-V2 firmware channel")

    req_id = uuid.uuid4().hex[:16]
    requester = f"local-{uuid.uuid4().hex[:8]}"
    reply_topic = topic(args.account, requester, args.app, "REPLY", req_id)
    req_topic = topic(args.account, args.client, args.app, verb, resource)

    payload = dict(metrics)
    payload.setdefault("request.id", req_id)
    payload.setdefault("requester.client.id", requester)
    data = kura_encode.encode_payload(payload)

    tls = _tls_args(args)
    sub_cmd = ["mosquitto_sub", "-h", args.host, "-p", str(args.port), *tls,
               "-t", reply_topic, "-F", "%t | %x", "-C", "1", "-W", str(args.timeout)]
    pub_cmd = ["mosquitto_pub", "-h", args.host, "-p", str(args.port), *tls,
               "-t", req_topic, "-s"]

    if args.dry_run:
        print("# would subscribe:", shlex.join(sub_cmd))
        print("# would publish to:", req_topic)
        print("# payload hex:", data.hex())
        return 0

    # start the reply listener first so we don't miss a fast answer
    sub = subprocess.Popen(sub_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(0.3)
    pub = subprocess.run(pub_cmd, input=data, stderr=subprocess.PIPE)
    if pub.returncode != 0:
        sub.kill()
        sys.exit(f"publish failed: {pub.stderr.decode().strip()}")

    try:
        out, err = sub.communicate(timeout=args.timeout + 2)
    except subprocess.TimeoutExpired:
        sub.kill()
        out, err = sub.communicate()

    line = out.decode(errors="replace").strip()
    if not line:
        print(json.dumps({"request.id": req_id, "resource": f"{verb}/{resource}",
                          "reply": None, "note": "no reply within timeout"}))
        return 1

    rtopic, _, hexstr = line.partition(" | ")
    decoded = kura_decode.decode_payload(bytes.fromhex(hexstr.strip()))
    print(json.dumps({"request.id": req_id, "resource": f"{verb}/{resource}",
                      "reply_topic": rtopic, "reply": decoded}, ensure_ascii=False))
    return 0


def _confirm(verb, resource, metrics, assume_yes):
    if assume_yes:
        return
    sys.stderr.write(f"About to {verb} {resource} with {metrics or '{}'} on a REAL device.\n")
    if not sys.stdin.isatty():
        sys.exit("refusing a mutating command without --yes in non-interactive mode")
    if input("Proceed? [y/N] ").strip().lower() not in ("y", "yes"):
        sys.exit("aborted")


def _parse_metrics(items):
    out = {}
    for it in items:
        k, v = kura_encode._parse_metric_arg(it)
        out[k] = v
    return out


def main():
    ap = argparse.ArgumentParser(description="Kura GET/PUT/EXEC request-reply client")
    ap.add_argument("verb", choices=["get", "put", "exec"])
    ap.add_argument("resource", help="resource path after the verb, e.g. 310/0/0")
    ap.add_argument("--metric", "-m", action="append", default=[],
                    help="request metric name=value or name:TYPE=value (repeatable)")
    ap.add_argument("--host", default=DEFAULTS["host"])
    ap.add_argument("--port", default=DEFAULTS["port"])
    ap.add_argument("--account", default=DEFAULTS["account"])
    ap.add_argument("--client", default=DEFAULTS["client"], help="device MAC / client id")
    ap.add_argument("--app", default=DEFAULTS["app"])
    ap.add_argument("--cafile", default=None, help="broker cert (adds --insecure)")
    ap.add_argument("--timeout", type=int, default=10, help="seconds to wait for a reply")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation for PUT/EXEC")
    ap.add_argument("--dry-run", action="store_true", help="print what would be sent, send nothing")
    args = ap.parse_args()

    verb = args.verb.upper()
    metrics = _parse_metrics(args.metric)
    if verb in ("PUT", "EXEC") and not args.dry_run:
        _confirm(verb, args.resource, metrics, args.yes)
    sys.exit(request(args, verb, args.resource, metrics))


if __name__ == "__main__":
    main()
