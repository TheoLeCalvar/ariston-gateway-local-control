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
import energy  # noqa: E402
import override  # noqa: E402
import tprog  # noqa: E402


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


class EnumMappings(unittest.TestCase):
    """Labels confirmed byte-for-byte from the installer capture (docs/05)."""

    def test_season_mode(self):
        self.assertEqual(menu.decode("3", 0), (0, "Été"))
        self.assertEqual(menu.decode("3", 2), (2, "Chauffage seul"))
        self.assertEqual(menu.decode("3", 5), (5, "Arrêt"))

    def test_dhw_position(self):
        self.assertEqual(menu.decode("0.6", 0), (0, "Désactivé"))
        self.assertEqual(menu.decode("0.6", 2), (2, "Toujours active"))

    def test_zone_mode_and_comfort(self):
        self.assertEqual(menu.decode("0.2", 2), (2, "Manuel"))
        self.assertEqual(menu.decode("6.9.2", 1), (1, "Temporisé"))


class Entities(unittest.TestCase):
    def test_all_codes_cover_specs(self):
        codes = entities.all_codes()
        self.assertIn("U_6.3.1.0.0", codes)
        self.assertIn("U_3", codes)
        # every spec's menu is mapped in menu_params.json
        for m in entities.all_menus():
            self.assertIn(m, menu.PARAMS, f"{m} missing from menu_params.json")

    def test_live_temperature_reads_use_T_prefix(self):
        codes = entities.all_codes()
        for code in ("T_4.3.0", "T_4.3.1", "T_8.4.0"):
            self.assertIn(code, codes)
        temps = [s for s in entities.SENSORS if s.device_class == "temperature"]
        self.assertTrue(all(s.prefix == "T_" for s in temps))
        self.assertEqual({s.menu for s in temps}, {"4.3.0", "4.3.1", "8.4.0"})

    def test_active_zone_filtering(self):
        # zone 1 only: zone 2/3 numbers and their codes drop out; DHW stays
        one = entities.numbers([1])
        menus = {s.menu for s in one}
        self.assertIn("6.3.1.0.0", menus)   # zone 1 comfort
        self.assertIn("6.9.0", menus)        # DHW (not zone-scoped)
        self.assertNotIn("6.3.1.1.0", menus)  # zone 2 comfort
        self.assertNotIn("6.3.1.2.0", menus)  # zone 3 comfort
        codes = entities.all_codes([1])
        self.assertNotIn("U_6.3.1.1.0", codes)
        self.assertNotIn("U_6.3.0.4", codes)  # zone 3 flow
        # climate follows zones too
        self.assertEqual([c.zone for c in entities.climates([1])], [1])
        self.assertEqual(entities.climates([2]), [])
        # default keeps everything
        self.assertEqual(entities.numbers(), entities.NUMBERS)

    def test_climate_zone_codes_are_read(self):
        z1 = next(c for c in entities.CLIMATES if c.zone == 1)
        self.assertEqual((z1.temp_menu, z1.setpoint_menu, z1.mode_menu),
                         ("4.3.0", "4.3.1", "0.0"))
        codes = entities.all_codes()
        for code in (f"T_{z1.temp_menu}", f"T_{z1.setpoint_menu}",
                     f"U_{z1.mode_menu}"):
            self.assertIn(code, codes)
        # mode is an enum with the Manuel(2)/Programmé(3) values the climate maps
        self.assertEqual(menu.decode(z1.mode_menu, 3), (3, "Chauffage programmé"))


class Stat(unittest.TestCase):
    def test_decode_body(self):
        entry = bytes.fromhex("0804" "1006" "1a04" "0064c801")  # idx4 typ6 [0,100,200]
        body = bytes([0x0a, len(entry)]) + entry
        series = energy.decode_stat_body(body)
        self.assertEqual(series, {(6, 4): [0, 100, 200]})

    def test_kwh(self):
        self.assertEqual(energy.kwh([0, 124652, 487552]), 612.204)


class Override(unittest.TestCase):
    def test_build_matches_captured_put(self):
        # captured: zone 1, enable, 18.0 C, end epoch 1793124000
        body = override.build_der_body(1, True, 18.0, 1793124000)
        self.assertEqual(body.hex(), "0a0d0801100118b40120a0cd83d706")

    def test_roundtrip(self):
        body = override.build_der_body(1, True, 18.0, 1793124000)
        info = override.parse_der_body(body)
        self.assertEqual(info, {"zone": 1, "enabled": True, "temp": 18.0,
                                "end": 1793124000})

    def test_idle_parses_inactive(self):
        body = override.build_der_body(1, False, 0, 0)
        info = override.parse_der_body(body)
        self.assertFalse(info["enabled"])
        self.assertIsNone(info["temp"])


