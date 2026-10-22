"""AFC's lane, dryer and G-code rules, ported from the Snapmaker build's
tests/libslic3r/test_afc_protocol.cpp."""

import copy
import unittest

import fake_orca  # noqa: F401  (puts orca_afc on the path)
from orca_afc import protocol as p
from orca_afc.protocol import AFCError, Lane, Snapshot, U1PrintOptions


def lane_status(name, unit, index, maps, extruder, color, material, loaded, weight=1000):
    return {"name": name, "unit": unit, "lane": index, "map": list(maps), "extruder": extruder,
            "color": color, "material": material,
            "spool_vendor": "Polymaker" if loaded else "", "filament_name": "PolyLite" if loaded else "",
            "spool_id": 12 if loaded else None, "weight": weight, "extruder_temp": 215, "bed_temp": 60,
            "prep": loaded, "load": loaded, "tool_loaded": False, "multi_color_hexes": [],
            "status": "Ready" if loaded else "None"}


def webhook_fixture():
    """Shape of AFC's printer/afc/status webhook: {"status:": {"AFC": {...}}}."""
    afc = {
        "Turtle_1": {
            "system": {"type": "Box_Turtle", "hub_loaded": False},
            "lane1": lane_status("lane1", "Turtle_1", 1, ["T0"], "extruder", "#ff0000", "PLA", True),
            "lane2": lane_status("lane2", "Turtle_1", 2, ["T1"], "extruder", "#00FF00", "PETG", True, 40),
        },
        "Bambu_AMS_1": {
            "system": {"type": "BambuAMS", "hub_loaded": True},
            "lane5": lane_status("lane5", "Bambu_AMS_1", 1, ["T4"], "extruder1", "0000FF", "PLA", True),
            "lane6": lane_status("lane6", "Bambu_AMS_1", 2, ["T5"], "extruder1", "", "", False),
        },
        "system": {"version": "1.0.30", "current_load": "lane5", "spoolman": "http://spoolman:7912",
                   "extruders": {"extruder": {}, "extruder1": {}}},
    }
    return {"status:": {"AFC": afc}}


def parsed(fixture=None):
    s = p.parse_status_endpoint(fixture or webhook_fixture())
    assert s is not None
    return s


def dryer_units():
    afc = {
        "Ace2_1": {"system": {"type": "ACE2"},
                   "lane0": lane_status("lane0", "Ace2_1", 1, ["T0"], "extruder", "", "PLA", True)},
        "Bambu_AMS_1": {"system": {"type": "BambuAMS"},
                        "lane12": lane_status("lane12", "Bambu_AMS_1", 1, ["T12"], "extruder", "", "PLA", True)},
        "Turtle_1": {"system": {"type": "Box_Turtle"},
                     "lane8": lane_status("lane8", "Turtle_1", 1, ["T8"], "extruder5", "", "", False)},
    }
    return afc


