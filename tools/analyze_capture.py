#!/usr/bin/env python3
"""Summarize an mqtt_mitm.py capture (mitm.jsonl) into a command dictionary.

The MITM proxy logs one JSON object per MQTT packet. This tool rolls those up
into the thing the reverse-engineering actually needs: which resources the app
reads (GET), writes (PUT) and executes (EXEC), what metrics each carries, and
the values seen -- so you can map the hex-coded eBUS parameter IDs to meaning.

Usage:
    tools/analyze_capture.py mitm.jsonl
    tools/analyze_capture.py mitm.jsonl --json      # machine-readable
    tools/analyze_capture.py mitm.jsonl --metrics   # just the metric catalog
    tools/analyze_capture.py mitm.jsonl --follow     # live: print new discoveries
                                                     #   as you drive the app

Topics decode as $EDC/<account>/<client>/<app>/<VERB>/<resource...>, so the
"resource" is everything after the verb (e.g. PUT/310/... -> resource 310/...).
"""
import os
import sys
import json
import time
import argparse
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import ebus_params
except Exception:
    ebus_params = None

VERBS = ("GET", "PUT", "EXEC", "REPLY")
# C2S = gateway->cloud (telemetry, replies); S2C = cloud->gateway (commands).


def ebus_hint(name):
    """Short 'ebus_id name (unit)' hint for a hex metric, or '' if unknown/none."""
    if ebus_params is None:
        return ""
    info = ebus_params.annotate(name)
    if not info:
        return ""
    known = info.get("known")
    if not known:
        return f"  [eBUS {info['ebus_id']} z{info['zone']} — unmapped]"
    unit = f" {known['unit']}" if known.get("unit") else ""
    return f"  [eBUS {info['ebus_id']} z{info['zone']} = {known['name']}{unit}]"


def split_edc(topic):
    """Return (verb, resource) for an $EDC control topic, else (None, None)."""
    parts = topic.split("/")
    if not parts or parts[0] != "$EDC":
        return None, None
    for i, p in enumerate(parts):
        if p in VERBS:
            return p, "/".join(parts[i + 1:])
    return None, None


def iter_packets(path):
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def summarize(path):
    resources = defaultdict(lambda: {
        "verb": None, "count": 0, "dirs": set(), "metrics": defaultdict(set)})
    metrics_global = defaultdict(lambda: {"types": set(), "values": set(), "count": 0})
    topics = defaultdict(int)
    pkt_counts = defaultdict(int)

    for obj in iter_packets(path):
        if obj.get("pkt"):
            pkt_counts[obj["pkt"]] += 1
        topic = obj.get("topic")
        if not topic:
            continue
        topics[topic] += 1
        verb, resource = split_edc(topic)
        decoded = obj.get("decoded") or {}
        metrics = decoded.get("metrics") or {}

        for name, m in metrics.items():
            g = metrics_global[name]
            g["count"] += 1
            g["types"].add(m.get("type"))
            v = m.get("value")
            if v is not None and len(g["values"]) < 12:
                g["values"].add(_short(v))

        if verb:
            key = (verb, resource)
            r = resources[key]
            r["verb"] = verb
            r["count"] += 1
            if obj.get("dir"):
                r["dirs"].add(obj["dir"])
            for name, m in metrics.items():
                r["metrics"][name].add(_short(m.get("value")))

    return resources, metrics_global, topics, pkt_counts


def _short(v):
    s = str(v)
    return s if len(s) <= 40 else s[:37] + "..."


def _set(x):
    return sorted(s for s in x if s is not None)


def print_report(resources, metrics_global, topics, pkt_counts):
    print("# Packet types")
    for name, n in sorted(pkt_counts.items(), key=lambda kv: -kv[1]):
        print(f"  {name:12} {n}")

    print("\n# Command dictionary (resources by verb)")
    if not resources:
        print("  (no $EDC GET/PUT/EXEC/REPLY topics seen -- drive the app to capture them)")
    for (verb, resource), r in sorted(resources.items()):
        dirs = ",".join(sorted(r["dirs"]))
        print(f"  [{verb}] {resource or '(root)'}   x{r['count']} {dirs}")
        for name, vals in sorted(r["metrics"].items()):
            vs = ", ".join(_set(vals)[:8])
            print(f"        - {name}: {vs}" if vs else f"        - {name}")

    print("\n# Metric catalog (all metric names seen)")
    for name, g in sorted(metrics_global.items()):
        types = "/".join(_set(g["types"]))
        vals = ", ".join(_set(g["values"])[:8])
        print(f"  {name}  [{types}] x{g['count']}" + (f"  e.g. {vals}" if vals else ""))

    print(f"\n# Topics ({len(topics)})")
    for t, n in sorted(topics.items(), key=lambda kv: -kv[1]):
        print(f"  {n:5} {t}")