class Holiday(unittest.TestCase):
    # captured PUT Func/SpecF enabling holiday until 2026-10-27 23:59Z
    CAP = ("c2b802440a137265717565737465722e636c69656e742e69641005422b4b6170756"
           "1506f6f6c2d313739333132303238323839382d383039303631393536353139363"
           "53837373731c2b8020b0a0154100330c4f584d706c2b8020a0a044d6f646510033"
           "004c2b802240a0a726571756573742e6964100542142d3536353332323931323932"
           "3237333931393238")

    def test_holiday_put_metrics(self):
        m = kura.decode_payload(bytes.fromhex(self.CAP))["metrics"]
        self.assertEqual(m["Mode"], {"type": "INT32", "value": 4})
        self.assertEqual(m["T"], {"type": "INT32", "value": 1793145540})


class TProg(unittest.TestCase):
    # captured Func/TProg reply bodies (docs/05)
    ID1 = ("0a450801100f1a100a0207061204081c10011204085c10001a1f0a050102030405"
           "1204081a10011204082210001204084610011204085e100020b901289b01"
           "3000380040004800")
    ID7 = ("0a710807100f1a0f0a01071204082010011204085810001a1e0a0401020304"
           "1204081810011204082010001204084010011204085810001a1b0a0105"
           "12040818100112040820100012040 83c10011204085c10001a0f0a0106"
           "1204081c10011204085e100020b90128aa013000380040004800").replace(" ", "")

    def test_id1_decodes_to_known_schedule(self):
        p = tprog.decode_reply_body_hex(self.ID1)
        self.assertEqual(p["id"], 1)
        self.assertEqual(p["slot_minutes"], 15)
        self.assertEqual((p["comfort"], p["eco"]), (18.5, 15.5))
        # weekend profile (days 7,6) and weekday profile (1-5), owner-confirmed
        self.assertEqual(p["profiles"][0]["days"], [7, 6])
        self.assertEqual(p["profiles"][0]["windows"], [["07:00", "23:00"]])
        self.assertEqual(p["profiles"][1]["days"], [1, 2, 3, 4, 5])
        self.assertEqual(p["profiles"][1]["windows"],
                         [["06:30", "08:30"], ["17:30", "23:30"]])

    def test_id7_profiles(self):
        p = tprog.decode_reply_body_hex(self.ID7)
        self.assertEqual(p["id"], 7)
        self.assertEqual([pr["days"] for pr in p["profiles"]],
                         [[7], [1, 2, 3, 4], [5], [6]])

    def test_build_round_trips_byte_for_byte(self):
        for hexs in (self.ID1, self.ID7):
            p = tprog.decode_reply_body_hex(hexs)
            self.assertEqual(tprog.build_tprog_body(p).hex(), hexs.lower())

    def test_hhmm_slot_conversion(self):
        self.assertEqual(tprog.hhmm_to_slot("06:30", 15), 26)
        self.assertEqual(tprog.hhmm_to_slot("23:30", 15), 94)
        self.assertEqual(tprog.windows_to_transitions([["06:30", "08:30"]], 15),
                         [[26, 1], [34, 0]])

    def test_apply_setpoints_keeps_schedule(self):
        p = tprog.decode_reply_body_hex(self.ID1)
        out = tprog.apply_changes(p, comfort=20.0)
        self.assertEqual(out["comfort"], 20.0)
        self.assertEqual(out["profiles"], p["profiles"])  # untouched

    def test_apply_profiles_rebuilds_known_body(self):
        # feeding id1's own windows back in reproduces its body byte-for-byte
        p = tprog.decode_reply_body_hex(self.ID1)
        profiles = [{"days": pr["days"], "windows": pr["windows"]}
                    for pr in p["profiles"]]
        out = tprog.apply_changes(p, profiles=profiles)
        self.assertEqual(tprog.build_tprog_body(out).hex(), self.ID1.lower())

    def test_empty_body_is_none(self):
        self.assertIsNone(tprog.decode_reply_body_hex(""))

    def test_day_range_label(self):
        self.assertEqual(tprog.day_range_label([1, 2, 3, 4, 5]), "Mon–Fri")
        self.assertEqual(tprog.day_range_label([7, 6]), "Sat–Sun")
        self.assertEqual(tprog.day_range_label([5]), "Fri")
        self.assertEqual(tprog.day_range_label([1, 3]), "Mon, Wed")

    def test_describe_profiles_is_legible(self):
        p = tprog.decode_reply_body_hex(self.ID1)
        self.assertEqual(
            tprog.describe_profiles(p["profiles"], step=p["slot_minutes"]),
            ["Sat–Sun: 07:00–23:00",
             "Mon–Fri: 06:30–08:30, 17:30–23:30"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
