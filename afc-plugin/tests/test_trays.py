"""AFC's lanes as OrcaSlicer's AMS trays, and the print dialog's tray choice."""

import json
import unittest

import fake_orca  # noqa: F401
from orca_afc import trays
from orca_afc.protocol import Lane, Snapshot


def loaded(name, tool, **values):
    return Lane(name=name, maps=["T%d" % tool], prep=True, load=True, **values)


class AmsTest(unittest.TestCase):
    def test_units_of_four(self):
        snap = Snapshot(present=True, lanes=[
            loaded("lane1", 0, material="PLA", color="#FF8800", weight=250, initial_weight=1000),
            Lane(name="lane2", maps=["T1"]),
            loaded("lane9", 6, material="PETG", multi_colors=["#FF0000", "#0000FF"], color="#FF0000"),
        ])
        ams = trays.ams_json(snap, {0: "GFL99", 6: "GFG99"})
        self.assertEqual((ams["ams_exist_bits"], ams["tray_exist_bits"]), ("3", "41"))
        first, second = ams["ams"]
        self.assertEqual((first["id"], first["info"], len(first["tray"]), len(second["tray"])), ("0", "0002", 4, 3))
        t0 = first["tray"][0]
        self.assertEqual((t0["tray_info_idx"], t0["tray_color"], t0["remain"]), ("GFL99", "FF8800FF", 25))
        self.assertEqual(first["tray"][1]["tray_slot_placeholder"], "1")  # lane2 holds nothing
        t6 = second["tray"][2]
        self.assertEqual((t6["id"], t6["tray_type"], t6["cols"]), ("2", "PETG", ["FF0000FF", "0000FFFF"]))
        self.assertNotIn("remain", t6)  # no weight from AFC

    def test_loaded_tray_reading_and_spool(self):
        snap = Snapshot(present=True, current_lane="lane5", lanes=[
            loaded("lane1", 0, material="PLA", flow_k=0.035),
            loaded("lane5", 5, material="PETG", tool_loaded=True),
        ])
        extra = {0: {"spool": trays.spool_json(snap.lanes[0], ["Polymaker PolyLite PLA"], "Generic PLA"),
                     "filament_preset": "Mine"}}
        ams = trays.ams_json(snap, {0: "GFL99", 5: "GFG99"}, extra, reading={0, 99})
        self.assertEqual((ams["tray_now"], ams["tray_tar"], ams["tray_reading_bits"]), ("5", "5", "1"))
        t0 = ams["ams"][0]["tray"][0]
        self.assertEqual((t0["k"], t0["filament_preset"], t0["spool"]["names"], t0["spool"]["base"]),
                         (0.035, "Mine", ["Polymaker PolyLite PLA"], "Generic PLA"))
        self.assertEqual(t0["spool"]["pressure_advance"], 0.035)
        self.assertNotIn("k", ams["ams"][1]["tray"][1])  # no K saved on that spool

    def test_nothing_loaded_is_tray_255(self):
        snap = Snapshot(present=True, lanes=[loaded("lane1", 0)])
        self.assertEqual(trays.ams_json(snap, {})["tray_now"], "255")

    def test_no_lanes(self):
        self.assertEqual(trays.ams_json(Snapshot(present=True), {}),
                         {"ams": [], "ams_exist_bits": "0", "tray_exist_bits": "0"})

    def test_first_lane_per_t_number(self):
        a, b = loaded("a", 2), loaded("b", 2)
        self.assertIs(trays.lanes_by_tray(Snapshot(lanes=[a, b, loaded("far", 40)]))[2], a)
        self.assertNotIn(40, trays.lanes_by_tray(Snapshot(lanes=[loaded("far", 40)])))


class MappingTest(unittest.TestCase):
    def test_tray_ids(self):
        self.assertEqual(trays.mapping_from_print("[4, 0, -1]", "", 3), {0: 4, 1: 0})

    def test_unit_and_slot(self):
        second = json.dumps([{"ams_id": 1, "slot_id": 2}, {"ams_id": 255, "slot_id": 255}])
        self.assertEqual(trays.mapping_from_print("[-1, -1]", second, 2), {0: 6})

    def test_nothing_usable(self):
        self.assertEqual(trays.mapping_from_print("not json", "{}", 2), {})
        self.assertEqual(trays.mapping_from_print("", "", 0), {})


if __name__ == "__main__":
    unittest.main()