class WebhookTest(unittest.TestCase):
    def test_units_and_lanes(self):
        s = parsed()
        self.assertTrue(s.present)
        self.assertEqual(s.version, "1.0.30")
        self.assertTrue(s.spoolman)
        self.assertEqual(s.spoolman_url, "http://spoolman:7912")
        self.assertEqual(s.current_lane, "lane5")
        self.assertEqual(len(s.units), 2)
        self.assertEqual(len(s.lanes), 4)
        self.assertEqual(s.extruders, ["extruder", "extruder1"])

        l1 = s.find_lane("lane1")
        self.assertEqual((l1.unit, l1.unit_type, l1.color, l1.spool_id, l1.extruder_index),
                         ("Turtle_1", "Box_Turtle", "#FF0000", 12, 0))
        self.assertTrue(l1.has_filament)
        l5 = s.find_lane("lane5")
        self.assertEqual((l5.color, l5.extruder_index, l5.unit_type), ("#0000FF", 1, "BambuAMS"))
        self.assertIs(s.lane_for_tool(4), l5)
        self.assertIsNone(s.lane_for_tool(3))
        self.assertEqual(s.max_tool(), 5)

    def test_in_tool_counts_as_loaded(self):
        # A U1 toolhead lane after a reset: its extruder holds it, tool_loaded still reads false.
        fixture = webhook_fixture()
        afc = fixture["status:"]["AFC"]
        afc["Turtle_1"]["lane1"]["filament_status"] = "In Tool"
        afc["Turtle_1"]["lane2"]["filament_status"] = "Ready"
        afc["Bambu_AMS_1"]["lane5"]["filament_status"] = "In Tool"
        afc["Bambu_AMS_1"]["lane5"]["tool_loaded"] = True
        s = parsed(fixture)
        self.assertTrue(s.find_lane("lane1").tool_loaded)
        self.assertFalse(s.find_lane("lane2").tool_loaded)
        self.assertTrue(s.find_lane("lane5").tool_loaded)
        self.assertFalse(s.find_lane("lane6").tool_loaded)
        self.assertIs(p.to_json(s)["lanes"][0]["tool_loaded"], True)

    def test_without_wrapper_and_junk(self):
        s = p.parse_status_endpoint({"AFC": webhook_fixture()["status:"]["AFC"]})
        self.assertEqual(len(s.lanes), 4)
        # Moonraker's own wrapper around the webhook's.
        s = p.parse_status_endpoint({"result": webhook_fixture()})
        self.assertEqual(len(s.lanes), 4)
        self.assertIsNone(p.parse_status_endpoint([]))
        self.assertIsNone(p.parse_status_endpoint({"status": {"toolhead": {}}}))
        self.assertIsNone(p.parse_status_endpoint(None))

    def test_natural_order(self):
        # Printer 1's layout: key order would give AMS_1, Ace2_1, Bambu..., lane10 before lane8.
        afc = {
            "Ace2_1": {"system": {"type": "ACE2"},
                       "lane0": lane_status("lane0", "Ace2_1", 1, ["T0"], "extruder", "", "PLA", True)},
            "AMS_1": {"system": {"type": "OpenAMS"},
                      "lane4": lane_status("lane4", "AMS_1", 1, ["T4"], "extruder4", "", "", False)},
            "Turtle_1": {"system": {"type": "Box_Turtle"}},
            "Bambu_AMS_1": {"system": {"type": "BambuAMS"},
                            "lane12": lane_status("lane12", "Bambu_AMS_1", 1, ["T12"], "extruder", "#0A2989",
                                                  "PLA", True)},
            "Bambu_AMS_2": {"system": {"type": "BambuAMS"}},  # unclaimed BridgeBox spare
            "Tools": {"system": {"type": "Toolchanger"}},
            "system": {"version": "1.2.7"},
        }
        for i in (10, 8, 11, 9):
            afc["Turtle_1"]["lane%d" % i] = lane_status("lane%d" % i, "Turtle_1", i - 7, ["T%d" % i],
                                                        "extruder5", "", "", False)
        s = parsed({"status:": {"AFC": dict(sorted(afc.items()))}})
        self.assertEqual([u.name for u in s.units],
                         ["Ace2_1", "AMS_1", "Turtle_1", "Bambu_AMS_1", "Bambu_AMS_2", "Tools"])
        self.assertEqual(s.units[2].lanes, ["lane8", "lane9", "lane10", "lane11"])
        self.assertEqual(len(s.lanes), 7)
        self.assertEqual([s.lanes[2].name, s.lanes[5].name, s.lanes[6].name], ["lane8", "lane11", "lane12"])


