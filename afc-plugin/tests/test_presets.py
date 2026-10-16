"""Which filament preset the printer agent names for each AFC lane."""

import json
import os
import tempfile
import unittest

import fake_orca
from orca_afc import presets
from orca_afc.presets import Candidate, FilamentIds
from orca_afc.protocol import Lane


def lane(material="PLA", vendor="Polymaker", product="PolyLite", sub_type=""):
    return Lane(name="lane1", material=material, vendor=vendor, filament_name=product, sub_type=sub_type,
                prep=True, load=True)


def candidate(name, kind="PLA", vendor="Generic", system=True, file="", support=False):
    return Candidate(name=name, type=kind, vendor=vendor, system=system, file=file, support=support)


class RankTest(unittest.TestCase):
    CANDIDATES = [
        candidate("Generic PLA @U1"),
        candidate("Polymaker PolyTerra PLA @U1", vendor="Polymaker"),
        candidate("Polymaker PolyLite PLA @U1", vendor="Polymaker"),
        candidate("Bambu PLA Basic @U1", vendor="Bambu Lab"),
        candidate("Polymaker PolyLite PETG @U1", kind="PETG", vendor="Polymaker"),
    ]

    def names(self, ranked):
        return [c.name for c in ranked]

    def test_the_spools_own_preset_first(self):
        self.assertEqual(self.names(presets.rank(lane(), self.CANDIDATES)),
                         ["Polymaker PolyLite PLA @U1", "Polymaker PolyTerra PLA @U1"])

    def test_no_name_or_vendor_match_ranks_nothing(self):
        self.assertEqual(presets.rank(lane(vendor="Elegoo", product="Rapid"), self.CANDIDATES), [])
        self.assertEqual(self.names(presets.generic(lane(vendor="Elegoo"), self.CANDIDATES))[0], "Generic PLA @U1")

    def test_sub_type_first(self):
        candidates = [candidate("Generic PA @U1", kind="PA"), candidate("Generic PA-CF @U1", kind="PA-CF")]
        self.assertEqual(self.names(presets.generic(lane("PA", "", "", "CF"), candidates)), ["Generic PA-CF @U1"])
        self.assertEqual(self.names(presets.generic(lane("PA", "", ""), candidates)), ["Generic PA @U1"])

    def test_user_preset_after_the_system_one(self):
        mine = candidate("Polymaker PolyLite PLA @U1", vendor="Polymaker", system=False)
        ranked = presets.rank(lane(), [mine] + self.CANDIDATES)
        self.assertTrue(ranked[0].system)
        self.assertIs(ranked[1], mine)

    def test_support_presets_only_for_support_spools(self):
        # A Snapmaker PLA spool once got Snapmaker's breakaway support preset.
        breakaway = candidate("Snapmaker Breakaway Support For PLA @U1", support=True)
        candidates = [breakaway, candidate("Generic PLA @U1")]
        self.assertEqual(presets.rank(lane(vendor="Snapmaker", product=""), candidates), [])
        self.assertEqual(self.names(presets.generic(lane(vendor="Snapmaker"), candidates)), ["Generic PLA @U1"])
        self.assertEqual(self.names(presets.rank(lane(vendor="Snapmaker", product="Breakaway"), candidates)),
                         ["Snapmaker Breakaway Support For PLA @U1"])

    def test_support_material_keeps_its_presets(self):
        pva = candidate("Generic PVA @U1", kind="PVA", support=True)
        self.assertEqual(self.names(presets.generic(lane("PVA", "", ""), [pva])), ["Generic PVA @U1"])

    def test_plainest_generic_first(self):
        candidates = [candidate("Generic PLA Silk @U1"), candidate("Generic PLA High Speed @U1"),
                      candidate("Generic PLA @U1")]
        self.assertEqual(self.names(presets.generic(lane(vendor="Elegoo", product="Rapid"), candidates)),
                         ["Generic PLA @U1", "Generic PLA Silk @U1", "Generic PLA High Speed @U1"])

    def test_name_words(self):
        self.assertEqual(presets.name_words("Generic PLA Matte @U1 0.4 nozzle"), ["generic", "pla", "matte"])


class CollectTest(unittest.TestCase):
    def test_visible_compatible_filaments(self):
        Preset = fake_orca.Preset
        bundle = fake_orca.PresetBundle(filaments=[
            Preset({"filament_type": ["pla"], "filament_vendor": ["Generic"]}, name="Generic PLA", is_system=True),
            Preset({"filament_type": ["PLA"]}, name="Hidden", is_visible=False),
            Preset({"filament_type": ["PLA"]}, name="Other printer", is_compatible=False),
            Preset({"filament_type": ["PLA"]}, name="Default Filament", is_default=True),
            Preset({"filament_type": "PETG", "filament_vendor": "Me", "inherits": "Generic PETG"}, name="My PETG"),
            Preset({"filament_type": "PLA", "filament_is_support": "1"}, name="Breakaway"),
        ])
        found = presets.collect(bundle)
        self.assertEqual([(c.name, c.type, c.vendor, c.system, c.inherits, c.support) for c in found],
                         [("Generic PLA", "PLA", "Generic", True, "", False),
                          ("My PETG", "PETG", "Me", False, "Generic PETG", False),
                          ("Breakaway", "PLA", "", False, "", True)])


class FilamentIdTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = folder.name
        self.system = os.path.join(self.root, "system")

    def write(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def test_follows_inherits_across_vendors(self):
        self.write(os.path.join(self.system, "OrcaFilamentLibrary", "filament", "base", "Polymaker PolyLite PLA @base.json"),
                   {"filament_id": "GFL01"})
        u1 = self.write(os.path.join(self.system, "Snapmaker", "filament", "Polymaker PolyLite PLA @U1.json"),
                        {"inherits": "Polymaker PolyLite PLA @base"})
        mine = self.write(os.path.join(self.root, "user", "1234", "filament", "My PolyLite.json"),
                          {"inherits": "Polymaker PolyLite PLA @U1"})
        ids = FilamentIds()
        system = candidate("Polymaker PolyLite PLA @U1", file=u1)
        ids.learn([system])
        self.assertEqual(ids.of(system), "GFL01")
        # A user preset sits outside the system folder; its parent is found there all the same.
        self.assertEqual(ids.of(candidate("My PolyLite", system=False, file=mine)), "GFL01")

    def test_loops_and_missing_files(self):
        a = self.write(os.path.join(self.system, "V", "filament", "a.json"), {"inherits": "b"})
        self.write(os.path.join(self.system, "V", "filament", "b.json"), {"inherits": "a"})
        ids = FilamentIds()
        self.assertEqual(ids.of(candidate("a", file=a)), "")
        self.assertEqual(ids.of(candidate("gone", file=os.path.join(self.system, "gone.json"))), "")

    def test_match(self):
        generic = self.write(os.path.join(self.system, "V", "filament", "Generic PLA.json"), {"filament_id": "GFL99"})
        nameless = self.write(os.path.join(self.system, "V", "filament", "Polymaker PolyLite PLA.json"), {})
        candidates = [candidate("Polymaker PolyLite PLA", vendor="Polymaker", file=nameless),
                      candidate("Generic PLA", file=generic)]
        ids = FilamentIds()
        # The best match has no id of its own, so the next one with an id is it.
        self.assertEqual(presets.match(lane(), candidates, ids), "GFL99")
        self.assertEqual(presets.match(lane("TPU"), candidates, ids), presets.UNKNOWN_FILAMENT_ID)


if __name__ == "__main__":
    unittest.main()
