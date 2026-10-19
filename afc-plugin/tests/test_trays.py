"""AFC's lanes as OrcaSlicer's AMS trays, and the print dialog's tray choice."""

import json
import unittest

import fake_orca  # noqa: F401
from orca_afc import trays
from orca_afc.protocol import Dryer, Lane, Snapshot, Unit


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
        self.assertEqual((first["id"], first["info"], len(first["tray"]), len(second["tray"])), ("0", "0001", 4, 3))
        t0 = first["tray"][0]
        self.assertEqual((t0["tray_info_idx"], t0["tray_color"], t0["remain"]), ("GFL99", "FF8800FF", 25))
        self.assertEqual(t0["remain_g"], 250)
        self.assertTrue(ams["calibrate_remain_flag"])  # OrcaSlicer draws spool fill only with it
        self.assertEqual(first["tray"][1]["tray_slot_placeholder"], "1")  # lane2 holds nothing
        t6 = second["tray"][2]
        self.assertEqual((t6["id"], t6["tray_type"], t6["cols"]), ("2", "PETG", ["FF0000FF", "0000FFFF"]))
        self.assertNotIn("remain", t6)  # no weight from AFC
        self.assertNotIn("remain_g", t6)

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
        self.assertEqual(ams["ams"][1]["tray"][1]["k"], 0.0)  # no K saved on that spool

    def test_toolhead_of_each_tray(self):
        snap = Snapshot(present=True, lanes=[loaded("lane1", 0, extruder_index=2), loaded("lane2", 1)])
        first = trays.ams_json(snap, {0: "GFL99", 1: "GFL99"})["ams"][0]["tray"]
        self.assertEqual(first[0]["toolhead"], 2)
        self.assertNotIn("toolhead", first[1])  # AFC names no extruder for it

    def test_a_lane_in_its_toolhead_shows_loaded(self):
        snap = Snapshot(present=True, current_lane="lane1", lanes=[
            loaded("lane1", 0, tool_loaded=True), loaded("lane2", 1, tool_loaded=True), loaded("lane3", 2)])
        first = trays.ams_json(snap, {})["ams"][0]["tray"]
        self.assertEqual([t.get("in_tool") for t in first[:3]], [True, True, None])  # lane2 in a docked tool

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


def units_snapshot():
    """A Box Turtle, a toolchanger's two tools, a Bambu AMS HT and an ACE, drying."""
    lanes = [loaded("lane%d" % i, i - 1, unit="Turtle_1") for i in range(1, 5)]
    lanes += [loaded("extruder1", 4, unit="Tools"), loaded("extruder2", 5, unit="Tools", tool_loaded=True)]
    lanes += [loaded("lane12", 12, unit="Bambu_AMS_1")]
    lanes += [loaded("ace%d" % i, 6 + i, unit="Ace_1") for i in range(4)]
    ht = Dryer(kind="bambu", model="ht", max_temp=85, temperature=26.04, humidity=35.4)
    ace = Dryer(kind="ace", model="ACE", max_temp=55, drying=True, target=55, remaining_s=3599, duration_s=4 * 3600)
    units = [Unit("Turtle_1", "Box_Turtle", ["lane1", "lane2", "lane3", "lane4"]),
             Unit("Tools", "Toolchanger", ["extruder1", "extruder2"]),
             Unit("Bambu_AMS_1", "BambuAMS", ["lane12"], ht),
             Unit("Ace_1", "ACE", ["ace0", "ace1", "ace2", "ace3"], ace)]
    return Snapshot(present=True, current_lane="extruder2", lanes=lanes, units=units)


