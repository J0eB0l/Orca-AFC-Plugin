import json
import os
import tempfile
import unittest

from orca_afc import bambu_ids


class BambuIdsTest(unittest.TestCase):
    def setUp(self):
        self.addCleanup(bambu_ids.set_map, {})

    def test_loads_orcaslicers_map(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "printers"))
            with open(os.path.join(root, "printers", "bambu_filament_ids.json"), "w", encoding="utf-8") as f:
                json.dump({"source": "x", "filaments": {"OFoiVqVM": {"bambu_id": "GFA00", "name": "Bambu PLA Basic"}}}, f)
            self.assertEqual(bambu_ids.load(root), 1)
        self.assertEqual(bambu_ids.to_orca("GFA00"), "OFoiVqVM")
        self.assertEqual(bambu_ids.to_orca("GFA01"), "")

    def test_no_file_translates_nothing(self):
        self.assertEqual(bambu_ids.load("/nonexistent"), 0)
        self.assertEqual(bambu_ids.to_orca("GFA00"), "")

    def test_is_bambu(self):
        self.assertTrue(bambu_ids.is_bambu("GFA00"))
        self.assertFalse(bambu_ids.is_bambu("PLA-1KG"))
        self.assertFalse(bambu_ids.is_bambu(None))
