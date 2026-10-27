# 01 — Discovery

How we established that the Ariston Wi-Fi + Zigbee gateway has no local API and
talks only to a cloud MQTT broker.

## The device

- IP: `192.168.42.114`
- MAC: `0C:8B:95:15:C0:3C` → OUI **Espressif** (ESP32/ESP8266-class module)
- Role: Ariston boiler Wi-Fi + Zigbee gateway

## Port / service scan

- ICMP: up (TTL 255).
- **All 65,535 TCP ports closed** (actively refused — no firewall drop, simply no listener).
- Direct probes of ~30 common IoT ports (80, 443, 1883, 8080/8083, 6053, 8123, 502, Tuya 6668, …): nothing open.
- mDNS/Bonjour: advertises nothing (other LAN devices do, so discovery works).
- SSDP/UPnP M-SEARCH: no response.
- CoAP (UDP 5683): no response.

Conclusion: the gateway is a pure **outbound cloud client**, not a server. There
is no local API to call. That rules out direct local polling and points to
observing/redirecting its cloud connection instead.

## Traffic capture

Captured the gateway's traffic with the MikroTik packet sniffer streaming TZSP to
a workstation, decoded in Wireshark/tshark.

- 100% of TCP connections go to **`broker-ari.everyware-cloud.com:8883`** (MQTT
  over TLS). Resolved to AWS eu-west-1 addresses (54.74.230.43, 34.244.230.237,
  54.74.94.46, 34.250.169.73 — these rotate).
- Also: NTP to `pool.ntp.org`, DNS to the LAN resolver `192.168.42.252`.
- No LAN-local traffic of any kind.

`everyware-cloud.com` is **Eurotech's Everyware Cloud** (Kapua/Kura IoT platform).
So the gateway is a Eurotech MQTT device; all telemetry and commands ride that one
TLS session.

## Decision

Because there is no local service, local control is achieved by **owning the MQTT
endpoint**: point the gateway's DNS for `broker-ari.everyware-cloud.com` at a local
broker. This works only if the gateway does not validate the server certificate —
which it does not (see [03](03-local-broker-and-bridge.md)).
