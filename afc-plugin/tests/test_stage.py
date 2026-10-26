"""The Device tab's stage line from Klipper's objects and AFC's state."""

import unittest
from types import SimpleNamespace

from orca_afc import stage


class Snapshot:
    def __init__(self, lanes=None, message="", message_type=""):
        self.lanes = lanes or {}
        self.message, self.message_type = message, message_type

    def find_lane(self, name):
        return self.lanes.get(name)


SNAPSHOT = Snapshot({"lane8": SimpleNamespace(maps=["T4"], extruder="extruder1"),
                     "lane2": SimpleNamespace(maps=[], extruder="extruder")})


def printing(**objects):
    base = {"print_stats": {"state": "printing"}, "toolhead": {"homed_axes": "xyz", "extruder": "extruder1"},
            "heater_bed": {"temperature": 60.0, "target": 60.0},
            "extruder": {"temperature": 25.0, "target": 0.0},
            "extruder1": {"temperature": 220.0, "target": 220.0}}
    base.update(objects)
    return base


class DescribeTest(unittest.TestCase):
    def test_nothing_but_printing(self):
        self.assertEqual(stage.describe(printing(), SNAPSHOT, 2), (stage.PRINTING, ""))

    def test_not_printing(self):
        self.assertEqual(stage.describe({"print_stats": {"state": "standby"}}, SNAPSHOT, 2), (stage.PRINTING, ""))

    def test_heating_the_bed_first(self):
        objects = printing(heater_bed={"temperature": 41.6, "target": 60.0},
                           extruder1={"temperature": 150.0, "target": 220.0})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2),
                         (stage.BED_HEATING, "Heating the bed to 60 °C (now 42 °C)"))

    def test_heating_the_active_toolhead(self):
        objects = printing(extruder1={"temperature": 150.0, "target": 220.0})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2),
                         (stage.NOZZLE_HEATING, "Heating toolhead 2 to 220 °C (now 150 °C)"))
        # One toolhead: no number to tell it from the others.
        self.assertEqual(stage.describe(objects, SNAPSHOT, 1)[1], "Heating the nozzle to 220 °C (now 150 °C)")

    def test_close_to_target_is_not_heating(self):
        objects = printing(heater_bed={"temperature": 58.5, "target": 60.0},
                           extruder1={"temperature": 218.0, "target": 220.0})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2), (stage.PRINTING, ""))

    def test_homing(self):
        objects = printing(toolhead={"homed_axes": "", "extruder": "extruder1"})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2), (stage.HOMING, "Homing"))

    def test_bed_leveling(self):
        self.assertEqual(stage.describe(printing(), SNAPSHOT, 2, probing=True),
                         (stage.BED_LEVELING, "Auto bed leveling"))

    def test_a_macros_message(self):
        objects = printing(display_status={"message": "Purging"})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2), (stage.PRINTING, "Purging"))

    def test_loading_a_lane(self):
        objects = printing(AFC={"current_state": "Loading", "current_lane": "lane8"})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2),
                         (stage.LOADING, "Loading lane8 (T4) into toolhead 2"))

    def test_loading_after_afc_lets_go_of_the_lane(self):
        objects = printing(AFC={"current_state": "Loading", "current_lane": None, "next_lane": "lane8"})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2)[1], "Loading lane8 (T4) into toolhead 2")

    def test_loading_waits_on_the_nozzle(self):
        objects = printing(AFC={"current_state": "Loading", "current_lane": "lane8"},
                           extruder1={"temperature": 180.0, "target": 220.0})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2),
                         (stage.LOADING, "Heating toolhead 2 to 220 °C (now 180 °C) for lane8 (T4)"))

    def test_unloading_from_the_lanes_own_toolhead(self):
        objects = printing(AFC={"current_state": "Unloading", "current_lane": "lane2",
                                "current_toolchange": 3, "number_of_toolchanges": 12})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2),
                         (stage.UNLOADING, "Change 3 of 12: unloading lane2 from toolhead 1"))

    def test_tool_swap(self):
        objects = printing(AFC={"current_state": "ToolSwap", "current_lane": "lane2", "next_lane": "lane8"})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2),
                         (stage.CHANGING_FILAMENT, "Switching toolheads for lane8 (T4)"))

    def test_afc_before_homing(self):
        objects = printing(AFC={"current_state": "Loading", "current_lane": "lane8"},
                           toolhead={"homed_axes": "", "extruder": "extruder1"})
        self.assertEqual(stage.describe(objects, SNAPSHOT, 2)[0], stage.LOADING)

    def test_paused_by_afc(self):
        snapshot = Snapshot(message="lane8 runout, no spare lane\nLoad a spool and resume", message_type="error")
        objects = {"print_stats": {"state": "paused"}}
        self.assertEqual(stage.describe(objects, snapshot, 2),
                         (stage.RUNOUT_PAUSE, "Paused: lane8 runout, no spare lane"))

    def test_paused_without_an_afc_error(self):
        snapshot = Snapshot(message="Spoolman is offline", message_type="warning")
        self.assertEqual(stage.describe({"print_stats": {"state": "paused"}}, snapshot, 2),
                         (stage.USER_PAUSE, "Paused"))


class ProbeWatchTest(unittest.TestCase):
    def test_probe_lines_since_the_last_look(self):
        watch = stage.ProbeWatch()
        old = [{"message": "// probe at 10.000,10.000 is z=0.012", "time": 1.0}]
        self.assertFalse(watch.update(old))  # the first look only notes where the store is
        self.assertFalse(watch.update(old))
        probe = old + [{"message": "// probe at 50.000,10.000 is z=0.020", "time": 2.0}]
        self.assertTrue(watch.update(probe))
        self.assertFalse(watch.update(probe))  # nothing new
        done = probe + [{"message": "// probe at 90.000,10.000 is z=0.031", "time": 3.0},
                        {"message": "// Mesh Bed Leveling Complete", "time": 3.5}]
        self.assertFalse(watch.update(done))

    def test_bad_entries(self):
        watch = stage.ProbeWatch()
        self.assertFalse(watch.update(None))
        self.assertFalse(watch.update(["probe at", {"message": "probe at"}]))


if __name__ == "__main__":
    unittest.main()
