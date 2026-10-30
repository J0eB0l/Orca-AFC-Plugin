"""What the Device tab's lamp, tray refresh, tray popup, Load and Unload do on an AFC printer."""

import unittest

import fake_orca  # noqa: F401
from orca_afc import controls, trays
from orca_afc.protocol import AFCError, Lane, Snapshot, Unit


def lane(name, tool, unit="Turtle_1", unit_type="Box_Turtle", **values):
    values.setdefault("prep", True)
    return Lane(name=name, unit=unit, unit_type=unit_type, maps=["T%d" % tool], **values)


class FanTest(unittest.TestCase):
    OBJECTS = ["heater_fan hotend_fan", "fan_generic aux_fan", "fan", "controller_fan mcu_fan",
               "temperature_fan chamber", "led main_led", "fan_generic bad name"]
    FANS = ["fan", "fan_generic aux_fan", "heater_fan hotend_fan", "controller_fan mcu_fan", "temperature_fan chamber"]

    def test_finds_every_fan_the_part_fan_first(self):
        self.assertEqual(controls.find_fans(self.OBJECTS), self.FANS)
        self.assertEqual(controls.fan_ids(self.FANS), {"fan": 1, "fan_generic aux_fan": 16, "heater_fan hotend_fan": 17,
                                                      "controller_fan mcu_fan": 18, "temperature_fan chamber": 19})

    def test_airduct(self):
        duct = controls.airduct({"fan": {"speed": 0.3}, "heater_fan hotend_fan": {"speed": 1.0}}, self.FANS[:3])
        self.assertEqual((duct["modeCur"], duct["modeList"], duct["agent_fans"]), (-1, [], True))
        self.assertEqual(duct["parts"], [
            {"id": 16, "func": 1, "state": 30, "range": 100 << 16, "name": "", "auto": False},
            {"id": 256, "func": 16, "state": 0, "range": 100 << 16, "name": "Aux fan", "auto": False},
            {"id": 272, "func": 17, "state": 100, "range": 100 << 16, "name": "Hotend fan", "auto": True}])

    def test_the_popups_m106_sets_its_fan(self):
        self.assertEqual(controls.fan_gcode(self.FANS, "M106 P1 S128 \n"), "M106 S128")
        self.assertEqual(controls.fan_gcode(self.FANS, "M106 P16 S255"), "SET_FAN_SPEED FAN=aux_fan SPEED=1.00")
        self.assertEqual(controls.fan_gcode(self.FANS, "M106 P17 S0"), "")    # Klipper runs it
        self.assertEqual(controls.fan_gcode(self.FANS, "M106 P99 S0"), "")    # no such fan
        self.assertIsNone(controls.fan_gcode(self.FANS, "M106 S128"))         # typed G-code runs as it is
        self.assertIsNone(controls.fan_gcode([], "M106 P1 S128"))


class SpeedTest(unittest.TestCase):
    def test_levels(self):
        self.assertEqual([controls.speed_gcode(str(n)) for n in range(6)],
                         ["", "M220 S50", "M220 S100", "M220 S124", "M220 S166", ""])
        self.assertEqual(controls.speed_gcode(None), "")

    def test_report(self):
        self.assertEqual(controls.speed_report(1.0), {"spd_mag": 100, "spd_lvl": 2})
        self.assertEqual(controls.speed_report(1.24), {"spd_mag": 124, "spd_lvl": 3})
        self.assertEqual(controls.speed_report(0.6), {"spd_mag": 60, "spd_lvl": 1})
        self.assertEqual(controls.speed_report(None), {})