class AFCObjectTest(unittest.TestCase):
    def test_multiple_mapping(self):
        s = parsed()
        self.assertFalse(s.multiple_mapping)
        self.assertEqual(p.afc_object_query()["AFC"], ["multiple_tool_mapping", "message"])
        p.merge_afc_object({"eventtime": 1.0, "status": {"AFC": {"multiple_tool_mapping": True}}}, s)
        self.assertTrue(s.multiple_mapping)
        self.assertEqual(len(s.lanes), 4)
        # An answer without the field (older AFC) or without AFC leaves it alone.
        p.merge_afc_object({"status": {"AFC": {}}}, s)
        self.assertTrue(s.multiple_mapping)
        p.merge_afc_object([], s)
        self.assertTrue(s.multiple_mapping)
        p.merge_afc_object({"AFC": {"multiple_tool_mapping": False}}, s)
        self.assertFalse(s.multiple_mapping)

    def test_message(self):
        s = parsed()
        self.assertEqual((s.message, s.message_type), ("", ""))
        p.merge_afc_object({"status": {"AFC": {"message": {"message": " lane2 failed to load\n", "type": "error"}}}}, s)
        self.assertEqual((s.message, s.message_type), ("lane2 failed to load", "error"))
        # An older AFC without the field leaves it; AFC_CLEAR_MESSAGE empties it.
        p.merge_afc_object({"status": {"AFC": {"multiple_tool_mapping": True}}}, s)
        self.assertEqual(s.message, "lane2 failed to load")
        p.merge_afc_object({"status": {"AFC": {"message": {"message": "", "type": ""}}}}, s)
        self.assertEqual((s.message, s.message_type), ("", ""))

    def test_u1_print_options(self):
        s = parsed()
        self.assertFalse(s.u1_print.present)
        self.assertIsNone(p.to_json(s)["u1_print"])
        self.assertEqual(p.afc_object_query()["print_task_config"],
                         ["auto_bed_leveling", "flow_calibrate", "time_lapse_camera"])
        # Another Klipper printer has no print_task_config: nothing changes.
        p.merge_afc_object({"status": {"AFC": {"multiple_tool_mapping": True}}}, s)
        self.assertFalse(s.u1_print.present)
        # Klipper answers the query on such a printer with null fields: still not a U1.
        p.merge_afc_object({"status": {"AFC": {}, "print_task_config": {
            "auto_bed_leveling": None, "flow_calibrate": None, "time_lapse_camera": None}}}, s)
        self.assertFalse(s.u1_print.present)
        p.merge_afc_object({"status": {"AFC": {}, "print_task_config": {
            "auto_bed_leveling": True, "flow_calibrate": False, "time_lapse_camera": True}}}, s)
        self.assertEqual(s.u1_print, U1PrintOptions(True, True, False, True))
        self.assertEqual(p.to_json(s)["u1_print"],
                         {"bed_level": True, "flow_calibrate": False, "time_lapse_camera": True})

    def test_u1_top_cover(self):
        s = parsed()
        self.assertEqual(p.afc_object_query()["purifier"], ["power_detected"])
        p.merge_afc_object({"status": {"AFC": {}, "purifier": {"power_detected": None}}}, s)
        self.assertFalse(s.top_cover)  # no purifier on this printer
        p.merge_afc_object({"status": {"AFC": {}, "purifier": {"power_detected": True}}}, s)
        self.assertTrue(s.top_cover)
        p.merge_afc_object({"status": {"AFC": {}, "purifier": {"power_detected": False}}}, s)
        self.assertFalse(s.top_cover)  # the cover came off

        options = U1PrintOptions(flow_calibrate=True)
        self.assertEqual(p.u1_print_options_gcode(options),
                         "SET_PRINT_PREFERENCE KEY=bed_level VALUE=0\n"
                         "SET_PRINT_PREFERENCE KEY=flow_calibrate VALUE=1\n"
                         "SET_PRINT_PREFERENCE KEY=time_lapse_camera VALUE=0")
        self.assertEqual(p.u1_file_print_options_gcode(options),
                         "SET_PRINT_PREFERENCES BED_LEVEL=0 FLOW_CALIBRATE=1 TIME_LAPSE_CAMERA=0 FORCE=1")
        options.bed_level = options.time_lapse = True
        self.assertEqual(p.u1_file_print_options_gcode(options),
                         "SET_PRINT_PREFERENCES BED_LEVEL=1 FLOW_CALIBRATE=1 TIME_LAPSE_CAMERA=1 FORCE=1")

    def test_print_options_from_page(self):
        self.assertEqual(U1PrintOptions.from_page({"bed_level": True, "time_lapse_camera": 1}),
                         U1PrintOptions(True, True, False, True))
        self.assertFalse(U1PrintOptions.from_page("x").present)

    def test_objects_fallback(self):
        listing = {"objects": ["toolhead", "AFC", "AFC_BoxTurtle Turtle_1", "AFC_stepper lane1", "AFC_lane lane2",
                               "AFC_hub Turtle_1", "AFC_extruder extruder", "AFC_stepper lane9"]}
        self.assertEqual(p.lane_object_names(listing, ["lane1", "lane2"]), ["AFC_stepper lane1", "AFC_lane lane2"])
        status = {
            "AFC": {"units": ["Box_Turtle Turtle_1"], "lanes": ["lane1", "lane2"], "current_load": None,
                    "spoolman": None, "multiple_tool_mapping": True, "extruders": ["extruder"]},
            "AFC_stepper lane1": lane_status("lane1", "Turtle_1", 1, ["T0", "T7"], "extruder", "#123456", "ASA", True),
            "AFC_lane lane2": lane_status("lane2", "Turtle_1", 2, ["T1"], "extruder", "#654321", "PLA", False),
        }
        s = p.parse_objects_status({"status": status})
        self.assertFalse(s.spoolman)
        self.assertTrue(s.multiple_mapping)
        self.assertEqual(len(s.units), 1)
        self.assertEqual(s.units[0].type, "Box_Turtle")
        self.assertEqual(s.units[0].lanes, ["lane1", "lane2"])
        self.assertEqual(s.find_lane("lane1").unit_type, "Box_Turtle")
        self.assertEqual(s.max_tool(), 7)
        self.assertEqual(s.lane_for_tool(7).name, "lane1")
        self.assertIsNone(p.parse_objects_status({"status": {"toolhead": {}}}))

    def test_saved_map_string(self):
        status = {"AFC": {"units": ["Box_Turtle Turtle_1"], "lanes": ["lane1"]},
                  "AFC_stepper lane1": dict(lane_status("lane1", "Turtle_1", 1, [], "extruder", "", "", True),
                                            map="T0,T5")}
        self.assertEqual(p.parse_objects_status(status).find_lane("lane1").maps, ["T0", "T5"])


