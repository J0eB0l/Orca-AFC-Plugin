"""The "AFC G-code" step on an exported file, against a fake Moonraker."""

import json
import math
import os
import tempfile
import unittest

import fake_orca
from fake_moonraker import FakeMoonraker, closed_port_url
from orca_afc import postprocess
from orca_afc.postprocess import AFCGcode
from test_protocol import webhook_fixture

orca = fake_orca.orca

CONFIG = ("; CONFIG_BLOCK_START\n"
          "; filament_colour = #FF0000;#00FF00\n"
          "; filament_diameter = 1.75,1.75\n"
          "; flush_volumes_matrix = 0,100,110,0\n"
          "; gcode_flavor = %s\n"
          "; nozzle_diameter = 0.4\n"
          "; single_extruder_multi_material = 0\n"
          "; CONFIG_BLOCK_END\n")
BODY = "PRINT_START TOOL=0\nT0\nG1 X10 E1\nT1\nG1 X20 E1\n"
PURGE = "%.1f" % (100 / (math.pi / 4 * 1.75 ** 2))


class PostProcessTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMoonraker()
        self.addCleanup(self.fake.close)
        self.fake.webhook = webhook_fixture()      # T0 and T1 on toolhead 0, both spool 12
        self.fake.spools = {12: {"id": 12, "extra": {"flow_k": "0.032"}}}
        orca.bundle = fake_orca.PresetBundle({"host_type": "moonraker", "print_host": self.fake.url})
        self.addCleanup(setattr, orca, "bundle", fake_orca.PresetBundle())
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "plate_1.gcode")

    def write(self, text):
        with open(self.path, "w", newline="") as f:
            f.write(text)

    def read(self):
        with open(self.path, newline="") as f:
            return f.read()

    def test_lane_change_purge_and_spool_k(self):
        self.write(BODY + CONFIG % "klipper")
        message = postprocess.process_file(self.path, {}, lambda key: None)
        lines = self.read().splitlines()
        self.assertEqual(lines[1], "T0")
        self.assertEqual(lines[2], "SET_PRESSURE_ADVANCE ADVANCE=0.032 ; AFC spool flow K")
        self.assertEqual(lines[4], "T1 PURGE_LENGTH=" + PURGE)
        self.assertEqual(message, "AFC G-code: 2 tool changes, 1 lane-change purges, 2 spool PA settings")

    def test_printer_not_answering(self):
        text = BODY + CONFIG % "klipper"
        self.write(text)
        message = postprocess.process_file(self.path, {"printer_url": closed_port_url()}, None, timeout=10)
        # Nothing is known about the printer, so the file stays as it was sliced.
        self.assertEqual(self.read(), text)
        self.assertIn("left as it is, without AFC", message)

    def test_printer_without_afc(self):
        text = BODY + CONFIG % "klipper"
        self.write(text)
        self.fake.webhook = None
        message = postprocess.process_file(self.path, {}, lambda key: None)
        self.assertEqual(self.read(), text)
        self.assertIn("left as it is, without AFC", message)

    def test_no_print_host(self):
        text = BODY + CONFIG % "klipper"
        self.write(text)
        orca.bundle = fake_orca.PresetBundle()
        self.assertIn("left as it is, without AFC", postprocess.process_file(self.path, {}, lambda key: None))
        self.assertEqual(self.read(), text)

    def test_afc_box_ticked_purges_while_the_printer_is_off(self):
        self.write(BODY + CONFIG.replace("; flush", "; afc_enabled = 1\n; flush") % "klipper")
        message = postprocess.process_file(self.path, {"printer_url": closed_port_url()}, None, timeout=10)
        # The box says AFC does the lane changes, so they get its purges without the printer.
        self.assertEqual(self.read().splitlines()[3], "T1 PURGE_LENGTH=" + PURGE)
        self.assertIn("1 lane-change purges", message)
        self.assertIn("(without AFC:", message)

    def test_afc_box_off_leaves_the_file_without_asking(self):
        text = BODY + CONFIG.replace("; flush", "; afc_enabled = 0\n; flush") % "klipper"
        self.write(text)
        self.assertIn("AFC box is off", postprocess.process_file(self.path, {}, lambda key: None))
        self.assertEqual(self.read(), text)
        self.assertEqual(self.fake.requests, [])

    def test_other_flavors_untouched(self):
        text = BODY + CONFIG % "marlin2"
        self.write(text)
        self.assertIn("not a Klipper file", postprocess.process_file(self.path, {}, None))
        self.assertEqual(self.read(), text)
        self.assertEqual(self.fake.requests, [])

    def test_slice_settings_win(self):
        other = FakeMoonraker()
        self.addCleanup(other.close)
        self.write(BODY + CONFIG % "klipper")
        postprocess.process_file(self.path, {}, {"print_host": other.url, "host_type": "moonraker"}.get)
        self.assertTrue(other.requests)
        self.assertEqual(self.fake.requests, [])


class CapabilityTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeMoonraker()
        self.addCleanup(self.fake.close)
        self.fake.webhook = webhook_fixture()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "plate_1.gcode")
        with open(self.path, "w") as f:
            f.write(BODY + CONFIG % "klipper")
        self.step = AFCGcode()
        self.step.save_config(json.dumps({"printer_url": self.fake.url}))

    def test_post_process_step(self):
        self.assertEqual(self.step.get_name(), "AFC G-code")
        result = self.step.execute(fake_orca.SlicingContext(self.path))
        self.assertEqual(result.status, orca.PluginResult.Success)
        self.assertIn("1 lane-change purges", result.message)
        with open(self.path) as f:
            self.assertIn("PURGE_LENGTH", f.read())

    def test_geometry_steps_pass(self):
        result = self.step.execute(fake_orca.SlicingContext(self.path, step=orca.slicing.Step.posSlice))
        self.assertEqual((result.status, result.message), (orca.PluginResult.Success, ""))
        self.assertEqual(self.fake.requests, [])

    def test_unwritable_file_fails_the_export(self):
        result = self.step.execute(fake_orca.SlicingContext(self.path + ".missing"))
        self.assertEqual(result.status, orca.PluginResult.FatalError)


if __name__ == "__main__":
    unittest.main()
