"""Reading AFC and running the page's commands against a fake Moonraker."""

import time
import unittest
from unittest import mock

import fake_orca  # noqa: F401
from fake_moonraker import FakeMoonraker, closed_port_url
from orca_afc.manager import FLOW_K_MAX_AGE, AFCManager
from orca_afc.moonraker import Moonraker, base_url
from orca_afc.protocol import AFCError
from test_protocol import lane_status, webhook_fixture

SPOOL_12 = {"id": 12, "extra": {"flow_k": "0.032"}}


class Case(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMoonraker()
        self.addCleanup(self.fake.close)
        self.fake.webhook = webhook_fixture()
        self.fake.objects = {"AFC": {"multiple_tool_mapping": True}, "print_stats": {"state": "standby"}}
        self.now = 1000.0
        self.manager = AFCManager(clock=lambda: self.now)
        self.client = Moonraker(self.fake.url, timeout=2)

    def refreshed(self):
        self.assertEqual(self.manager.refresh(self.client), "")
        return self.manager.state(self.client.url)


class ReadTest(Case):
    def test_webhook(self):
        self.fake.lane_data = {"T5": {"vendor_name": "Bambu", "material": "PETG"}}
        state = self.refreshed()
        self.assertTrue(state["connected"])
        self.assertNotIn("error", state)
        afc = state["afc"]
        self.assertTrue(afc["present"])
        self.assertTrue(afc["multiple_mapping"])  # from the AFC object, not the webhook
        self.assertIsNone(afc["u1_print"])        # print_task_config answered with nulls
        self.assertEqual([lane["name"] for lane in afc["lanes"]], ["lane1", "lane2", "lane5", "lane6"])
        self.assertEqual(afc["lanes"][3]["vendor"], "Bambu")
        self.assertIn("/printer/afc/status", self.fake.paths())

    def test_u1(self):
        self.fake.objects["print_task_config"] = {"auto_bed_leveling": True, "flow_calibrate": False,
                                                  "time_lapse_camera": False}
        self.assertEqual(self.refreshed()["afc"]["u1_print"],
                         {"bed_level": True, "flow_calibrate": False, "time_lapse_camera": False})

    def test_objects_fallback(self):
        self.fake.webhook = None
        self.fake.objects = {
            "AFC": {"units": ["Box_Turtle Turtle_1"], "lanes": ["lane1", "lane2"], "spoolman": None,
                    "multiple_tool_mapping": False, "extruders": ["extruder"]},
            "AFC_BoxTurtle Turtle_1": {},
            "AFC_stepper lane1": lane_status("lane1", "Turtle_1", 1, ["T0"], "extruder", "#123456", "ASA", True),
            "AFC_lane lane2": lane_status("lane2", "Turtle_1", 2, ["T1"], "extruder", "#654321", "PLA", False),
        }
        afc = self.refreshed()["afc"]
        self.assertTrue(afc["present"])
        self.assertEqual([lane["name"] for lane in afc["lanes"]], ["lane1", "lane2"])
        self.assertEqual(afc["lanes"][0]["material"], "ASA")
        self.assertIn("/printer/objects/list", self.fake.paths())

    def test_no_afc(self):
        self.fake.webhook = None
        self.fake.objects = {"print_stats": {"state": "standby"}}
        state = self.refreshed()
        self.assertFalse(state["afc"]["present"])
        self.assertTrue(state["connected"])

    def test_unreachable(self):
        client = Moonraker(closed_port_url(), timeout=1)
        error = self.manager.refresh(client)
        self.assertTrue(error.startswith("Cannot reach"), error)
        self.assertFalse(self.manager.state(client.url, error=error)["afc"]["present"])

    def test_failed_read_keeps_last_snapshot(self):
        self.refreshed()
        self.fake.api_key = "secret"  # every call now answers 401
        error = self.manager.refresh(self.client)
        self.assertEqual(error, "Unauthorized")
        state = self.manager.state(self.client.url, error=error)
        self.assertTrue(state["afc"]["present"])
        self.assertEqual(state["error"], "Unauthorized")

    def test_other_printer(self):
        self.refreshed()
        # The tab moved to another printer: the old printer's lanes are not shown for it.
        self.assertFalse(self.manager.state("http://other:7125")["afc"]["present"])
        other = Moonraker(closed_port_url(), timeout=1)
        self.manager.refresh(other)
        self.assertFalse(self.manager.state(other.url)["afc"]["present"])
        self.assertFalse(self.manager.state(self.client.url)["afc"]["present"])

    def test_api_key(self):
        self.fake.api_key = "secret"
        self.client = Moonraker(self.fake.url, "secret", timeout=2)
        self.assertTrue(self.refreshed()["afc"]["present"])


class FlowKTest(Case):
    def proxy_calls(self):
        return self.fake.paths().count("/server/spoolman/proxy")

    def test_read_once_then_cached(self):
        self.fake.spools = {12: SPOOL_12}
        lanes = self.refreshed()["afc"]["lanes"]
        self.assertEqual([lane["flow_k"] for lane in lanes], [0.032, 0.032, 0.032, None])
        self.assertEqual(self.proxy_calls(), 1)  # three lanes share spool 12
        self.refreshed()
        self.assertEqual(self.proxy_calls(), 1)
        self.now += FLOW_K_MAX_AGE
        self.refreshed()
        self.assertEqual(self.proxy_calls(), 2)

    def test_spool_without_k(self):
        self.fake.spools = {12: {"id": 12, "extra": {}}}
        self.assertIsNone(self.refreshed()["afc"]["lanes"][0]["flow_k"])

    def test_spoolman_directly_when_moonraker_cannot(self):
        self.fake.proxy = False
        self.fake.spools = {12: SPOOL_12}
        self.fake.webhook["status:"]["AFC"]["system"]["spoolman"] = self.fake.url
        self.assertEqual(self.refreshed()["afc"]["lanes"][0]["flow_k"], 0.032)
        self.assertIn("/api/v1/spool/12", self.fake.paths())


class CommandTest(Case):
    def setUp(self):
        super().setUp()
        self.refreshed()

    def test_edit_lane(self):
        self.assertEqual(self.manager.edit_lane(self.client, {"lane": "lane2", "changes": {
            "material": "PETG", "weight": 40}}), "")
        self.assertEqual(self.fake.scripts, ["SET_MATERIAL LANE=lane2 MATERIAL=PETG\nSET_WEIGHT LANE=lane2 WEIGHT=40"])
        with self.assertRaises(AFCError):
            self.manager.edit_lane(self.client, {"lane": "lane2", "changes": {"material": "PLA\nM112"}})
        self.assertEqual(len(self.fake.scripts), 1)

    def test_klipper_error(self):
        self.fake.gcode_error = "Unknown command:\"SET_COLOR\""
        self.assertEqual(self.manager.edit_lane(self.client, {"lane": "lane2", "changes": {"color": "#FFFFFF"}}),
                         "Unknown command:\"SET_COLOR\"")

    def test_lane_action_idle(self):
        self.assertEqual(self.manager.lane_action(self.client, {"lane": "lane1", "action": "load"}), "")
        self.assertTrue(self.manager.wait_lane_move(5))
        self.assertEqual(self.fake.scripts, ["CHANGE_TOOL LANE=lane1"])

    def test_lane_action_does_not_wait_for_the_load(self):
        # Moonraker answers a load only when Klipper is done; the page must not wait for it.
        self.fake.gcode_delay = 1.0
        started = time.monotonic()
        self.assertEqual(self.manager.lane_action(self.client, {"lane": "lane1", "action": "load"}), "")
        self.assertLess(time.monotonic() - started, 0.9)
        self.assertEqual(self.manager.lane_moving(), "lane1 is loading")
        self.assertEqual(self.manager.lane_action(self.client, {"lane": "lane2", "action": "load"}),
                         "lane1 is loading; wait until it is done")
        self.assertEqual(self.manager.edit_lane(self.client, {"lane": "lane2", "changes": {"weight": 40}}),
                         "lane1 is loading; try again when it is done")
        # The page keeps reading AFC meanwhile.
        self.assertEqual(self.manager.refresh(self.client), "")
        self.assertTrue(self.manager.wait_lane_move(5))
        self.assertEqual(self.manager.lane_moving(), "")
        self.assertEqual(self.fake.scripts, ["CHANGE_TOOL LANE=lane1"])
        self.assertNotIn("error", self.manager.state(self.client.url))

    def test_lane_action_klipper_error_comes_once(self):
        self.fake.gcode_error = "AFC: lane1 did not reach the toolhead"
        self.assertEqual(self.manager.lane_action(self.client, {"lane": "lane1", "action": "load"}), "")
        self.assertTrue(self.manager.wait_lane_move(5))
        self.assertEqual(self.manager.state(self.client.url)["error"], "lane1: AFC: lane1 did not reach the toolhead")
        self.assertNotIn("error", self.manager.state(self.client.url))

    def test_lane_action_still_running_is_not_an_error(self):
        self.fake.gcode_delay = 1.0
        with mock.patch("orca_afc.manager.LANE_MOVE_TIMEOUT", 0.2):
            self.assertEqual(self.manager.lane_action(self.client, {"lane": "lane1", "action": "load"}), "")
            self.assertTrue(self.manager.wait_lane_move(5))
        self.assertNotIn("error", self.manager.state(self.client.url))

    def test_lane_action_printing(self):
        self.fake.objects["print_stats"]["state"] = "printing"
        self.assertEqual(self.manager.lane_action(self.client, {"lane": "lane1", "action": "load"}),
                         "The printer is printing; lane moves wait until it is idle")
        self.assertEqual(self.fake.scripts, [])

    def test_lane_action_state_unknown(self):
        del self.fake.objects["print_stats"]
        self.assertEqual(self.manager.lane_action(self.client, {"lane": "lane1", "action": "eject"}),
                         "Could not read the printer state, so nothing was moved")
        self.assertEqual(self.fake.scripts, [])

    def test_apply_mapping(self):
        self.assertEqual(self.manager.apply_mapping(self.client, {"mapping": [
            {"tool": 0, "lane": "lane1"}, {"tool": 1, "lane": "lane5"}]}), "")
        self.assertEqual(self.fake.scripts, ["SET_MAP LANE=lane5 MAP=T1"])

    def test_apply_mapping_in_place_sends_nothing(self):
        self.assertEqual(self.manager.apply_mapping(self.client, {"mapping": [{"tool": 0, "lane": "lane1"}]}), "")
        self.assertNotIn("/printer/gcode/script", self.fake.paths())

    def test_apply_mapping_upload_while_printing(self):
        self.fake.objects["print_stats"]["state"] = "paused"
        message = self.manager.apply_mapping(self.client, {"idle_only": True, "mapping": [{"tool": 1, "lane": "lane5"}]})
        self.assertTrue(message.startswith("The printer is paused, so AFC's lanes stay as they are"), message)
        self.assertEqual(self.fake.scripts, [])

    def test_apply_mapping_rejects(self):
        for params in ({}, {"mapping": [{"tool": "1", "lane": "lane5"}]}, {"mapping": [{"tool": 1}]},
                       {"mapping": [{"tool": 1, "lane": "lane5"}, {"tool": 1, "lane": "lane2"}]}):
            with self.subTest(params=params), self.assertRaises(AFCError):
                self.manager.apply_mapping(self.client, params)

    def test_print_options(self):
        with self.assertRaises(AFCError):
            self.manager.set_print_options(self.client, {"bed_level": True})
        self.fake.objects["print_task_config"] = {"auto_bed_leveling": False, "flow_calibrate": False,
                                                  "time_lapse_camera": False}
        self.refreshed()
        self.assertEqual(self.manager.set_print_options(self.client, {"bed_level": True, "time_lapse_camera": True}),
                         "")
        self.assertEqual(self.fake.scripts, ["SET_PRINT_PREFERENCE KEY=bed_level VALUE=1\n"
                                             "SET_PRINT_PREFERENCE KEY=flow_calibrate VALUE=0\n"
                                             "SET_PRINT_PREFERENCE KEY=time_lapse_camera VALUE=1"])

    def test_dry(self):
        self.fake.objects["AFC_BambuAMS Bambu_AMS_1"] = {"has_heater": True, "dry_max_temp": 65, "drying": False}
        dryer = self.refreshed()["afc"]["units"][1]["dryer"]
        self.assertEqual((dryer["kind"], dryer["max_temp"]), ("bambu", 65))
        self.assertEqual(self.manager.dry(self.client, {"unit": "Bambu_AMS_1", "action": "start", "temp": 60,
                                                        "minutes": 120, "rotate": True}), "")
        self.assertEqual(self.fake.scripts, ["AFC_BAMBU_HEATER_START UNIT=Bambu_AMS_1 TEMP=60 TIME=120 ROTATE=1"])


class BaseUrlTest(unittest.TestCase):
    def test_base_url(self):
        self.assertEqual(base_url("192.168.1.20"), "http://192.168.1.20")
        self.assertEqual(base_url(" printer.local:7125/ "), "http://printer.local:7125")
        self.assertEqual(base_url("https://x/"), "https://x")
        self.assertEqual(base_url(""), "")


if __name__ == "__main__":
    unittest.main()
