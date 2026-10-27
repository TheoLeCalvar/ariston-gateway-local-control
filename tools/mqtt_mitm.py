#!/usr/bin/env python3
"""Transparent MQTT/TLS man-in-the-middle for the Ariston gateway.

Sits between the gateway and the real Eurotech cloud so that:
  * the official Ariston app keeps working (traffic really reaches the cloud), and
  * every MQTT packet is logged, with PUBLISH payloads decoded via kura_decode.

How it fits in: the gateway's DNS for broker-ari.everyware-cloud.com is pointed
at the machine running this proxy. The proxy terminates the gateway's TLS with
the self-signed cert (which the gateway does not validate), opens its own TLS
session to the REAL cloud (by IP, so it is not caught by the DNS override), and
relays bytes in both directions untouched. The gateway's own CONNECT -- including
its real credentials -- passes straight through, so we never need to know them.

This is a passive observer on the wire: it relays faithfully and only logs.
It does not modify, inject, or drop packets.

Usage:
    sudo python3 -I mqtt_mitm.py \
        --cert ../broker/server.crt --key ../broker/server.key \
        --upstream-host broker-ari.everyware-cloud.com \
        [--upstream-ip 54.74.230.43] [--listen 0.0.0.0:8883] \
        [--log mitm.jsonl]

If --upstream-ip is omitted, the real IP is resolved via a public resolver
(1.1.1.1) directly, bypassing the local DNS override.
"""
import os
import sys
import ssl
import json
import socket
import struct
import argparse
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import kura_decode
except Exception:
    kura_decode = None

PKT = {1: "CONNECT", 2: "CONNACK", 3: "PUBLISH", 4: "PUBACK", 5: "PUBREC",
       6: "PUBREL", 7: "PUBCOMP", 8: "SUBSCRIBE", 9: "SUBACK", 10: "UNSUBSCRIBE",
       11: "UNSUBACK", 12: "PINGREQ", 13: "PINGRESP", 14: "DISCONNECT", 15: "AUTH"}

_log_lock = threading.Lock()
_logfile = None


def log(obj):
    obj["t"] = round(time.time(), 3)
    line = json.dumps(obj, ensure_ascii=False, default=str)
    with _log_lock:
        print(line, flush=True)
        if _logfile:
            _logfile.write(line + "\n")
            _logfile.flush()


def mini_dns_a(name, resolver="1.1.1.1"):
    """Resolve an A record via a specific resolver (bypasses local DNS override)."""
    tid = os.urandom(2)
    q = tid + b"\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    for part in name.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00\x00\x01\x00\x01"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(4)
    s.sendto(q, (resolver, 53))
    data, _ = s.recvfrom(2048)
    s.close()
    # skip header(12) + question, then walk answers for the first A record
    i = 12
    while data[i] != 0:
        i += 1 + data[i]
    i += 5  # null + qtype + qclass
    ancount = struct.unpack(">H", data[6:8])[0]
    for _ in range(ancount):
        if data[i] & 0xC0 == 0xC0:
            i += 2
        else:
            while data[i] != 0:
                i += 1 + data[i]
            i += 1
        rtype, _rclass, _ttl, rdlen = struct.unpack(">HHIH", data[i:i + 10])
        i += 10
        if rtype == 1 and rdlen == 4:
            return ".".join(str(b) for b in data[i:i + 4])
        i += rdlen
    raise RuntimeError(f"no A record for {name} via {resolver}")


def read_varint_len(get):
    """Read an MQTT remaining-length varint using callable get(1)->byte."""
    mult = 1
    value = 0
    nbytes = 0
    raw = b""
    while True:
        b = get(1)
        if not b:
            return None, raw
        raw += b
        value += (b[0] & 0x7F) * mult
        nbytes += 1
        if not (b[0] & 0x80):
            return value, raw
        mult *= 128
        if nbytes > 4:
            return None, raw


def parse_packet(buf, direction):
    """Log a single MQTT control packet (bytes)."""
    if not buf:
        return
    ptype = buf[0] >> 4
    flags = buf[0] & 0x0F
    name = PKT.get(ptype, f"?{ptype}")
    # locate variable header start (after remaining-length varint)
    i = 1
    mult = 1
    rem = 0
    while True:
        b = buf[i]
        rem += (b & 0x7F) * mult
        i += 1
        if not (b & 0x80):
            break
        mult *= 128
    body = buf[i:i + rem]

    entry = {"dir": direction, "pkt": name}

    if ptype == 3:  # PUBLISH
        qos = (flags >> 1) & 0x3
        tlen = struct.unpack(">H", body[0:2])[0]
        topic = body[2:2 + tlen].decode("utf-8", "replace")
        p = 2 + tlen
        if qos > 0:
            p += 2  # packet identifier
        payload = body[p:]
        entry["qos"] = qos
        entry["topic"] = topic
        entry["payload_hex"] = payload.hex()
        if kura_decode and payload:
            try:
                entry["decoded"] = kura_decode.decode_payload(payload)
            except Exception as e:
                entry["decode_error"] = str(e)
    elif ptype == 1:  # CONNECT
        try:
            plen = struct.unpack(">H", body[0:2])[0]
            proto = body[2:2 + plen].decode("ascii", "replace")
            q = 2 + plen
            _level = body[q]
            cflags = body[q + 1]
            entry["proto"] = proto
            entry["connect_flags"] = cflags
        except Exception:
            pass
    elif ptype == 8:  # SUBSCRIBE
        subs = []
        p = 2  # packet id
        while p < len(body):
            tlen = struct.unpack(">H", body[p:p + 2])[0]
            p += 2
            subs.append(body[p:p + tlen].decode("utf-8", "replace"))
            p += tlen + 1  # +1 requested QoS
        entry["subscriptions"] = subs

    log(entry)