class UnitLayoutTest(unittest.TestCase):
    def test_afc_units_as_ams_and_ams_ht(self):
        snap = units_snapshot()
        ams = trays.ams_json(snap, {}, reading={12}, by_unit=True, dry_filaments={"Ace_1": "PETG"})
        self.assertEqual([(u["id"], u["info"]) for u in ams["ams"]],
                         [("0", "0001"), ("1", "400023"), ("128", "0004"), ("129", "0004"), ("130", "0004")])
        turtle, ace, tool1, tool2, ht = ams["ams"]
        self.assertEqual((turtle["humidity"], "dryer" in turtle), ("0", False))  # an AMS, no humidity shown
        # Four-tray units show their AFC unit's name, a toolchanger's tools their extruder's;
        # another AMS HT keeps OrcaSlicer's.
        self.assertEqual([u.get("unit_name") for u in ams["ams"]], ["Turtle_1", "Ace_1", "extruder1", "extruder2", None])
        self.assertEqual([t["tool"] for t in turtle["tray"]] + [t["tool"] for t in ace["tray"]], [0, 1, 2, 3, 6, 7, 8, 9])
        self.assertEqual((tool1["tray"][0]["tool"], tool1["dryer"], tool2["tray"][0]["tool"], ht["tray"][0]["tool"]),
                         (4, False, 5, 12))
        self.assertEqual((ht["humidity_raw"], ht["humidity"], ht["temp"], ht["dry_time"]), ("35", "4", "26.0", 0))
        self.assertNotIn("dryer", ht)
        self.assertEqual((ace["dry_time"], ace["dry_setting"]),
                         (60, {"dry_filament": "PETG", "dry_temperature": 55, "dry_duration": 4}))
        # Unit bits 0-1 and 4-6, tray bits 0-7 and 16-18, the HT spinning on bit 18.
        self.assertEqual((ams["ams_exist_bits"], ams["tray_exist_bits"], ams["tray_reading_bits"]), ("73", "700FF", "40000"))
        self.assertEqual(ams["tray_now"], "129")  # extruder2, T5

    def test_ids_back_to_t_numbers(self):
        shown = trays.layout(units_snapshot(), by_unit=True)
        self.assertEqual((shown.tool(1, 2), shown.tool(129, 0), shown.tool(129, 1), shown.tool(2, 0)), (8, 5, -1, -1))
        self.assertEqual((shown.tool_of_tray(6), shown.tool_of_tray(130), shown.tool_of_tray(-1)), (8, 12, -1))
        self.assertEqual((shown.tray(8), shown.tray(12), shown.tray(30)), (6, 130, trays.NO_TRAY))
        self.assertEqual(trays.mapping_from_print("[130, -1]", json.dumps([{}, {"ams_id": 1, "slot_id": 0}]), 2, shown),
                         {0: 12, 1: 6})

    def test_a_lane_holding_two_t_numbers_is_one_tray(self):
        # A print mapped T0 onto ace0 beside its own T6, as AFC's SET_MAP does.
        # As two trays the Ace would split in two, one unit more than fits.
        snap = units_snapshot()
        for n, first in ((2, 13), (3, 17)):
            names = ["t%d_%d" % (n, i) for i in range(4)]
            snap.lanes += [loaded(name, first + i, unit="Turtle_%d" % n) for i, name in enumerate(names)]
            snap.units.append(Unit("Turtle_%d" % n, "Box_Turtle", names))
        snap.find_lane("lane1").maps, snap.find_lane("ace0").maps = [], ["T6", "T0"]
        shown = trays.layout(snap, by_unit=True)
        self.assertTrue(shown.by_unit)
        self.assertEqual([box.tools for box in shown.boxes if not box.single],
                         [[1, 2, 3], [6, 7, 8, 9], [13, 14, 15, 16], [17, 18, 19, 20]])
        self.assertEqual((shown.tray(0), shown.tray(6), shown.tool(1, 0)), (4, 4, 6))

    def test_a_unit_of_more_than_four_lanes_is_numbered(self):
        snap = units_snapshot()
        snap.lanes += [loaded("lane5", 13, unit="Turtle_1")]
        snap.find_unit("Turtle_1").lanes.append("lane5")
        ams = trays.ams_json(snap, {}, by_unit=True)
        self.assertEqual([u.get("unit_name") for u in ams["ams"] if int(u["id"]) < trays.HT_ID],
                         ["Turtle_1-1", "Turtle_1-2", "Ace_1"])
        self.assertNotIn("unit_name", trays.ams_json(snap, {})["ams"][0])  # by T number

    def test_t_numbers_when_the_units_do_not_fit(self):
        snap = units_snapshot()
        for n in range(2, 5):  # five units of several lanes beside the HTs, one more than fits
            names = ["t%d_%d" % (n, i) for i in range(2)]
            snap.lanes += [loaded(name, 13 + 2 * (n - 2) + i, unit="Turtle_%d" % n) for i, name in enumerate(names)]
            snap.units.append(Unit("Turtle_%d" % n, "Box_Turtle", names))
        shown = trays.layout(snap, by_unit=True)
        self.assertFalse(shown.by_unit)
        self.assertEqual((shown.tool(3, 0), shown.tray(12)), (12, 12))


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