class LaneDataTest(unittest.TestCase):
    def test_fills_blanks_only(self):
        s = parsed()
        p.merge_lane_data({"namespace": "lane_data", "value": {
            "T5": {"vendor_name": "Bambu", "material": "PETG", "color": "#AABBCC", "extruder_index": 1},
            "T0": {"vendor_name": "Other", "material": "ABS"},
            "bogus": {"material": "X"}}}, s)
        l6 = s.find_lane("lane6")
        self.assertEqual((l6.vendor, l6.material, l6.color), ("Bambu", "PETG", "#AABBCC"))
        # The live status wins.
        self.assertEqual((s.find_lane("lane1").vendor, s.find_lane("lane1").material), ("Polymaker", "PLA"))

    def test_keyed_by_lane_name(self):
        s = parsed()
        self.assertEqual(s.find_lane("lane6").vendor, "")
        p.merge_lane_data({"namespace": "lane_data", "value": {
            "lane6": {"vendor_name": "Bambu", "material": "PETG", "lane": "5", "spool_id": 12}}}, s)
        l6 = s.find_lane("lane6")
        self.assertEqual((l6.vendor, l6.material, l6.spool_id), ("Bambu", "PETG", 12))

    def test_junk(self):
        s = parsed()
        before = copy.deepcopy(s)
        p.merge_lane_data(None, s)
        p.merge_lane_data({"value": []}, s)
        self.assertEqual(s, before)


