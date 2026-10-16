"""The AFC tab as OrcaSlicer drives it: the page HTML, the printer address
and the page's messages through the worker thread."""

import json
import unittest
from unittest import mock

import fake_orca
from fake_moonraker import FakeMoonraker
from orca_afc import page as afc_page
from orca_afc.page import AFCPage, page_html, printer_connection
from orca_afc.postprocess import AFCGcode
from test_protocol import webhook_fixture

orca = fake_orca.orca


class PageHtmlTest(unittest.TestCase):
    def test_assets_inline(self):
        html = page_html("panel")
        self.assertNotIn('href="afc.css"', html)
        self.assertNotIn('src="afc-mock.js"', html)
        self.assertNotIn('src="afc.js"', html)
        self.assertNotIn('src="afc-icons.js"', html)
        self.assertLess(html.index("window.AFC_ICONS"), html.index("orca.onMessage"))
        self.assertIn("<style>", html)
        self.assertIn('window.AFC_VIEW = "panel"; window.AFC_MODE = "";', html)
        self.assertIn("orca.onMessage", html)
        self.assertLess(html.index("window.AFC_VIEW"), html.index("orca.onMessage"))

    def test_popup_mode(self):
        self.assertIn('window.AFC_VIEW = "print"; window.AFC_MODE = "upload";', page_html("print", "upload"))


class PrinterConnectionTest(unittest.TestCase):
    def connection(self, printer, config=None):
        return printer_connection(config or {}, fake_orca.PresetBundle(printer))

    def test_preset(self):
        self.assertEqual(self.connection({"host_type": "moonraker", "print_host": "192.168.1.20:7125",
                                          "printhost_apikey": "k"}), ("http://192.168.1.20:7125", "k", ""))
        # The U1 profile connects as OctoPrint; Moonraker answers it.
        self.assertEqual(self.connection({"host_type": "octoprint", "print_host": "http://u1.local"})[0],
                         "http://u1.local")

    def test_config_wins(self):
        self.assertEqual(self.connection(None, {"printer_url": "10.0.0.5", "api_key": "x"}),
                         ("http://10.0.0.5", "x", ""))

    def test_hints(self):
        self.assertEqual(self.connection(None), ("", "", "OrcaSlicer has no printer selected."))
        url, _, hint = self.connection({"host_type": "prusalink", "print_host": "1.2.3.4"})
        self.assertEqual(url, "")
        self.assertIn("connects as prusalink", hint)
        url, _, hint = self.connection({"host_type": "moonraker", "print_host": ""})
        self.assertEqual(url, "")
        self.assertIn("printer_url", hint)


class AFCPageTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMoonraker()
        self.addCleanup(self.fake.close)
        self.fake.webhook = webhook_fixture()
        self.fake.objects = {"AFC": {}, "print_stats": {"state": "standby"}}
        orca.bundle = fake_orca.PresetBundle({"host_type": "moonraker", "print_host": self.fake.url})
        self.page = AFCPage()
        self.addCleanup(self.page.on_unload)

    def ask(self, cmd, params=None, afc_id=1):
        before = len(self.page.posted)
        message = {"afc_id": afc_id, "cmd": cmd}
        if params is not None:
            message["params"] = params
        self.page.on_message(message)
        answer = self.page.wait_posted(before + 1)[before]
        self.assertEqual(answer["afc_id"], afc_id)
        return answer

    def test_tab(self):
        self.assertEqual(self.page.get_name(), "AFC")
        self.assertTrue(self.page.get_icon().endswith("afc.svg"))
        self.assertIn("<style>", self.page.get_ui())
        self.assertEqual(self.page.get_default_config(), {"printer_url": "", "api_key": ""})

    def test_get_state(self):
        answer = self.ask("get_state", afc_id=7)
        self.assertTrue(answer["ok"])
        self.assertTrue(answer["data"]["connected"])
        self.assertEqual(len(answer["data"]["afc"]["lanes"]), 4)

    def test_lane_action(self):
        self.ask("get_state")
        answer = self.ask("lane_action", {"lane": "lane1", "action": "load"}, afc_id=2)
        self.assertTrue(answer["ok"])
        self.assertNotIn("error", answer["data"])
        self.assertEqual(self.fake.scripts, ["CHANGE_TOOL LANE=lane1"])

    def test_command_error_comes_back_with_the_state(self):
        self.ask("get_state")
        self.fake.objects["print_stats"]["state"] = "printing"
        answer = self.ask("lane_action", {"lane": "lane1", "action": "load"})
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["data"]["error"], "The printer is printing; lane moves wait until it is idle")
        self.assertTrue(answer["data"]["afc"]["present"])

    def test_refused(self):
        self.ask("get_state")
        answer = self.ask("edit_lane", {"lane": "lane2", "changes": {"material": "PLA\nM112"}})
        self.assertFalse(answer["ok"])
        self.assertIn("material", answer["message"])
        self.assertEqual(self.ask("reboot")["message"], "unknown command reboot")
        self.assertEqual(self.fake.scripts, [])

    def test_no_printer(self):
        orca.bundle = fake_orca.PresetBundle(None)
        answer = self.ask("get_state")
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["data"], {"connected": False, "afc": answer["data"]["afc"],
                                          "hint": "OrcaSlicer has no printer selected."})
        self.assertFalse(answer["data"]["afc"]["present"])
        answer = self.ask("edit_lane", {"lane": "lane2", "changes": {"weight": 1}})
        self.assertEqual((answer["ok"], answer["message"]), (False, "OrcaSlicer has no printer selected."))

    def test_orca_closing(self):
        orca.bundle = RuntimeError("OrcaSlicer is closing")
        answer = self.ask("get_state")
        self.assertEqual((answer["data"]["connected"], answer["data"]["hint"]), (False, "OrcaSlicer is closing"))

    def test_config_address(self):
        orca.bundle = fake_orca.PresetBundle(None)
        self.page.save_config(json.dumps({"printer_url": self.fake.url}))
        self.assertTrue(self.ask("get_state")["data"]["afc"]["present"])

    def test_open_spoolman(self):
        self.ask("get_state")
        with mock.patch.object(afc_page.webbrowser, "open") as browser:
            self.assertTrue(self.ask("open_spoolman", {"spool_id": 12})["ok"])
            self.assertTrue(self.ask("open_spoolman", {"spool_id": "12;x"})["ok"])
        self.assertEqual([c.args[0] for c in browser.call_args_list],
                         ["http://spoolman:7912/spool/show/12", "http://spoolman:7912/spool"])

    def test_spoolman_on_the_printer(self):
        # AFC reports Spoolman as the printer sees it; from this computer that is the printer's address.
        self.fake.webhook["status:"]["AFC"]["system"]["spoolman"] = "http://localhost:7912"
        self.ask("get_state")
        with mock.patch.object(afc_page.webbrowser, "open") as browser:
            self.assertTrue(self.ask("open_spoolman", {"spool_id": 12})["ok"])
        self.assertEqual(browser.call_args.args[0], "http://127.0.0.1:7912/spool/show/12")

    def test_answers_in_order(self):
        for i in range(5):
            self.page.on_message({"afc_id": i, "cmd": "get_state", "params": {"refresh": i == 0}})
        self.assertEqual([m["afc_id"] for m in self.page.wait_posted(5)], [0, 1, 2, 3, 4])
        self.assertEqual(self.fake.paths().count("/printer/afc/status"), 1)

    def test_ignores_other_messages(self):
        self.page.on_message("hello")
        self.page.on_message({"afc_id": 1})
        self.assertEqual(self.page.posted, [])

    def test_unload_stops_the_worker(self):
        self.ask("get_state")
        thread = self.page.worker._thread
        self.page.on_unload()
        thread.join(5)
        self.assertFalse(thread.is_alive())


class PluginEntryTest(unittest.TestCase):
    def test_registers_the_tab_and_the_gcode_step(self):
        import orca_afc
        self.assertIn(orca_afc.AFCPlugin, orca.plugins)
        orca.capabilities.clear()
        orca_afc.AFCPlugin().register_capabilities()
        self.assertEqual(orca.capabilities, [AFCPage, AFCGcode])


if __name__ == "__main__":
    unittest.main()
