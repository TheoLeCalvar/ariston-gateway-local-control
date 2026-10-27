#!/usr/bin/env python3
"""Tests for the Kura codec, the capture analyzer, and the client encoding.

Run:  python3 -I tools/test_kura.py   (or:  python3 -m unittest -v)
Stdlib only; no device or broker required.
"""
import os
import sys
import json
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import argparse
import kura_encode
import kura_decode
import analyze_capture
import ebus_params
import sweep_registers
import menu_params


class RoundTrip(unittest.TestCase):
    def rt(self, metrics, ts=1793100075000):
        data = kura_encode.encode_payload(metrics, timestamp=ts)
        return kura_decode.decode_payload(data)

    def test_types(self):
        out = self.rt({
            "s": ("STRING", "hello"),
            "i": ("INT32", 42),
            "l": ("INT64", 2**40),
            "d": ("DOUBLE", 3.5),
            "f": ("FLOAT", 1.5),
            "b": ("BOOL", True),
            "raw": ("BYTES", b"\x01\x02"),
        })
        self.assertEqual(out["timestamp"], 1793100075000)
        m = out["metrics"]
        self.assertEqual(m["s"], {"type": "STRING", "value": "hello"})
        self.assertEqual(m["i"], {"type": "INT32", "value": 42})
        self.assertEqual(m["l"], {"type": "INT64", "value": 2**40})
        self.assertEqual(m["d"], {"type": "DOUBLE", "value": 3.5})
        self.assertAlmostEqual(m["f"]["value"], 1.5)
        self.assertEqual(m["b"], {"type": "BOOL", "value": True})
        self.assertEqual(m["raw"], {"type": "BYTES", "value": "0102"})

    def test_negative_int32(self):
        # the bug this guards: a negative int must not decode as a huge unsigned.
        out = self.rt({"x": ("INT32", -7)})
        self.assertEqual(out["metrics"]["x"]["value"], -7)

    def test_negative_int64(self):
        out = self.rt({"x": ("INT64", -1234567890123)})
        self.assertEqual(out["metrics"]["x"]["value"], -1234567890123)

    def test_inference(self):
        out = self.rt({"on": True, "n": 5, "t": 21.5, "name": "ar1"})
        m = out["metrics"]
        self.assertEqual(m["on"], {"type": "BOOL", "value": True})
        self.assertEqual(m["n"], {"type": "INT32", "value": 5})
        self.assertEqual(m["t"], {"type": "DOUBLE", "value": 21.5})
        self.assertEqual(m["name"], {"type": "STRING", "value": "ar1"})

    def test_real_captured_payload(self):
        # the documented real sample from docs/02-mqtt-protocol.md
        raw = bytes.fromhex(
            "08f89fdfe8973" "4c2b802" "100a" "0a39303039383246453030" "1003" "3000")
        # rebuild cleanly from the doc's byte listing instead of the concatenation above
        raw = bytes.fromhex(
            "08 f8 9f df e8 97 34 c2 b8 02 10 0a 0a 39 30 30 39 38 32 46 45 30 30 10 03 30 00"
            .replace(" ", ""))
        out = kura_decode.decode_payload(raw)
        self.assertEqual(out["timestamp"], 1793100075000)
        self.assertEqual(out["metrics"]["900982FE00"], {"type": "INT32", "value": 0})

    def test_no_timestamp(self):
        data = kura_encode.encode_payload({"x": 1}, timestamp=False)
        out = kura_decode.decode_payload(data)
        self.assertIsNone(out["timestamp"])