class GcodeTest(unittest.TestCase):
    def test_lane_edit(self):
        s = parsed()
        g = p.build_lane_edit_gcode(s, "lane2", {"color": "#a1b2c3", "material": "PETG", "weight": 812.5,
                                                 "spool_id": 0, "nozzle_temp": 240})
        # Spoolman id first so the explicit values are not overwritten by Spoolman's.
        self.assertEqual(g, ["SET_SPOOL_ID LANE=lane2 SPOOL_ID=", "SET_MATERIAL LANE=lane2 MATERIAL=PETG",
                             "SET_COLOR LANE=lane2 COLOR=A1B2C3", "SET_WEIGHT LANE=lane2 WEIGHT=812.5",
                             "AFC_SET_SPOOL_TEMP LANE=lane2 EXTRUDER_TEMP=240"])
        self.assertEqual(p.build_lane_edit_gcode(s, "lane2", {"weight": 40, "bed_temp": 65.5, "map": "t3",
                                                              "spool_id": 7}),
                         ["SET_SPOOL_ID LANE=lane2 SPOOL_ID=7", "SET_MAP LANE=lane2 MAP=T3",
                          "SET_WEIGHT LANE=lane2 WEIGHT=40", "AFC_SET_SPOOL_TEMP LANE=lane2 BED_TEMP=66"])

    def test_lane_edit_rejects(self):
        s = parsed()
        for lane, changes in (("lane2", {"material": "PLA\nM112"}), ("lane2", {"material": "PLA ;x"}),
                              ("lane2 X=1", {"weight": 1}), ("lane42", {"weight": 1}),
                              ("lane2", {"color": "red"}), ("lane2", {"map": "T5 M112"}),
                              ("lane2", {"run": "M112"}), ("lane2", {}), ("lane2", {"weight": True}),
                              ("lane2", {"spool_id": 1.5}), ("lane2", {"nozzle_temp": 900})):
            with self.subTest(lane=lane, changes=changes), self.assertRaises(AFCError):
                p.build_lane_edit_gcode(s, lane, changes)

    def test_tool_mapping_replays_swaps(self):
        s = parsed()
        # T0 already on lane1: skipped. T1 -> lane5 swaps (lane2 takes T4). T4 -> lane2 is then in place.
        self.assertEqual(p.build_tool_mapping_gcode(s, [(0, "lane1"), (1, "lane5"), (4, "lane2")]),
                         ["SET_MAP LANE=lane5 MAP=T1"])
        for pairs in ([(0, "lane5"), (1, "lane5")], [(9, "lane5")], [(0, "nope")], [(1, "lane1"), (1, "lane2")]):
            with self.subTest(pairs=pairs), self.assertRaises(AFCError):
                p.build_tool_mapping_gcode(s, pairs)

    def test_tool_mapping_multiple(self):
        s = parsed()
        s.multiple_mapping = True
        self.assertEqual(p.build_tool_mapping_gcode(s, [(0, "lane5"), (1, "lane5")]),
                         ["SET_MAP LANE=lane5 MAP=T0", "SET_MAP LANE=lane5 MAP=T1"])

    def test_lane_actions(self):
        s = parsed()
        self.assertEqual(p.build_lane_action_gcode(s, "lane1", "load"), "CHANGE_TOOL LANE=lane1")
        self.assertEqual(p.build_lane_action_gcode(s, "lane1", "eject"), "LANE_UNLOAD LANE=lane1")
        s.find_lane("lane2").tool_loaded = True
        self.assertEqual(p.build_lane_action_gcode(s, "lane2", "unload"), "TOOL_UNLOAD LANE=lane2")
        for lane, action in (("lane6", "load"), ("lane1", "unload"), ("lane1", "purge"), ("lane1;M112", "load"),
                             ("lane2", "eject")):
            with self.subTest(lane=lane, action=action), self.assertRaises(AFCError):
                p.build_lane_action_gcode(s, lane, action)

    def test_helpers(self):
        self.assertEqual(p.tool_number("T12"), 12)
        self.assertEqual(p.tool_number("T"), -1)
        self.assertEqual(p.tool_number("lane1"), -1)
        self.assertEqual(p.extruder_index_from_name("extruder3"), 3)
        self.assertEqual(p.extruder_index_from_name("extruder_stepper x"), -1)
        self.assertEqual(p.extruder_index_from_name("extruder"), 0)
        self.assertEqual(p.extruder_index_from_name("e0"), 0)
        self.assertEqual(p.extruder_index_from_name("e3"), 3)
        self.assertEqual(p.extruder_index_from_name("e"), -1)
        self.assertEqual(p.extruder_index_from_name("ebb"), -1)
        self.assertEqual(p.normalize_color("ff00aaFF"), "#FF00AA")
        self.assertEqual(p.normalize_color("#12345"), "")
        self.assertEqual(p._round(2.5), 3)
        self.assertEqual(p._round(-2.5), -3)
        self.assertEqual(p.spoolman_page("http://s:7912/", 12), "http://s:7912/spool/show/12")
        self.assertEqual(p.spoolman_page("http://s:7912", 0), "http://s:7912/spool")

    def test_spoolman_for_printer(self):
        self.assertEqual(p.spoolman_for_printer("http://localhost:7912", "http://192.168.1.20:7125"), "http://192.168.1.20:7912")
        self.assertEqual(p.spoolman_for_printer("http://127.0.0.1:7912/", "https://voron.local"), "http://voron.local:7912/")
        self.assertEqual(p.spoolman_for_printer("http://[::1]:7912", "http://[fd00::5]:7125"), "http://[fd00::5]:7912")
        self.assertEqual(p.spoolman_for_printer("http://spoolman:7912", "http://192.168.1.20"), "http://spoolman:7912")
        self.assertEqual(p.spoolman_for_printer("http://localhost:7912", ""), "http://localhost:7912")
        self.assertEqual(p.spoolman_for_printer("", "http://192.168.1.20"), "")


