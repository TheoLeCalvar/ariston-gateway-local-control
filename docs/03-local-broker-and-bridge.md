# 03 — Local broker, MITM proxy, and bridge

Three ways to insert yourself between the gateway and the cloud, from simplest to
most capable. All three rely on one fact: **the gateway does not validate the
broker's TLS certificate**, so a self-signed cert with CN/SAN
`broker-ari.everyware-cloud.com` is accepted.

Common prerequisites:

1. Generate the cert: `broker/make-cert.sh` (writes `server.crt` / `server.key`).
2. Redirect DNS for `broker-ari.everyware-cloud.com` to the machine running the
   broker/proxy. The gateway queries the LAN resolver `192.168.42.252`, so add the
   override **there** (on MikroTik: `/ip dns static add name=... address=...`,
   then `/ip dns cache flush`).
3. **Power-cycle the gateway** — it caches the old IP and holds a long-lived TLS
   session, so it won't switch until it reconnects.

To revert: remove the DNS static entry, flush cache, power-cycle.

---

## Mode A — Fully local broker (observe + control, app offline)

Run Mosquitto with `broker/mosquitto.conf`. The gateway connects to you and
nothing reaches the Ariston cloud (the app shows the device offline).

```bash
cd broker && mosquitto -c mosquitto.conf -v
```

Watch decoded telemetry live:

```bash
tools/live_monitor.sh
```

Send a command by publishing a Kura payload to `$EDC/ari/<MAC>/ar1/PUT/<resource>`
and reading the matching `$EDC/ari/<MAC>/ar1/REPLY/...`. You need the resource
paths and payload shapes first — capture them in Mode B.

`tools/kura_client.py` does the publish/subscribe dance for you (it shells out to
`mosquitto_pub`/`mosquitto_sub`, so no extra Python deps). It subscribes to the
Kapua `REPLY/<request.id>` topic, publishes the request with the `request.id` and
`requester.client.id` metrics Kapua expects, and prints the decoded reply:

```bash
# read a resource (safe)
tools/kura_client.py get 310/0/0 --cafile broker/server.crt

# preview a write without sending anything
tools/kura_client.py put 310/0/0 -m value:DOUBLE=21.5 --dry-run

# actually write (prompts first; --yes to skip; refuses the DEPLOY-V2 channel)
tools/kura_client.py put 310/0/0 -m value:DOUBLE=21.5 --cafile broker/server.crt
```

Use this once you already know the command vocabulary and want a cloud-free setup.

---

## Mode B — Transparent MITM proxy (observe, app stays online) — RECOMMENDED

`tools/mqtt_mitm.py` terminates the gateway's TLS, opens its own TLS to the real
cloud (by IP, so the DNS override doesn't loop it back), and relays bytes
untouched while logging every MQTT packet and decoding PUBLISH payloads. The
gateway's own CONNECT (with its real credentials) passes straight through, so the
app keeps working and you never need the device password.

```bash
# resolve the real cloud IP (bypasses your DNS override)
tools/resolve_real.sh            # e.g. 54.74.230.43

sudo python3 -I tools/mqtt_mitm.py \
  --cert broker/server.crt --key broker/server.key \
  --upstream-host broker-ari.everyware-cloud.com \
  --log mitm.jsonl
```

Now press buttons in the official Ariston app and watch the `GET`/`PUT`/`EXEC`
packets appear with decoded payloads. This is how you build the command
dictionary that Mode A (and the HA integration) needs.

Roll a capture up into that dictionary with the analyzer:

```bash
tools/analyze_capture.py mitm.jsonl            # human-readable summary
tools/analyze_capture.py mitm.jsonl --json     # machine-readable
tools/analyze_capture.py mitm.jsonl --metrics  # just the metric catalog
tools/analyze_capture.py mitm.jsonl --follow   # LIVE: print new finds as you
                                               #   drive the app (Ctrl-C to stop)
```

It groups PUBLISHes by verb + resource, lists the metrics and observed values per
resource, and prints a catalog of every metric name seen (with its types and
example values) — the raw material for mapping the hex eBUS parameter IDs.

**`--follow` is the discovery workflow.** Leave it running in a second terminal
while the MITM logs to `mitm.jsonl`, then press buttons in the app one at a time.
Each *newly-seen* topic, GET/PUT/EXEC resource and metric prints the moment it
appears, with the eBUS register hint inline, so you can see exactly which resource
and metric each action produced:

```
13:06:36  + GET     72/0/0
13:06:36  + metric  106800FE01 = 452  [eBUS 6810 z1 = heating_flow_temp C]
```

Correlate the value you set in the app with the printed metric to confirm the
register and its scaling, then add it to `tools/ebus_registers.json`.

This proxy is a passive observer on the wire: it relays faithfully and only logs.
It does not modify, inject, or drop packets.

---

## Mode C — Mosquitto bridge (both worlds via mosquitto)

`broker/mosquitto-bridge.conf` makes the local broker forward topics to the real
cloud. This is simpler to run than the proxy but has real drawbacks:

- A bridge logs in as **its own** MQTT session with credentials you configure. It
  cannot reuse the gateway's live client-id without causing "session taken over"
  flapping, and Kapua accounts may reject a second login.
- You must supply the device's **password** (the username is known). Recover it in
  Mode B: the proxy logs the CONNECT, or terminate the gateway's TLS once and read
  the CONNECT's password field.

Prefer Mode B unless you specifically want mosquitto to own both links.

---

## Safety notes

- **Never publish to `$EDC/ari/<MAC>/DEPLOY-V2/#`** — that is the firmware
  deployment channel.
- Keep `server.key` out of git (see `.gitignore`).
- While the DNS override is active the device depends on your machine being up; if
  you take it down, revert the override so the gateway reaches the real cloud.
- `PUT` writes setpoints on a real boiler. Test reads (`GET`) before writes, and
  know how to revert a setting physically.