class Pump(threading.Thread):
    """Relay bytes from src to dst, reassembling MQTT packets for logging."""

    def __init__(self, src, dst, direction):
        super().__init__(daemon=True)
        self.src = src
        self.dst = dst
        self.direction = direction

    def run(self):
        buf = b""
        try:
            while True:
                chunk = self.src.recv(4096)
                if not chunk:
                    break
                # relay immediately and untouched
                self.dst.sendall(chunk)
                buf += chunk
                # peel off complete MQTT packets for logging
                buf = self._drain(buf)
        except (OSError, ssl.SSLError):
            pass
        finally:
            for s in (self.src, self.dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _drain(self, buf):
        while len(buf) >= 2:
            # parse remaining length
            mult = 1
            rem = 0
            i = 1
            ok = False
            while i < len(buf):
                b = buf[i]
                rem += (b & 0x7F) * mult
                if not (b & 0x80):
                    ok = True
                    i += 1
                    break
                mult *= 128
                i += 1
                if i > 5:
                    return b""  # desync; drop
            if not ok:
                break  # need more bytes for the length
            total = i + rem
            if len(buf) < total:
                break  # packet not fully arrived
            try:
                parse_packet(buf[:total], self.direction)
            except Exception as e:
                log({"dir": self.direction, "pkt": "parse_error", "err": str(e)})
            buf = buf[total:]
        return buf


def handle(client_sock, args, up_ip):
    cctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    cctx.load_cert_chain(args.cert, args.key)
    try:
        cctx.set_ciphers("DEFAULT@SECLEVEL=0")
    except ssl.SSLError:
        pass
    try:
        client_tls = cctx.wrap_socket(client_sock, server_side=True)
    except ssl.SSLError as e:
        log({"event": "client_tls_failed", "err": str(e)})
        client_sock.close()
        return

    # upstream TLS to the real cloud (by IP, with real SNI)
    uctx = ssl.create_default_context()
    raw_up = socket.create_connection((up_ip, args.upstream_port), timeout=15)
    up_tls = uctx.wrap_socket(raw_up, server_hostname=args.upstream_host)

    log({"event": "session_open", "peer": client_sock.getpeername()[0],
         "upstream": f"{up_ip}:{args.upstream_port}"})

    a = Pump(client_tls, up_tls, "C2S")  # gateway -> cloud
    b = Pump(up_tls, client_tls, "S2C")  # cloud -> gateway
    a.start()
    b.start()
    a.join()
    b.join()
    log({"event": "session_close", "peer": client_sock.getpeername()[0] if client_sock.fileno() != -1 else "?"})


def main():
    global _logfile
    ap = argparse.ArgumentParser(description="Transparent MQTT/TLS MITM for the Ariston gateway")
    ap.add_argument("--cert", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--listen", default="0.0.0.0:8883")
    ap.add_argument("--upstream-host", default="broker-ari.everyware-cloud.com")
    ap.add_argument("--upstream-port", type=int, default=8883)
    ap.add_argument("--upstream-ip", default=None,
                    help="pin the real cloud IP (else resolved via 1.1.1.1)")
    ap.add_argument("--resolver", default="1.1.1.1")
    ap.add_argument("--log", default=None, help="append JSONL to this file as well as stdout")
    args = ap.parse_args()

    if args.log:
        _logfile = open(args.log, "a")

    up_ip = args.upstream_ip or mini_dns_a(args.upstream_host, args.resolver)
    log({"event": "start", "upstream_host": args.upstream_host, "upstream_ip": up_ip})

    host, port = args.listen.rsplit(":", 1)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((host, int(port)))
    srv.listen(8)
    log({"event": "listening", "on": args.listen})

    try:
        while True:
            cs, _ = srv.accept()
            # re-resolve per connection in case of IP rotation (unless pinned)
            ip = args.upstream_ip or up_ip
            threading.Thread(target=handle, args=(cs, args, ip), daemon=True).start()
    except KeyboardInterrupt:
        log({"event": "stop"})


if __name__ == "__main__":
    main()
