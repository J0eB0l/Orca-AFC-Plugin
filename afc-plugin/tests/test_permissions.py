"""What the plugin asks OrcaSlicer for while it loads."""

import json
import tempfile
import unittest
from pathlib import Path

import fake_orca  # noqa: F401  (puts afc-plugin/ on sys.path)
from orca_afc import permissions


class PermissionsTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)

    def preset(self, user, name, **values):
        path = self.data / "user" / user / "machine" / (name + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(values, name=name)), encoding="utf-8")

    def test_afc_printer_hosts(self):
        self.preset("default", "U1 - Joe", printer_agent="afc", print_host="192.168.4.36", inherits="Snapmaker U1")
        self.preset("1234", "SV08", printer_agent="afc", print_host="https://voron.local:7125/")
        self.preset("1234", "SV08 0.6", inherits="SV08")  # an AFC printer through the preset it inherits
        self.preset("1234", "Other", printer_agent="moonraker", print_host="10.0.0.9")
        self.preset("1234", "No host", printer_agent="afc")
        (self.data / "user" / "1234" / "machine" / "broken.json").write_text("{", encoding="utf-8")
        self.assertEqual(sorted(permissions.printer_hosts(self.data)), ["http://192.168.4.36", "https://voron.local"])

    def test_declares_threads_github_and_printers(self):
        self.preset("default", "U1", printer_agent="afc", print_host="192.168.4.36")
        asked = []
        permissions.declare(lambda **kw: asked.append(kw), self.data)
        self.assertEqual(asked, [{"threading": True, "network": [permissions.GITHUB, "http://192.168.4.36"]}])

    def test_older_orcaslicer_has_no_request(self):
        permissions.declare(None, self.data)  # nothing to call, nothing raised

    def test_data_dir_from_the_extracted_plugin(self):
        module = self.data / "orca_plugins" / "orca_afc.whl" / "__whl_extracted__" / "orca_afc" / "orca_afc" / "__init__.py"
        self.assertEqual(permissions.data_dir(str(module)), self.data.resolve())
        self.assertIsNone(permissions.data_dir(str(self.data / "src" / "orca_afc" / "__init__.py")))


if __name__ == "__main__":
    unittest.main()
