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

from . import kura, menu, energy, override, tprog
from .const import SPECF_HOLIDAY_MODE, SPECF_IDLE_MODE

_LOGGER = logging.getLogger(__name__)


def _metric(reply: dict, name: str):
    """A metric's value from a decoded reply ({value, type} or a bare value)."""
    m = (reply.get("metrics") or {}).get(name)
    return m.get("value") if isinstance(m, dict) else m


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
        self._tls_configured = False

    # ----------------------------------------------------------------- topics
    def _topic(self, client, verb, resource=""):
        base = f"$EDC/{self.account}/{client}/{self.app}/{verb}"
        return f"{base}/{resource}" if resource else base

    @property
    def reply_wildcard(self):
        return self._topic(self.requester, "REPLY", "#")

    # ----------------------------------------------------------------- TLS
    def _configure_tls(self):
        # Build the context WITHOUT create_default_context(): that loads the
        # system trust store from disk (set_default_verify_paths), which blocks
        # the event loop if __init__ runs there. We verify nothing anyway, so a
        # bare client context is enough. Called from connect() (an executor).
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
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
        if not self._tls_configured:
            self._configure_tls()  # does disk I/O; must run off the event loop
            self._tls_configured = True
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
    def _request(self, verb, resource, metrics, timeout=8, body=None):
        req_id = uuid.uuid4().hex[:16]
        ev = threading.Event()
        with self._lock:
            self._events[req_id] = ev
        payload = dict(metrics)
        payload["request.id"] = req_id
        payload["requester.client.id"] = self.requester
        data = kura.encode_payload(payload, timestamp=False, body=body)
        self._client.publish(self._topic(self.mac, verb, resource), data, qos=0)
        got = ev.wait(timeout)
        with self._lock:
            self._events.pop(req_id, None)
            reply = self._pending.pop(req_id, None)
        if not got:
            raise TimeoutError(f"no reply to {verb} {resource} within {timeout}s")
        return reply or {}

    def _put(self, resource, metrics, timeout, body=None) -> int | None:
        """PUT a resource and return the device's response.code (200 = accepted)."""
        reply = self._request("PUT", resource, metrics, timeout, body=body)
        return _metric(reply, "response.code")

    # ----------------------------------------------------------------- public API
    def read(self, codes, timeout=8) -> dict:
        """Read parameter codes via Menu/Par. Returns {code: raw_value}."""
        values: dict = {}
        for batch in menu.batch_metrics(codes):
            reply = self._request("GET", "Menu/Par", batch, timeout)
            for name in reply.get("metrics") or {}:
                if name != "response.code":
                    values[name] = _metric(reply, name)
        return values

    def write(self, code, raw, timeout=8) -> int | None:
        """Write one parameter via PUT Menu/Par. Returns the response.code."""
        if "DEPLOY-V2" in code.upper():
            raise ValueError("refusing to target the DEPLOY-V2 channel")
        return self._put("Menu/Par", {code: ("INT32", int(raw))}, timeout)

    def read_stats(self, typs=(energy.TYP_HEATING, energy.TYP_DHW), timeout=8) -> dict:
        """Read energy stats via Stat/cWh. Returns {(typ, idx): [Wh values]}."""
        typ_str = ",".join(str(t) for t in typs)
        reply = self._request("GET", "Stat/cWh", {"Typ": ("STRING", typ_str)}, timeout)
        return energy.decode_reply_body_hex(reply.get("body", ""))

    def read_time_program(self, prog_id, timeout=8) -> dict | None:
        """Read a heating time program (weekly schedule) via Func/TProg."""
        reply = self._request(
            "GET", "Func/TProg", {"Id": ("STRING", str(prog_id))}, timeout)
        return tprog.decode_reply_body_hex(reply.get("body", ""))

    def set_time_program(self, prog_id, *, comfort=None, eco=None, profiles=None,
                         timeout=8, verify=True) -> int | None:
        """Read-modify-write a time program via Func/TProg.

        Reads the current program, applies the given changes, and PUTs the
        rebuilt body (we only ever send a body derived from one the device gave
        us). The boiler is the source of truth, so the read is done immediately
        before the write: we never push a stale copy. Returns the response.code.

        With verify (default), the program is read back and compared to what we
        sent; a mismatch is logged (it would mean the schedule was also changed
        on the boiler in the same instant -- the boiler's copy wins)."""
        current = self.read_time_program(prog_id, timeout)
        if current is None:
            raise ValueError(f"time program {prog_id} not readable")
        program = tprog.apply_changes(
            current, comfort=comfort, eco=eco, profiles=profiles)
        body = tprog.build_tprog_body(program)
        rc = self._put("Func/TProg", {}, timeout, body=body)
        if verify and rc in (None, 200):
            applied = self.read_time_program(prog_id, timeout)
            if applied is None or tprog.build_tprog_body(applied) != body:
                _LOGGER.warning(
                    "time program %s read-back differs from the write "
                    "(concurrent change on the boiler?); the boiler's copy wins",
                    prog_id)
        return rc

    # ----------------------------------------------------------------- override
    def read_override(self, zone=1, timeout=8) -> dict | None:
        """Read the temporary override (dérogation) for a zone via Func/Der."""
        reply = self._request("GET", "Func/Der", {"Z": ("STRING", str(zone))}, timeout)
        return override.parse_reply_body_hex(reply.get("body", ""))

    def set_override(self, zone, temp_c, end_epoch, timeout=8) -> int | None:
        """Set a temporary override: hold temp_c on a zone until end_epoch."""
        body = override.build_der_body(zone, True, temp_c, end_epoch)
        return self._put("Func/Der", {}, timeout, body=body)

    def clear_override(self, zone, timeout=8) -> int | None:
        """Clear a zone's temporary override (revert to schedule now)."""
        body = override.build_der_body(zone, False, 0, 0)
        return self._put("Func/Der", {}, timeout, body=body)

    # ----------------------------------------------------------------- holiday
    def read_special_function(self, timeout=8) -> dict:
        """Read the special function (Func/SpecF): {mode, end}. Mode 4 = holiday."""
        reply = self._request("GET", "Func/SpecF", {}, timeout)
        return {"mode": _metric(reply, "Mode"), "end": _metric(reply, "T")}

    def set_holiday(self, end_epoch, timeout=8) -> int | None:
        """Enable holiday/absence mode until end_epoch (absolute Unix seconds)."""
        return self._put("Func/SpecF", {
            "Mode": ("INT32", SPECF_HOLIDAY_MODE), "T": ("INT32", int(end_epoch))},
            timeout)

    def clear_holiday(self, timeout=8) -> int | None:
        """Cancel holiday mode (back to idle: Mode 1, T 0)."""
        return self._put("Func/SpecF", {
            "Mode": ("INT32", SPECF_IDLE_MODE), "T": ("INT32", 0)}, timeout)