def to_json(resources, metrics_global, topics, pkt_counts):
    return {
        "packet_types": dict(pkt_counts),
        "resources": [
            {"verb": v, "resource": res, "count": r["count"],
             "dirs": sorted(r["dirs"]),
             "metrics": {n: _set(vals) for n, vals in r["metrics"].items()}}
            for (v, res), r in sorted(resources.items())
        ],
        "metrics": {
            n: {"types": _set(g["types"]), "count": g["count"], "values": _set(g["values"])}
            for n, g in sorted(metrics_global.items())
        },
        "topics": dict(sorted(topics.items(), key=lambda kv: -kv[1])),
    }


def _iter_new_lines(path, from_start):
    """Yield complete JSONL lines from a growing file, tail -f style.

    Follows across truncation/rotation (restarts if the file shrinks) and never
    yields a half-written line (waits for the terminating newline).
    """
    while not os.path.exists(path):
        time.sleep(0.5)
    f = open(path)
    if not from_start:
        f.seek(0, os.SEEK_END)
    buf = ""
    inode = os.fstat(f.fileno()).st_ino
    while True:
        chunk = f.readline()
        if chunk:
            buf += chunk
            if buf.endswith("\n"):
                line = buf.strip()
                buf = ""
                if line:
                    yield line
            continue
        # no data: check for rotation, then wait
        try:
            if os.stat(path).st_ino != inode or os.stat(path).st_size < f.tell():
                f.close()
                f = open(path)
                inode = os.fstat(f.fileno()).st_ino
                buf = ""
                continue
        except FileNotFoundError:
            pass
        time.sleep(0.4)


def follow(path, from_start):
    """Print each newly-seen topic / resource / metric as it appears."""
    seen_topics = set()
    seen_resources = set()
    seen_metrics = set()
    print(f"# watching {path} — drive the app; new discoveries print below "
          f"(Ctrl-C to stop)", flush=True)
    for line in _iter_new_lines(path, from_start):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        topic = obj.get("topic")
        if not topic:
            continue
        ts = time.strftime("%H:%M:%S")
        if topic not in seen_topics:
            seen_topics.add(topic)
            print(f"{ts}  + topic   {topic}", flush=True)
        verb, resource = split_edc(topic)
        if verb and (verb, resource) not in seen_resources:
            seen_resources.add((verb, resource))
            print(f"{ts}  + {verb:4}    {resource or '(root)'}", flush=True)
        decoded = obj.get("decoded") or {}
        for name, m in (decoded.get("metrics") or {}).items():
            if name in seen_metrics:
                continue
            seen_metrics.add(name)
            val = m.get("value") if isinstance(m, dict) else m
            print(f"{ts}  + metric  {name} = {_short(val)}{ebus_hint(name)}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Summarize an mqtt_mitm.py capture")
    ap.add_argument("logfile", help="JSONL capture from mqtt_mitm.py (or live_monitor output)")
    ap.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    ap.add_argument("--metrics", action="store_true", help="print only the metric catalog")
    ap.add_argument("--follow", "-f", action="store_true",
                    help="tail the log and print new topics/resources/metrics live")
    ap.add_argument("--from-start", action="store_true",
                    help="with --follow, also replay the existing file first")
    args = ap.parse_args()

    if args.follow:
        try:
            follow(args.logfile, args.from_start)
        except KeyboardInterrupt:
            pass
        return

    data = summarize(args.logfile)
    if args.json:
        print(json.dumps(to_json(*data), indent=2, ensure_ascii=False))
        return
    if args.metrics:
        _, metrics_global, _, _ = data
        for name, g in sorted(metrics_global.items()):
            types = "/".join(_set(g["types"]))
            vals = ", ".join(_set(g["values"])[:8])
            print(f"{name}  [{types}] x{g['count']}" + (f"  e.g. {vals}" if vals else ""))
        return
    print_report(*data)


if __name__ == "__main__":
    main()
