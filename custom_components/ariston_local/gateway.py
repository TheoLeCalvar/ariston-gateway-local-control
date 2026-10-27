"""Blocking MQTT client that reads/writes the gateway's Menu/Par parameters.

Mirrors tools/kura_client.py as a persistent client: it connects to the local
broker (Mode A), subscribes to its own REPLY topic, and correlates replies to
requests by request.id. Calls are blocking; the HA layer runs them in an executor.
"""
from __future__ import annotations

import logging
import secrets
import ssl
import threading
import uuid

import paho.mqtt.client as mqtt

from . import kura, menu, energy

_LOGGER = logging.getLogger(__name__)


class AristonGateway:
    def __init__(self, host, port, mac, *, account="ari", app="ar1",
                 cafile=None, requester=None):
        self.host = host
        self.port = int(port)
        self.mac = mac
        self.account = account
        self.app = app
        self.cafile = cafile
        self.requester = requester or f"ha-{secrets.token_hex(4)}"

        self._pending: dict[str, dict] = {}
        self._events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._connected = threading.Event()

        try:  # paho-mqtt 2.x
            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2, client_id=self.requester)
        except (AttributeError, TypeError):  # paho-mqtt 1.x
            self._client = mqtt.Client(client_id=self.requester)
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        self._configure_tls()

    # ----------------------------------------------------------------- topics
    def _topic(self, client, verb, resource=""):
        base = f"$EDC/{self.account}/{client}/{self.app}/{verb}"
        return f"{base}/{resource}" if resource else base

    @property
    def reply_wildcard(self):
        return self._topic(self.requester, "REPLY", "#")

    # ----------------------------------------------------------------- TLS
    def _configure_tls(self):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        if self.cafile:
            try:
                ctx.load_verify_locations(self.cafile)
            except OSError:
                _LOGGER.warning("could not load cafile %s; proceeding insecurely", self.cafile)
        try:
            ctx.set_ciphers("DEFAULT@SECLEVEL=0")
        except ssl.SSLError:
            pass
        self._client.tls_set_context(ctx)
        self._client.tls_insecure_set(True)

    # ----------------------------------------------------------------- lifecycle
    def connect(self, timeout=10):
        self._client.connect(self.host, self.port, keepalive=30)
        self._client.loop_start()
        if not self._connected.wait(timeout):
            raise TimeoutError(f"could not connect to {self.host}:{self.port}")

    def disconnect(self):
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties=None):
        client.subscribe(self.reply_wildcard, qos=0)
        self._connected.set()

    def _on_message(self, client, userdata, msg):
        req_id = msg.topic.rsplit("/", 1)[-1]
        with self._lock:
            ev = self._events.get(req_id)
            if ev is None:
                return
            try:
                self._pending[req_id] = kura.decode_payload(msg.payload)
            except Exception as err:  # noqa: BLE001
                self._pending[req_id] = {"error": str(err)}
            ev.set()

    # ----------------------------------------------------------------- request/reply
    def _request(self, verb, resource, metrics, timeout=8):
        req_id = uuid.uuid4().hex[:16]
        ev = threading.Event()
        with self._lock:
            self._events[req_id] = ev
        payload = dict(metrics)
        payload["request.id"] = req_id
        payload["requester.client.id"] = self.requester
        data = kura.encode_payload(payload, timestamp=False)
        self._client.publish(self._topic(self.mac, verb, resource), data, qos=0)
        got = ev.wait(timeout)
        with self._lock:
            self._events.pop(req_id, None)
            reply = self._pending.pop(req_id, None)
        if not got:
            raise TimeoutError(f"no reply to {verb} {resource} within {timeout}s")
        return reply or {}

    # ----------------------------------------------------------------- public API
    def read(self, codes, timeout=8) -> dict:
        """Read parameter codes via Menu/Par. Returns {code: raw_value}."""
        values: dict = {}
        for batch in menu.batch_metrics(codes):
            reply = self._request("GET", "Menu/Par", batch, timeout)
            for name, m in (reply.get("metrics") or {}).items():
                if name == "response.code":
                    continue
                values[name] = m.get("value") if isinstance(m, dict) else m
        return values

    def write(self, code, raw, timeout=8) -> int | None:
        """Write one parameter via PUT Menu/Par. Returns the response.code."""
        if "DEPLOY-V2" in code.upper():
            raise ValueError("refusing to target the DEPLOY-V2 channel")
        reply = self._request("PUT", "Menu/Par", {code: ("INT32", int(raw))}, timeout)
        rc = (reply.get("metrics") or {}).get("response.code")
        return rc.get("value") if isinstance(rc, dict) else rc

    def read_stats(self, typs=(energy.TYP_HEATING, energy.TYP_DHW), timeout=8) -> dict:
        """Read energy stats via Stat/cWh. Returns {(typ, idx): [Wh values]}."""
        typ_str = ",".join(str(t) for t in typs)
        reply = self._request("GET", "Stat/cWh", {"Typ": ("STRING", typ_str)}, timeout)
        return energy.decode_reply_body_hex(reply.get("body", ""))