class Analyzer(unittest.TestCase):
    def test_split_edc(self):
        v, r = analyze_capture.split_edc("$EDC/ari/AABBCCDDEEFF/ar1/PUT/310/0/0")
        self.assertEqual((v, r), ("PUT", "310/0/0"))
        self.assertEqual(analyze_capture.split_edc("ari/AABBCCDDEEFF/ar1/Log/log"),
                         (None, None))

    def test_summary(self):
        p310 = kura_encode.encode_payload({"value": ("DOUBLE", 21.5)})
        lines = [
            {"dir": "S2C", "pkt": "PUBLISH", "topic": "$EDC/ari/M/ar1/PUT/310/0/0",
             "decoded": kura_decode.decode_payload(p310)},
            {"dir": "C2S", "pkt": "PUBLISH", "topic": "ari/M/ar1/Log/log",
             "decoded": {"metrics": {"900982FE00": {"type": "INT32", "value": 0}}}},
            {"pkt": "PINGREQ"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            for o in lines:
                f.write(json.dumps(o) + "\n")
            path = f.name
        try:
            resources, metrics, topics, pkts = analyze_capture.summarize(path)
        finally:
            os.unlink(path)
        self.assertIn(("PUT", "310/0/0"), resources)
        self.assertEqual(pkts["PINGREQ"], 1)
        self.assertIn("value", metrics)
        self.assertIn("900982FE00", metrics)
        self.assertEqual(topics["$EDC/ari/M/ar1/PUT/310/0/0"], 1)


class EbusParams(unittest.TestCase):
    def test_parse_known(self):
        # the three IDs matched against community registers
        p = ebus_params.parse_id("106800FE01")
        self.assertEqual(p["ebus_id"], "6810")
        self.assertEqual(p["register_le"], "1068")
        self.assertEqual(p["zone"], 1)
        self.assertTrue(p["is_broadcast"])
        self.assertEqual(ebus_params.parse_id("4BC000FE01")["ebus_id"], "c04b")
        self.assertEqual(ebus_params.parse_id("106900FE01")["ebus_id"], "6910")

    def test_annotate(self):
        info = ebus_params.annotate("106800FE01")
        self.assertEqual(info["known"]["name"], "heating_flow_temp")
        self.assertEqual(info["known"]["unit"], "C")

    def test_not_an_id(self):
        self.assertIsNone(ebus_params.parse_id("StatusCode"))
        self.assertIsNone(ebus_params.annotate("N"))

    def test_scale(self):
        self.assertEqual(ebus_params.apply_scale(452, "x10"), 45.2)
        self.assertEqual(ebus_params.apply_scale(3, "enum"), 3)

    def test_build_name_roundtrip(self):
        name = ebus_params.build_name("6810", flag=0x00, zone=0x01)
        self.assertEqual(name, "106800FE01")
        self.assertEqual(ebus_params.parse_id(name)["ebus_id"], "6810")
        # every seeded register round-trips through build_name -> parse_id
        for eid in ebus_params.REGISTERS:
            self.assertEqual(
                ebus_params.parse_id(ebus_params.build_name(eid))["ebus_id"], eid)

    def test_iter_ebus_ids(self):
        self.assertEqual(list(ebus_params.iter_ebus_ids("6810", "6812")),
                         ["6810", "6811", "6812"])

    def test_enrich(self):
        decoded = {"metrics": {
            "106800FE01": {"type": "INT32", "value": 452},
            "StatusCode": {"type": "INT32", "value": 1}}}
        out = ebus_params._enrich_obj(decoded)
        e = out["metrics"]["106800FE01"]["ebus"]
        self.assertEqual(e["name"], "heating_flow_temp")
        self.assertEqual(e["scaled_value"], 45.2)
        self.assertNotIn("ebus", out["metrics"]["StatusCode"])


class Sweeper(unittest.TestCase):
    def _args(self, **kw):
        base = dict(resources=[], resources_file=None, id="1", no_id=False,
                    req_metric=[], known=False, ids=[], range=None, step=1,
                    resource="{hexname}", flag=0x00, zone=0x01)
        base.update(kw)
        return argparse.Namespace(**base)

    def test_resource_targets_match_captured_get(self):
        # the real GET: topic .../GET/Func/TProg, metric Id = STRING "1"
        t = sweep_registers.build_targets(self._args(resources=["Func/TProg"]))
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0]["resource"], "Func/TProg")
        self.assertEqual(t[0]["metrics"]["Id"], ("STRING", "1"))
        # the encoded payload decodes back to Id STRING "1"
        data = kura_encode.encode_payload(t[0]["metrics"], timestamp=False)
        self.assertEqual(kura_decode.decode_payload(data)["metrics"]["Id"],
                         {"type": "STRING", "value": "1"})

    def test_no_id(self):
        t = sweep_registers.build_targets(self._args(resources=["X"], no_id=True))
        self.assertNotIn("Id", t[0]["metrics"])

    def test_register_mode_still_works(self):
        t = sweep_registers.build_targets(self._args(ids=["6810"]))
        self.assertEqual(t[0]["resource"], "106800FE01")


