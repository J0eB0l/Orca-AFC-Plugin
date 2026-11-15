"""The settings AFC printers are offered: AFC's own, preheating, Klipper's limits."""

import unittest

import fake_orca
from orca_afc import offers

orca = fake_orca.orca

START = r"G28\nPRINT_START BED=[bed_temperature_initial_layer_single] ; heat\nG1 Z5"
PRINTER = {
    "afc_enabled": "1", "afc_settings_check": "1",
    "enable_filament_ramming": "1", "high_current_on_filament_swap": "0",
    "cooling_tube_retraction": "91.5", "cooling_tube_length": "0", "parking_pos_retraction": "92",
    "extra_loading_move": "-2",
    "retract_length_toolchange": "10,0", "retract_restart_extra_toolchange": "0,0",
    "machine_start_gcode": START,
    "nozzle_diameter": "0.4,0.4", "single_extruder_multi_material": "0",
    "machine_max_speed_x": "500,200", "machine_max_speed_y": "300,300", "machine_max_acceleration_x": "20000,20000",
}


def get(values):
    return lambda key: values.get(key)


class ChangesTest(unittest.TestCase):
    def test_afc_changes(self):
        self.assertEqual(offers.afc_changes(get(PRINTER)), {
            "enable_filament_ramming": "0",
            "cooling_tube_retraction": "0", "parking_pos_retraction": "0", "extra_loading_move": "0",
            "retract_length_toolchange": "0,0",
            "machine_start_gcode": r"G28\nPRINT_START BED=[bed_temperature_initial_layer_single] TOOL={initial_tool} ; heat\nG1 Z5",
        })

    def test_a_start_line_naming_its_tool_stays(self):
        for line in ("PRINT_START TOOL=0", "START_PRINT INITIAL_TOOL={initial_tool}"):
            self.assertNotIn("machine_start_gcode", offers.afc_changes(get({"machine_start_gcode": line})))
        self.assertEqual(offers.afc_changes(get({"machine_start_gcode": r"M117 \"hi\"\nprint_start"})),
                         {"machine_start_gcode": r"M117 \"hi\"\nprint_start TOOL={initial_tool}"})
        self.assertEqual(offers.afc_changes(get({"machine_start_gcode": "G28"})), {})

    def test_limit_changes(self):
        limits = {"max_velocity": 300.0, "max_accel": 20000.0, "square_corner_velocity": 5.0}
        self.assertEqual(offers.limit_changes(get(PRINTER), limits), {"machine_max_speed_x": "300,300"})
        self.assertEqual(offers.limit_changes(get(PRINTER), {}), {})

    def test_preheat_is_for_toolchangers_with_ooze_prevention_off(self):
        self.assertTrue(offers.wants_preheat(get(PRINTER), get({"ooze_prevention": "0"})))
        self.assertFalse(offers.wants_preheat(get(PRINTER), get({"ooze_prevention": "1"})))
        self.assertFalse(offers.wants_preheat(get(dict(PRINTER, nozzle_diameter="0.4")), get({"ooze_prevention": "0"})))
        self.assertFalse(offers.wants_preheat(get(dict(PRINTER, single_extruder_multi_material="1")),
                                              get({"ooze_prevention": "0"})))


class OffersTest(unittest.TestCase):
    def setUp(self):
        orca.notifications.clear()
        orca.commits.clear()
        self.bundle = fake_orca.PresetBundle(printer=fake_orca.Preset(dict(PRINTER), name="U1"))
        self.bundle.process = fake_orca.Preset({"ooze_prevention": "0"}, name="0.20mm")
        orca.bundle = self.bundle
        self.addCleanup(setattr, orca, "bundle", fake_orca.PresetBundle())
        self.offers = offers.Offers(lambda job: job())

    def click(self, index):
        self.assertTrue(orca.notifications[index][3]())

    def test_each_offer_once_and_a_click_applies_it(self):
        self.offers.check(self.bundle)
        self.offers.check(self.bundle)
        self.assertEqual([n[2] for n in orca.notifications], ["Use AFC's settings", "Turn on preheating"])
        self.click(0)
        self.click(1)
        self.assertEqual([(c[0], c[1]) for c in orca.commits], [("printer", "U1"), ("process", "0.20mm")])
        self.assertEqual(orca.commits[0][2]["enable_filament_ramming"], "0")
        self.assertEqual(orca.commits[1][2], {"ooze_prevention": "1"})

    def test_klipper_limits(self):
        self.offers.check_limits(self.bundle, {"max_velocity": 300.0})
        self.offers.check_limits(self.bundle, {"max_velocity": 300.0})
        self.assertEqual([n[2] for n in orca.notifications], ["Use Klipper's limits"])
        self.click(0)
        self.assertEqual(orca.commits, [("printer", "U1", {"machine_max_speed_x": "300,300"})])

    def test_off_when_the_printer_says_so(self):
        for key in ("afc_enabled", "afc_settings_check"):
            self.bundle.printer.values[key] = "0"
            self.offers.check(self.bundle)
            self.offers.check_limits(self.bundle, {"max_velocity": 300.0})
            self.bundle.printer.values[key] = "1"
        self.assertEqual(orca.notifications, [])

    def test_on_where_orcaslicer_has_no_afc_settings(self):
        del self.bundle.printer.values["afc_enabled"], self.bundle.printer.values["afc_settings_check"]
        self.offers.check(self.bundle)
        self.assertEqual(len(orca.notifications), 2)

    def test_a_click_after_switching_presets_changes_the_one_edited_then(self):
        self.offers.check(self.bundle)
        self.bundle.printer = fake_orca.Preset(dict(PRINTER, enable_filament_ramming="0"), name="Other")
        self.click(0)
        self.assertEqual([(c[0], c[1]) for c in orca.commits], [("printer", "Other")])
        self.assertNotIn("enable_filament_ramming", orca.commits[0][2])

    def test_apply_leaves_a_preset_switched_to_meanwhile(self):
        offers.apply("printer", "U1", {"enable_filament_ramming": "0"})
        offers.apply("printer", "Gone", {"enable_filament_ramming": "0"})
        self.assertEqual([c[1] for c in orca.commits], ["U1"])


if __name__ == "__main__":
    unittest.main()
