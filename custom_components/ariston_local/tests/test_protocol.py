#!/usr/bin/env python3
"""Tests for the component's self-contained protocol layer (kura + menu).

No Home Assistant or paho-mqtt needed. Run:
    python3 -I custom_components/ariston_local/tests/test_protocol.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import kura  # noqa: E402
import menu  # noqa: E402
import entities  # noqa: E402


class Kura(unittest.TestCase):
    def test_roundtrip(self):
        data = kura.encode_payload({"U_3": ("INT32", 1)}, timestamp=False)
        self.assertEqual(data.hex(), "c2b802090a03555f3310033001")  # captured PUT
        out = kura.decode_payload(data)
        self.assertEqual(out["metrics"]["U_3"], {"type": "INT32", "value": 1})

    def test_negative_int(self):
        data = kura.encode_payload({"x": ("INT32", -7)}, timestamp=False)
        self.assertEqual(kura.decode_payload(data)["metrics"]["x"]["value"], -7)


class Menu(unittest.TestCase):
    def test_map_loaded(self):
        self.assertIn("6.3.1.0.0", menu.PARAMS)

    def test_decode_scaling_and_enum(self):
        self.assertEqual(menu.decode("6.3.1.0.0", 185), (18.5, None))
        self.assertEqual(menu.decode("3", 1), (1, "Hiver"))
        self.assertEqual(menu.decode("8.3.0", 32767), (None, "N/A"))

    def test_to_raw(self):
        self.assertEqual(menu.to_raw(19.5, "x10"), 195)

    def test_batch(self):
        batches = list(menu.batch_metrics([f"U_{i}" for i in range(20)]))
        self.assertEqual([len(b) for b in batches], [16, 4])
        self.assertEqual(batches[0]["P1"], ("STRING", "U_0"))


class Entities(unittest.TestCase):
    def test_all_codes_cover_specs(self):
        codes = entities.all_codes()
        self.assertIn("U_6.3.1.0.0", codes)
        self.assertIn("U_3", codes)
        # every spec's menu is mapped in menu_params.json
        for m in entities.all_menus():
            self.assertIn(m, menu.PARAMS, f"{m} missing from menu_params.json")


if __name__ == "__main__":
    unittest.main(verbosity=2)
