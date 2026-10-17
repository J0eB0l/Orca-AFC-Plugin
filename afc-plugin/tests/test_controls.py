"""What the Device tab's lamp, tray refresh, tray popup, Load and Unload do on an AFC printer."""

import unittest

import fake_orca  # noqa: F401
from orca_afc import controls
from orca_afc.protocol import AFCError, Lane, Snapshot


def lane(name, tool, unit="Turtle_1", unit_type="Box_Turtle", **values):
    values.setdefault("prep", True)
    return Lane(name=name, unit=unit, unit_type=unit_type, maps=["T%d" % tool], **values)


class LightTest(unittest.TestCase):
    def test_finds_the_printers_lights(self):
        objects = ["led main_led", "neopixel chamber", "output_pin caselight", "neopixel sb_leds",
                   "neopixel toolhead_light", "output_pin beeper", "AFC_led Turtle_1", "led", "extruder"]
        self.assertEqual(controls.find_lights(objects), ["led main_led", "neopixel chamber", "output_pin caselight"])

    def test_on_when_any_channel_is(self):
        lights = ["led main_led", "output_pin caselight"]
        off = {"led main_led": {"color_data": [[0.0, 0.0, 0.0, 0.0]]}, "output_pin caselight": {"value": 0.0}}
        self.assertIs(controls.lights_on(off, lights), False)
        off["led main_led"]["color_data"] = [[0.0, 0.0, 0.0, 1.0]]
        self.assertIs(controls.lights_on(off, lights), True)
        self.assertIsNone(controls.lights_on({}, lights))  # Klipper reported none of them

    def test_switch(self):
        lights = ["led main_led", "output_pin caselight"]
        self.assertEqual(controls.light_gcode(lights, True),
                         ["SET_LED LED=main_led RED=1 GREEN=1 BLUE=1 WHITE=1", "SET_PIN PIN=caselight VALUE=1"])
        self.assertEqual(controls.light_gcode(lights, False)[0], "SET_LED LED=main_led RED=0 GREEN=0 BLUE=0 WHITE=0")


class RfidTest(unittest.TestCase):
    def scan(self, the_lane, readers=None):
        return controls.rfid_scan_gcode(the_lane, readers or {})

    def test_each_units_own_command(self):
        self.assertEqual(self.scan(lane("lane5", 4, "Bambu_AMS_1", "BambuAMS")), "AFC_BAMBU_SCAN LANE=lane5")
        self.assertEqual(self.scan(lane("lane1", 0, "Ace_1", "ACE")), "ACE_RFID_RESCAN UNIT=Ace_1 LANE=lane1")
        readers = {"AFC_ACE2_rfid": {"lane2": 1}, "AFC_OpenAMS_rfid": {"lane9": 0},
                   "AFC_BoxTurtle_rfid": {"lane8": "reader0"}, "AFC_Vivid_rfid": {"lane3": 2},
                   "AFC_U1_rfid": {"e0": 0}}
        self.assertEqual(self.scan(lane("lane2", 1, "Ace2_1", "ACE2"), readers), "ACE_RFID_RESCAN UNIT=Ace2_1 LANE=lane2")
        self.assertEqual(self.scan(lane("lane9", 8, "OpenAMS_1", "OpenAMS"), readers),
                         "AFC_OAMS_RFID_SCAN UNIT=OpenAMS_1 LANE=lane9")
        self.assertEqual(self.scan(lane("lane8", 7), readers), "AFC_BT_RFID_STAGE LANE=lane8")
        self.assertEqual(self.scan(lane("lane3", 2, "ViViD_1", "ViViD"), readers), "VIVID_RFID_READ LANE=lane3")
        self.assertEqual(self.scan(lane("e0", 0, "U1", "Toolchanger"), readers), "FILAMENT_DT_UPDATE CHANNEL=0")

    def test_no_reader_no_command(self):
        self.assertEqual(self.scan(lane("lane1", 0)), "")                          # a Box Turtle without one
        self.assertEqual(self.scan(lane("lane2", 1, "Ace2_1", "ACE2")), "")         # an ACE 2 without AFC's reader
        self.assertEqual(self.scan(lane("lane9", 8, "OpenAMS_1", "OpenAMS")), "")

    def test_reader_lanes(self):
        status = {"AFC_ACE2_rfid": {"lane_slot_map": {"lane2": 1}}, "AFC_Vivid_rfid": {"lane_slot_map": None},
                  "AFC_U1_rfid": {"lane_channel_map": {"e0": 0}}}
        self.assertEqual(controls.reader_lanes(status), {"AFC_ACE2_rfid": {"lane2": 1}, "AFC_U1_rfid": {"e0": 0}})


class TrayTest(unittest.TestCase):
    def setUp(self):
        self.snap = Snapshot(present=True, current_lane="lane2", lanes=[
            lane("lane1", 0, color="#FF0000", material="PLA"),
            lane("lane2", 1, material="PETG", tool_loaded=True),
            lane("lane5", 4, "Bambu_AMS_1", "BambuAMS", material="PLA"),
            lane("lane6", 5, "Bambu_AMS_1", "BambuAMS", material="PLA", tool_loaded=True),
        ])

    def test_tray_of(self):
        self.assertEqual((controls.tray_of(1, 2), controls.tray_of(255, 0), controls.tray_of(0, 4)), (6, -1, -1))
        self.assertIs(controls.lane_of(self.snap, 4), self.snap.lanes[2])

    def test_popup_sets_what_changed(self):
        lane1 = self.snap.lanes[0]
        request = {"tray_color": "FF0000FF", "tray_type": "PETG"}
        self.assertEqual(controls.filament_setting_gcode(self.snap, lane1, request), ["SET_MATERIAL LANE=lane1 MATERIAL=PETG"])
        request = {"tray_color": "00FF00FF", "tray_type": "pla"}
        self.assertEqual(controls.filament_setting_gcode(self.snap, lane1, request), ["SET_COLOR LANE=lane1 COLOR=00FF00"])
        self.assertEqual(controls.filament_setting_gcode(self.snap, lane1, {"tray_color": "00000000"}), [])

    def test_load_a_tray(self):
        self.assertEqual(controls.change_filament_gcode(self.snap, {"ams_id": 1, "slot_id": 0, "target": 4}),
                         "CHANGE_TOOL LANE=lane5")

    def test_unload_the_units_loaded_lane(self):
        unload = {"ams_id": 1, "slot_id": 255, "target": 255}
        self.assertEqual(controls.change_filament_gcode(self.snap, unload), "TOOL_UNLOAD LANE=lane6")
        unload["ams_id"] = 0  # the active toolhead's lane is in this unit
        self.assertEqual(controls.change_filament_gcode(self.snap, unload), "TOOL_UNLOAD LANE=lane2")
        unload["ams_id"] = 3  # nothing of this unit is loaded: the active toolhead's lane
        self.assertEqual(controls.change_filament_gcode(self.snap, unload), "TOOL_UNLOAD LANE=lane2")

    def test_nothing_to_load_or_unload(self):
        with self.assertRaises(AFCError):
            controls.change_filament_gcode(self.snap, {"ams_id": 2, "slot_id": 0})
        empty = Snapshot(present=True, lanes=[lane("lane1", 0)])
        with self.assertRaises(AFCError):
            controls.change_filament_gcode(empty, {"ams_id": 0, "slot_id": 255})


if __name__ == "__main__":
    unittest.main()
