# 07 — Deploying the Home Assistant integration

End-to-end deployment of local, cloud-free monitoring and control, from a stock
gateway to working entities in Home Assistant. It assumes the reverse-engineering
is done (it is — see docs/04–06); this is the operational runbook.

Two parts:

1. **Put the gateway on your local broker (Mode A).** One-time infrastructure:
   broker, DNS override, self-signed cert. Covered in depth by
   [docs/03](03-local-broker-and-bridge.md); summarised here.
2. **Install the `ariston_local` custom component** and add the integration.

```
 ┌──────────┐   MQTT/TLS 8883    ┌─────────────┐   MQTT/TLS 8883   ┌─────────────┐
 │ Gateway  │ ─────────────────▶ │  Mosquitto  │ ◀──────────────── │ Home        │
 │ (boiler) │  $EDC/ari/<MAC>/…  │ local broker│   Menu/Par etc.   │ Assistant   │
 └──────────┘                    └─────────────┘                   └─────────────┘
    no cloud                      broker-ari.everyware-cloud.com       this add-on
```

The gateway, the broker, and Home Assistant can be three machines or one; only the
broker needs the cert and the DNS name pointed at it.

---

## Part 1 — Gateway onto the local broker (Mode A)

> This takes the device **off the Ariston cloud**: the official app will show it
> offline. Everything still works locally. To go back, revert the DNS override and
> power-cycle (see docs/03).

1. **Generate the broker cert** (CN/SAN `broker-ari.everyware-cloud.com`, which the
   gateway accepts without validation):

   ```bash
   cd broker && ./make-cert.sh        # writes server.crt / server.key
   ```

2. **Run Mosquitto** with the provided config:

   ```bash
   cd broker && mosquitto -c mosquitto.conf
   ```

   Keep it running as a service (systemd unit, container, etc.) for a permanent
   deployment — not in a terminal you'll close.

3. **Redirect DNS** for `broker-ari.everyware-cloud.com` to the broker's IP, on the
   resolver the gateway uses (the LAN router/resolver, e.g. MikroTik
   `192.168.42.252`). Flush the resolver cache.

4. **Power-cycle the gateway.** It caches the old cloud IP and holds a long-lived
   TLS session; it won't switch until it reconnects.

**Verify Part 1** before touching Home Assistant — confirm the gateway is talking
to your broker:

```bash
tools/live_monitor.sh                 # decoded telemetry should scroll
# or a one-off read of a known parameter:
tools/read_menu.py 3 --cafile broker/server.crt     # season mode -> a value
```

If nothing arrives: the DNS override isn't in effect, the gateway wasn't
power-cycled, or the cert CN is wrong. Don't proceed until a read works.

Note the **gateway MAC / client id** (the `<MAC>` in the topics, e.g.
`AABBCCDDEEFF`) — the integration needs it.

---

## Part 2 — Install the Home Assistant integration

### Copy the component

Copy `custom_components/ariston_local/` into your Home Assistant config:

```bash
# from this repo, targeting your HA config dir
cp -r custom_components/ariston_local <HA_CONFIG>/custom_components/
```

- **HA OS / supervised:** `<HA_CONFIG>` is `/config` (use the Samba/SSH add-on or
  the File editor).
- **Container:** the host path you mounted at `/config`.
- **Core venv:** `~/.homeassistant/`.

The component is self-contained; its one dependency (`paho-mqtt`) is declared in
`manifest.json` and HA installs it automatically.

Then **restart Home Assistant** (Developer Tools → Restart, or restart the
service/container) so it picks up the new component.

### Add the integration

**Settings → Devices & Services → Add Integration → "Ariston Local"**, then enter:

| field | value |
|---|---|
| **Broker host** | the Mosquitto host, e.g. `127.0.0.1` or its LAN IP |
| **Broker port** | `8883` |
| **Gateway MAC** | the client id from the topics, e.g. `AABBCCDDEEFF` |
| **CA/cert file** | optional path to `server.crt` on the HA host; leave blank to use TLS without verification (the gateway doesn't verify either) |

Polling interval is under the integration's **Options** (default 60 s).

> If HA runs on a different machine than the broker, "Broker host" is the broker's
> LAN IP, and (if you supply a cert) copy `server.crt` to the HA host.

### What you get

A single **Ariston gateway `<MAC>`** device with:

- **Climate** — Zone 1 thermostat: current temperature, target (active setpoint),
  `AUTO`/`HEAT` (schedule / manual). Setting a temperature issues a native 3 h
  auto-reverting hold.
- **Numbers** — zone comfort/eco setpoints, zone flow setpoints, DHW comfort.
- **Select** — Season mode (Summer / Winter / Heating only / Off).
- **Switches** — anti-legionella, internet time, internet weather.
- **Sensors** — Wi-Fi signal, anti-legionella frequency, and live temperatures
  (zone room temp, active setpoint, boiler flow).
- **Energy sensors** — heating & DHW gas energy (kWh), this month and trailing
  year; `device_class=energy` so they feed the **Energy dashboard** (add them
  under Settings → Dashboards → Energy → gas/individual devices).
- **Binary sensors** — per-zone override active (with target & end-time
  attributes).
- **Services** — `ariston_local.set_override` (zone, temperature, duration) and
  `ariston_local.clear_override`.

### Verify Part 2

1. The device appears with entities, and the live sensors show plausible values
   (room temp ~your room, flow temp 30–60 °C when heating).
2. **A read:** the Season select / setpoints match what the boiler panel shows.
3. **A safe write:** nudge a zone comfort setpoint by 0.5 °C and confirm the
   boiler panel reflects it; set it back.
4. **Climate:** set the Zone 1 target; the "override active" binary sensor should
   turn on and the panel should show the hold.

---

## Troubleshooting

| symptom | likely cause |
|---|---|
| Integration won't set up ("cannot connect to broker") | broker host/port wrong, or Mosquitto not running / not reachable from HA |
| Set up, but entities are "unavailable" | gateway isn't on the broker — re-run the Part 1 verification (DNS + power-cycle) |
| Reads work, writes rejected | only `U_` user params are writable; the log shows the device `response.code` on anything other than 200 |
| TLS errors in the HA log | supply the `server.crt`, or leave the cert field blank to skip verification |
| Values look wrong by 10× | a parameter's scaling isn't mapped — check `menu_params.json` |

Logs: set the component to debug in `configuration.yaml` —

```yaml
logger:
  logs:
    custom_components.ariston_local: debug
```

## Safety

Writes change real settings on a real boiler. The integration writes only `U_`
(user) parameters, refuses the `DEPLOY-V2` channel, deliberately does **not**
expose connectivity-enable (`6.16.0`, which would cut the gateway off), and
range-checks each entity. Know how to revert a setting from the boiler panel
before the first write.
