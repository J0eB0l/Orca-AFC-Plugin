"""Sync layout to AFC: the suggested spool layout written back to AFC's lanes."""

import unittest

import fake_orca  # noqa: F401
from orca_afc import layout
from orca_afc.protocol import AFCError, Lane, Snapshot


def lane(name, maps, spool_id=0, material="", color="", loaded=True):
    return Lane(name=name, unit="Turtle_1", maps=list(maps), spool_id=spool_id, material=material, color=color,
                prep=loaded)


def move(name, tool, spool_id=0, material="PLA", color="#FF0000", was=None, **more):
    out = {"lane": name, "tool": tool, "spool_id": spool_id, "material": material, "color": color,
           "was": was or {}}
    out.update(more)
    return out


class LayoutTest(unittest.TestCase):
    def test_a_moved_spool_takes_its_old_lanes_spool_and_t_number(self):
        # Spool 7 went from lane1 (T0) to lane3 (T2); lane3 forgot its old spool when it came out.
        snapshot = Snapshot(lanes=[lane("lane1", ["T0"], loaded=False), lane("lane3", ["T2"])])
        lines, outcome = layout.apply_gcode(snapshot, [move("lane3", 0, 7, weight=640.5, nozzle_temp=215,
                                                            was={"spool_id": 3, "material": "PETG"})])
        self.assertEqual(lines, ["SET_SPOOL_ID LANE=lane3 SPOOL_ID=7", "SET_MATERIAL LANE=lane3 MATERIAL=PLA",
                                 "SET_COLOR LANE=lane3 COLOR=FF0000", "SET_WEIGHT LANE=lane3 WEIGHT=640.5",
                                 "AFC_SET_SPOOL_TEMP LANE=lane3 EXTRUDER_TEMP=215", "SET_MAP LANE=lane3 MAP=T0"])
        self.assertEqual(outcome, {"lane3": "written"})

    def test_a_lane_that_read_its_spool_keeps_it(self):
        # lane3 knew spool 3 when the layout was suggested and read spool 9 from its tag since.
        snapshot = Snapshot(lanes=[lane("lane3", ["T2"], 9, "PETG", "#00FF00")])
        lines, outcome = layout.apply_gcode(snapshot, [move("lane3", 0, 7, was={"spool_id": 3, "material": "PETG"})])
        self.assertEqual((lines, outcome), (["SET_MAP LANE=lane3 MAP=T0"], {"lane3": "kept"}))

    def test_a_lane_still_holding_its_old_spool_is_written(self):
        snapshot = Snapshot(lanes=[lane("lane3", ["T0"], 3, "PETG", "#00FF00")])
        lines, outcome = layout.apply_gcode(snapshot, [move("lane3", 0, material="PLA", color="",
                                                            was={"spool_id": 3, "material": "petg", "color": "00ff00"})])
        self.assertEqual((lines, outcome), (["SET_MATERIAL LANE=lane3 MATERIAL=PLA"], {"lane3": "written"}))

    def test_an_empty_lane_is_left_alone(self):
        snapshot = Snapshot(lanes=[lane("lane3", ["T2"], loaded=False)])
        self.assertEqual(layout.apply_gcode(snapshot, [move("lane3", 0, 7)]), ([], {"lane3": "empty"}))

    def test_swapped_spools_let_go_first(self):
        # Spools 1 and 2 swapped lanes, which still name their old spools.
        snapshot = Snapshot(lanes=[lane("lane1", ["T0"], 1, "PLA", "#FF0000"), lane("lane2", ["T1"], 2, "PLA", "#0000FF")])
        lines, outcome = layout.apply_gcode(snapshot, [
            move("lane2", 0, 1, color="#FF0000", was={"spool_id": 2, "material": "PLA", "color": "#0000FF"}),
            move("lane1", 1, 2, color="#0000FF", was={"spool_id": 1, "material": "PLA", "color": "#FF0000"})])
        self.assertEqual(lines[:2], ["SET_SPOOL_ID LANE=lane1 SPOOL_ID=", "SET_SPOOL_ID LANE=lane2 SPOOL_ID="])
        self.assertEqual(lines[-2:], ["SET_MAP LANE=lane2 MAP=T0", "SET_MAP LANE=lane1 MAP=T1"])
        self.assertIn("SET_SPOOL_ID LANE=lane2 SPOOL_ID=1", lines)
        self.assertEqual(outcome, {"lane1": "written", "lane2": "written"})

    def test_a_spool_another_lane_read_is_not_taken_from_it(self):
        snapshot = Snapshot(lanes=[lane("lane1", ["T5"], 7, "PLA", "#FF0000"), lane("lane3", ["T2"])])
        lines, _ = layout.apply_gcode(snapshot, [move("lane1", 1, 4, was={"spool_id": 4}), move("lane3", 0, 7)])
        self.assertNotIn("SET_SPOOL_ID LANE=lane1 SPOOL_ID=", lines)
        self.assertNotIn("SET_SPOOL_ID LANE=lane3 SPOOL_ID=7", lines)
        self.assertIn("SET_MAP LANE=lane3 MAP=T0", lines)

    def test_bad_moves(self):
        snapshot = Snapshot(lanes=[lane("lane1", ["T0"])])
        for moves in ([], None, [{"lane": "lane9", "tool": 0}], [{"lane": "lane1", "tool": -1}],
                      [{"lane": "lane1", "tool": True}], [{"lane": "lane1;", "tool": 0}], ["lane1"]):
            with self.assertRaises(AFCError):
                layout.apply_gcode(snapshot, moves)


if __name__ == "__main__":
    unittest.main()