class MenuParams(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(menu_params.parse_code("U_6.16.0"),
                         {"code": "U_6.16.0", "variant": "", "kind": "user", "menu": "6.16.0"})
        p = menu_params.parse_code("C_T_8.4.0")
        self.assertEqual((p["variant"], p["kind"], p["menu"]), ("C", "technical", "8.4.0"))
        self.assertIsNone(menu_params.parse_code("P1"))
        self.assertIsNone(menu_params.parse_code("response.code"))

    def test_scaling_matches_webpage(self):
        # values captured from the device vs the installer menu screenshot
        cases = {"U_6.3.0.0": (500, 50.0), "U_6.3.1.0.0": (185, 18.5),
                 "U_6.9.0": (450, 45.0), "U_6.9.5.1": (28800, 480.0)}
        for code, (raw, want) in cases.items():
            obj = menu_params._enrich_obj({"metrics": {code: {"value": raw}}})
            self.assertEqual(obj["metrics"][code]["menu_param"]["scaled_value"], want)

    def test_enum_and_sentinel(self):
        obj = menu_params._enrich_obj({"metrics": {
            "U_3": {"value": 1}, "U_6.16.0": {"value": 1}, "U_8.3.0": {"value": 32767}}})
        self.assertEqual(obj["metrics"]["U_3"]["menu_param"]["enum_label"], "Hiver")
        self.assertEqual(obj["metrics"]["U_6.16.0"]["menu_param"]["enum_label"], "Marche")
        self.assertEqual(obj["metrics"]["U_8.3.0"]["menu_param"]["enum_label"], "N/A")

    def test_batch_metrics(self):
        batches = list(menu_params.batch_metrics([f"U_{i}" for i in range(20)]))
        self.assertEqual([len(b) for b in batches], [16, 4])
        self.assertEqual(batches[0]["P1"], ("STRING", "U_0"))

    def test_to_raw_roundtrip(self):
        self.assertEqual(menu_params.to_raw(19.5, "x10"), 195)
        self.assertEqual(menu_params.to_raw(480, "div60"), 28800)
        self.assertEqual(menu_params.to_raw(3, "enum"), 3)
        self.assertEqual(menu_params.apply_scale(menu_params.to_raw(18.5, "x10"), "x10"), 18.5)


class WriteMenu(unittest.TestCase):
    def test_put_matches_captured_packet(self):
        # the real captured PUT of U_3 = 1
        data = kura_encode.encode_payload({"U_3": ("INT32", 1)}, timestamp=False)
        self.assertEqual(data.hex(), "c2b802090a03555f3310033001")

    def test_write_scaling_and_validation(self):
        import write_menu
        code, raw, human, known, warning = write_menu.parse_assignment(
            "6.3.1.0.0=19.5", "U_", False)
        self.assertEqual((code, raw), ("U_6.3.1.0.0", 195))
        self.assertIsNone(warning)
        # value outside the known settable set for mode 3 warns
        _, raw3, _, _, warn3 = write_menu.parse_assignment("3=9", "U_", False)
        self.assertEqual(raw3, 9)
        self.assertIsNotNone(warn3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