class LightTest(unittest.TestCase):
    def test_finds_the_printers_lights(self):
        objects = ["led main_led", "neopixel chamber", "output_pin caselight", "neopixel sb_leds",
                   "neopixel toolhead_light", "output_pin beeper", "AFC_led Turtle_1", "led", "extruder"]
        self.assertEqual(controls.find_lights(objects), ["led main_led", "neopixel chamber", "output_pin caselight"])

    def test_the_u1s_light(self):
        objects = ["led cavity_led", "fan_generic cavity_fan", "output_pin e0_heat_sw", "temperature_sensor cavity"]
        self.assertEqual(controls.find_lights(objects), ["led cavity_led"])
        self.assertEqual(controls.light_gcode(["led cavity_led"], True), ["SET_LED LED=cavity_led RED=1 GREEN=1 BLUE=1 WHITE=1"])

    def test_finds_the_chamber_sensor(self):
        self.assertEqual(controls.find_chamber(["temperature_sensor mcu_temp", "temperature_sensor cavity"]),
                         "temperature_sensor cavity")
        # A chamber heater before a sensor: it has a target.
        self.assertEqual(controls.find_chamber(["temperature_sensor chamber", "heater_generic chamber_heater"]),
                         "heater_generic chamber_heater")
        self.assertEqual(controls.find_chamber(["temperature_sensor raspberry_pi", "neopixel chamber"]), "")

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
        self.assertEqual(self.scan(lane("lane5", 4, "Bambu_AMS_1", "BambuAMS")), "AFC_BAMBU_CAPSCAN LANE=lane5")
        self.assertEqual(self.scan(lane("lane1", 0, "Ace_1", "ACE")), "AFC_ACE_RFID_RESCAN UNIT=Ace_1 LANE=lane1")
        readers = {"AFC_ACE2_rfid": {"lane2": 1}, "AFC_OpenAMS_rfid": {"lane9": 0},
                   "AFC_BoxTurtle_rfid": {"lane8": "reader0"}, "AFC_Vivid_rfid": {"lane3": 2},
                   "AFC_U1_rfid": {"e0": 0}}
        self.assertEqual(self.scan(lane("lane2", 1, "Ace2_1", "ACE2"), readers), "AFC_ACE_RFID_RESCAN UNIT=Ace2_1 LANE=lane2")
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
        self.shown = trays.layout(self.snap)

    def test_lane_of(self):
        self.assertIs(controls.lane_of(self.snap, self.shown.tool(1, 0)), self.snap.lanes[2])
        self.assertIsNone(controls.lane_of(self.snap, -1))

    def test_popup_sets_what_changed(self):
        lane1 = self.snap.lanes[0]
        request = {"tray_color": "FF0000FF", "tray_type": "PETG"}
        self.assertEqual(controls.filament_setting_gcode(self.snap, lane1, request), ["SET_MATERIAL LANE=lane1 MATERIAL=PETG"])
        request = {"tray_color": "00FF00FF", "tray_type": "pla"}
        self.assertEqual(controls.filament_setting_gcode(self.snap, lane1, request), ["SET_COLOR LANE=lane1 COLOR=00FF00"])
        self.assertEqual(controls.filament_setting_gcode(self.snap, lane1, {"tray_color": "00000000"}), [])

    def test_load_a_tray(self):
        self.assertEqual(controls.change_filament_gcode(self.snap, {"ams_id": 1, "slot_id": 0, "target": 4}, self.shown),
                         "CHANGE_TOOL LANE=lane5")

    def test_unload_the_units_loaded_lane(self):
        unload = {"ams_id": 1, "slot_id": 255, "target": 255}
        self.assertEqual(controls.change_filament_gcode(self.snap, unload, self.shown), "TOOL_UNLOAD LANE=lane6")
        unload["ams_id"] = 0  # the active toolhead's lane is in this unit
        self.assertEqual(controls.change_filament_gcode(self.snap, unload, self.shown), "TOOL_UNLOAD LANE=lane2")
        unload["ams_id"] = 3  # nothing of this unit is loaded: the active toolhead's lane
        self.assertEqual(controls.change_filament_gcode(self.snap, unload, self.shown), "TOOL_UNLOAD LANE=lane2")

    def test_nothing_to_load_or_unload(self):
        with self.assertRaises(AFCError):
            controls.change_filament_gcode(self.snap, {"ams_id": 2, "slot_id": 0}, self.shown)
        empty = Snapshot(present=True, lanes=[lane("lane1", 0)])
        with self.assertRaises(AFCError):
            controls.change_filament_gcode(empty, {"ams_id": 0, "slot_id": 255}, trays.layout(empty))

    def test_load_and_unload_by_afc_unit(self):
        self.snap.units = [Unit("Turtle_1", "Box_Turtle", ["lane1", "lane2"]), Unit("Bambu_AMS_1", "BambuAMS", ["lane5", "lane6"])]
        shown = trays.layout(self.snap, by_unit=True)
        self.assertEqual(controls.change_filament_gcode(self.snap, {"ams_id": 1, "slot_id": 1, "target": 1}, shown),
                         "CHANGE_TOOL LANE=lane6")
        unload = {"ams_id": 1, "slot_id": 255, "target": 255}
        self.assertEqual(controls.change_filament_gcode(self.snap, unload, shown), "TOOL_UNLOAD LANE=lane6")



class CameraTest(unittest.TestCase):
    def test_mjpeg_stream_on_the_printers_web_server(self):
        cams = [{"name": "off", "service": "mjpegstreamer", "enabled": False, "stream_url": "/webcam3/?action=stream"},
                {"name": "cam", "service": "mjpegstreamer-adaptive", "enabled": True,
                 "stream_url": "/webcam/?action=stream", "snapshot_url": "/webcam/?action=snapshot"}]
        # Moonraker's own port is not where Fluidd's webcams are.
        self.assertEqual(controls.camera(cams, "http://192.168.4.47:7125"), ("http", "http://192.168.4.47/webcam/?action=stream"))
        self.assertEqual(controls.camera(cams, "http://printer.local:4408"),
                         ("http", "http://printer.local:4408/webcam/?action=stream"))

    def test_snapshot_when_the_stream_is_not_mjpeg(self):
        cams = [{"service": "iframe", "stream_url": "http://192.168.4.47:8092/?embed"},
                {"service": "webrtc-camerastreamer", "stream_url": "/webcam/webrtc",
                 "snapshot_url": "http://10.0.0.5/webcam/snapshot.jpg"}]
        self.assertEqual(controls.camera(cams, "http://10.0.0.5"), ("http_snapshot", "http://10.0.0.5/webcam/snapshot.jpg"))

    def test_nozzle_camera_last(self):
        nozzle = {"name": "Nozzle Cam", "service": "mjpegstreamer", "stream_url": "/webcam2/?action=stream"}
        main = {"name": "Printer", "service": "mjpegstreamer", "stream_url": "/webcam/?action=stream"}
        self.assertEqual(controls.camera([nozzle, main], "http://10.0.0.5"), ("http", "http://10.0.0.5/webcam/?action=stream"))
        self.assertEqual(controls.camera([nozzle], "http://10.0.0.5"), ("http", "http://10.0.0.5/webcam2/?action=stream"))

    def test_no_camera(self):
        self.assertEqual(controls.camera([], "http://10.0.0.5"), ("", ""))
        self.assertEqual(controls.camera([{"service": "webrtc-go2rtc", "stream_url": "/go2rtc/"}], "http://10.0.0.5"), ("", ""))


if __name__ == "__main__":
    unittest.main()
