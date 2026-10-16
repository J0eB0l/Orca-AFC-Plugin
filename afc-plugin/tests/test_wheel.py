"""The install file: what OrcaSlicer's wheel reader asks of it, and that the
extracted copy imports and draws its page."""

import base64
import hashlib
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS.parent))
import build_wheel  # noqa: E402


class WheelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.wheel = build_wheel.build(Path(cls.tmp.name))
        cls.zip = zipfile.ZipFile(cls.wheel)
        cls.names = cls.zip.namelist()
        cls.dist_info = "orca_afc-%s.dist-info/" % build_wheel.version()

    @classmethod
    def tearDownClass(cls):
        cls.zip.close()
        cls.tmp.cleanup()

    def test_name_stays_the_same(self):
        self.assertEqual(self.wheel.name, "orca_afc.whl")

    def test_metadata(self):
        self.assertEqual(sorted({n.split("/")[0] for n in self.names if ".dist-info/" in n}),
                         [self.dist_info.rstrip("/")])
        meta = self.zip.read(self.dist_info + "METADATA").decode()
        self.assertIn("\nName: AFC\n", meta)
        self.assertIn("\nVersion: %s\n" % build_wheel.version(), meta)
        self.assertIn("\nImport-Name: orca_afc\n", meta)
        wheel = self.zip.read(self.dist_info + "WHEEL").decode()
        self.assertIn("Wheel-Version: 1.0", wheel)
        self.assertIn("Tag: py3-none-any", wheel)
        self.assertEqual(self.zip.read(self.dist_info + "top_level.txt").decode(), "orca_afc\n")

    def test_record(self):
        lines = self.zip.read(self.dist_info + "RECORD").decode().splitlines()
        listed = {}
        for line in lines:
            name, digest, size = line.split(",")
            listed[name] = (digest, size)
        self.assertEqual(set(listed), set(self.names))
        for name, (digest, size) in listed.items():
            if name.endswith("RECORD"):
                continue
            data = self.zip.read(name)
            expected = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            self.assertEqual((digest, size), ("sha256=" + expected, str(len(data))), name)

    def test_contents(self):
        for name in ("orca_afc/__init__.py", "orca_afc/page.py", "orca_afc/manager.py", "orca_afc/moonraker.py",
                     "orca_afc/protocol.py", "orca_afc/gcode.py", "orca_afc/postprocess.py", "orca_afc/worker.py",
                     "orca_afc/connection.py", "orca_afc/web/index.html", "orca_afc/web/afc.js", "orca_afc/web/afc-icons.js",
                     "orca_afc/web/afc.css", "orca_afc/web/afc.svg"):
            self.assertIn(name, self.names)
        self.assertNotIn("orca_afc/web/afc-mock.js", self.names)
        self.assertFalse([n for n in self.names if "__pycache__" in n or n.endswith(".pyc")])

    def test_extracted_copy_loads(self):
        # As OrcaSlicer does: extract, put the folder on sys.path, import the package.
        target = Path(self.tmp.name) / "__whl_extracted__" / "orca_afc"
        self.zip.extractall(target)
        script = ("import sys; sys.path.insert(0, %r); import fake_orca; sys.path.insert(0, %r)\n"
                  "sys.path.remove(%r)\n"
                  "import orca_afc; from orca_afc.page import AFCPage\n"
                  "assert orca_afc.__file__.startswith(%r), orca_afc.__file__\n"
                  "page = AFCPage(); html = page.get_ui()\n"
                  "assert '<style>' in html and 'orca.onMessage' in html\n"
                  "import os; assert os.path.isfile(page.get_icon())\n"
                  "orca_afc.AFCPlugin().register_capabilities()\n"
                  "assert [c.__name__ for c in fake_orca.orca.capabilities] == ['AFCPage', 'AFCGcode', 'AFCAgent']\n"
                  "print('ok')\n") % (str(TESTS), str(target), str(TESTS.parent), str(target))
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(result.stdout.strip(), "ok", result.stderr)


if __name__ == "__main__":
    unittest.main()