class DryerTest(unittest.TestCase):
    def test_read_from_unit_objects(self):
        afc = dryer_units()
        afc["Bambu_AMS_HT_1"] = {"system": {"type": "BambuAMS"},
                                 "lane20": lane_status("lane20", "Bambu_AMS_HT_1", 1, ["T20"], "extruder", "", "PA",
                                                       True)}
        afc["Bambu_AMS_2"] = {"system": {"type": "BambuAMS"}}  # no lanes: not queried
        s = parsed({"status:": {"AFC": afc}})
        q = p.dryer_objects_query(s)
        self.assertEqual(set(q), {"AFC_ACE2 Ace2_1", "AFC_BambuAMS Bambu_AMS_1", "AFC_BambuAMS Bambu_AMS_HT_1"})

        p.merge_dryer_status({"status": {
            "AFC_ACE2 Ace2_1": {"ace_connected": True, "ace_temp": 41.5, "ace_humidity": 18, "ace_dryer": "keeping",
                                "ace_dryer_target": 55, "ace_dryer_remain": 5400, "ace_dryer_duration": 14400,
                                "ace_dry_rotate": False},
            "AFC_BambuAMS Bambu_AMS_1": {"has_heater": True, "dry_max_temp": 65, "drying": False,
                                         "temperature": 27.0, "humidity": 31.0, "ams_model": "ams2",
                                         "bridge_online": True, "dry_error": ""},
            "AFC_BambuAMS Bambu_AMS_HT_1": {"has_heater": True, "dry_max_temp": 85, "drying": True,
                                            "dry_target": 80, "dry_remaining": 3600, "dry_minutes": 720,
                                            "ams_model": "ht", "dry_note": "spools turning", "dry_rotate": True},
        }}, s)
        ace = s.find_unit("Ace2_1").dryer
        self.assertEqual((ace.kind, ace.drying, ace.max_temp, ace.duration_s, ace.remaining_s),
                         ("ace", True, 70, 14400, 5400))
        ams = s.find_unit("Bambu_AMS_1").dryer
        self.assertEqual((ams.drying, ams.max_temp), (False, 65))
        ht = s.find_unit("Bambu_AMS_HT_1").dryer
        self.assertEqual((ht.drying, ht.duration_s, ht.note), (True, 720 * 60, "spools turning"))
        self.assertIsNone(s.find_unit("Turtle_1").dryer)

        self.assertEqual((s.find_unit("Bambu_AMS_1").model, s.find_unit("Ace2_1").model), ("ams2", ""))

        # A Bambu AMS without a heater (AMS 1, AMS Lite) has no dryer: the row is skipped, not cleared.
        p.merge_dryer_status({"AFC_BambuAMS Bambu_AMS_1": {"has_heater": False, "ams_model": "lite"}}, s)
        self.assertIsNotNone(s.find_unit("Bambu_AMS_1").dryer)
        self.assertEqual(s.find_unit("Bambu_AMS_1").model, "lite")  # its model is still read
        self.assertEqual(p.to_json(s)["units"][0]["dryer"]["kind"], "ace")

    def test_first_ace_minutes_and_cap(self):
        afc = {"Ace_1": {"system": {"type": "ACE"},
                         "lane1": lane_status("lane1", "Ace_1", 1, ["T0"], "extruder", "", "PLA", True)}}
        s = parsed({"status:": {"AFC": afc}})
        p.merge_dryer_status({"AFC_ACE Ace_1": {"ace_dryer": "drying", "ace_dryer_duration": 240}}, s)
        d = s.find_unit("Ace_1").dryer
        self.assertEqual((d.duration_s, d.max_temp), (240 * 60, 55))

    def test_dry_gcode(self):
        s = parsed({"status:": {"AFC": dryer_units()}})
        p.merge_dryer_status({"AFC_ACE2 Ace2_1": {"ace_dryer": "stop"},
                              "AFC_BambuAMS Bambu_AMS_1": {"has_heater": True, "dry_max_temp": 65}}, s)
        self.assertEqual(p.build_dry_gcode(s, "Ace2_1", {"action": "start", "temp": 55, "minutes": 240,
                                                         "rotate": True}),
                         "ACE_DRY UNIT=Ace2_1 TEMP=55 DURATION=240 ROTATE=1")
        self.assertEqual(p.build_dry_gcode(s, "Ace2_1", {"action": "stop"}), "ACE_DRY_STOP UNIT=Ace2_1")
        self.assertEqual(p.build_dry_gcode(s, "Bambu_AMS_1", {"action": "start", "temp": 65, "minutes": 480}),
                         "AFC_BAMBU_HEATER_START UNIT=Bambu_AMS_1 TEMP=65 TIME=480 ROTATE=0")
        self.assertEqual(p.build_dry_gcode(s, "Bambu_AMS_1", {"action": "stop"}),
                         "AFC_BAMBU_HEATER_STOP UNIT=Bambu_AMS_1")
        for unit, request in (("Bambu_AMS_1", {"action": "start", "temp": 80, "minutes": 480}),  # over 65
                              ("Ace2_1", {"action": "start", "temp": 55, "minutes": 0}),
                              ("Ace2_1", {"action": "start", "temp": "55;M112", "minutes": 60}),
                              ("Turtle_1", {"action": "start", "temp": 50, "minutes": 60}),  # no dryer
                              ("Ace2_1;M112", {"action": "stop"}),
                              ("Ace2_1", {"action": "boost"})):
            with self.subTest(unit=unit, request=request), self.assertRaises(AFCError):
                p.build_dry_gcode(s, unit, request)


class FlowKTest(unittest.TestCase):
    SPOOL = {"id": 12, "extra": {"flow_k": "0.032", "card_uids": "\"04A1B2\""}}

    def test_extra_field(self):
        # Spoolman JSON-encodes extra values, as AFC autocal writes them.
        self.assertAlmostEqual(p.spool_flow_k(self.SPOOL), 0.032)
        self.assertAlmostEqual(p.spool_flow_k({"response": self.SPOOL, "error": None}), 0.032)
        self.assertEqual(p.spool_flow_k({"response": None, "error": {"status_code": 404}}), -1)
        self.assertAlmostEqual(p.spool_flow_k({"extra": {"flow_k": 0.045}}), 0.045)

    def test_no_k(self):
        for spool in ({"id": 3, "extra": {}}, {"id": 3}, {"extra": {"flow_k": ""}},
                      {"extra": {"flow_k": "\"fast\""}}, {"extra": {"flow_k": "-0.1"}}, None,
                      {"extra": {"flow_k": "NaN"}}):
            with self.subTest(spool=spool):
                self.assertEqual(p.spool_flow_k(spool), -1)

    def test_page_gets_k_only_when_known(self):
        s = Snapshot(present=True, lanes=[Lane(name="lane1", flow_k=0.032), Lane(name="lane2")])
        j = p.to_json(s)
        self.assertAlmostEqual(j["lanes"][0]["flow_k"], 0.032)
        self.assertIsNone(j["lanes"][1]["flow_k"])


class PageStateTest(unittest.TestCase):
    def test_to_json(self):
        j = p.to_json(parsed())
        self.assertTrue(j["present"])
        self.assertEqual(j["max_tool"], 5)
        self.assertEqual([lane["name"] for lane in j["lanes"]], ["lane1", "lane2", "lane5", "lane6"])
        self.assertEqual(j["units"][0], {"name": "Turtle_1", "type": "Box_Turtle", "lanes": ["lane1", "lane2"],
                                         "dryer": None})
        self.assertIs(j["lanes"][3]["has_filament"], False)
        self.assertEqual(p.to_json(Snapshot())["max_tool"], -1)


if __name__ == "__main__":
    unittest.main()
