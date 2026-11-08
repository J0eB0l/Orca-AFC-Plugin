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
            Preset({"filament_type": ["pla"], "filament_vendor": ["Generic"]}, name="Generic PLA", file="pla.json",
                   is_system=True),
            Preset({"filament_type": ["PETG"]}, name="Generic PETG", file="petg.json", is_system=True, is_visible=False),
            Preset({"filament_type": ["PLA"]}, name="Hidden", is_visible=False),
            Preset({"filament_type": ["PLA"]}, name="Other printer", is_compatible=False),
            Preset({"filament_type": ["PLA"]}, name="Default Filament", is_default=True),
            Preset({"filament_type": "PETG", "filament_vendor": "Me", "inherits": "Generic PETG"}, name="My PETG",
                   file="mine.json"),
            Preset({"filament_type": "PLA", "filament_is_support": "1"}, name="Breakaway", file="breakaway.json"),
        ])
        found = presets.collect(bundle)
        self.assertEqual([(c.name, c.type, c.vendor, c.system, c.inherits, c.support, c.base, c.file) for c in found],
                         [("Generic PLA", "PLA", "Generic", True, "", False, "Generic PLA", "pla.json"),
                          ("My PETG", "PETG", "Me", False, "Generic PETG", False, "Generic PETG", "petg.json"),
                          ("Breakaway", "PLA", "", False, "", True, "Breakaway", "breakaway.json")])

    def test_user_presets_stand_for_their_system_preset(self):
        Preset = fake_orca.Preset
        bundle = fake_orca.PresetBundle(filaments=[
            Preset({"filament_type": "PLA"}, name="Bambu PLA Basic @BBL X1C", is_system=True, is_visible=False,
                   is_compatible=False),
            Preset({"filament_type": "PLA", "inherits": "Bambu PLA Basic @BBL X1C"}, name="My Bambu PLA"),
            Preset({"filament_type": "PLA", "inherits": "Gone"}, name="Orphan"),
            Preset({"filament_type": "PLA", "inherits": "My Generic PLA"}, name="My PLA #13", file="13.json"),
            Preset({"filament_type": "PLA", "inherits": "Generic PLA @System"}, name="My Generic PLA",
                   file="mine.json", is_visible=False),
            Preset({"filament_type": "PLA"}, name="Generic PLA @System", file="generic.json", is_system=True,
                   is_visible=False),
        ])
        # The sync never picks a preset whose system preset does not fit the printer.
        self.assertEqual([(c.name, c.base, c.file) for c in presets.collect(bundle)],
                         [("My PLA #13", "Generic PLA @System", "generic.json")])


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
        ids = FilamentIds()
        system = candidate("Polymaker PolyLite PLA @U1", file=u1)
        ids.learn([system])
        self.assertEqual(ids.of(system), "GFL01")

    def test_a_user_presets_own_id_is_stale(self):
        # Joe's user presets still held ids from older OrcaSlicer versions (OGFL99), which 2.5 ignores.
        generic = self.write(os.path.join(self.system, "OrcaFilamentLibrary", "filament", "Generic PLA @System.json"),
                             {"filament_id": "OFDSrzZ8"})
        mine = self.write(os.path.join(self.root, "user", "1", "filament", "Inland PLA.json"),
                          {"inherits": "Generic PLA @System", "filament_id": "OGFL99"})
        root = self.write(os.path.join(self.root, "user", "1", "filament", "Spool 7.json"), {"filament_id": "P5ba6812"})
        Preset = fake_orca.Preset
        bundle = fake_orca.PresetBundle(filaments=[
            Preset({"filament_type": "PLA"}, name="Generic PLA @System", file=generic, is_system=True, is_visible=False),
            Preset({"filament_type": "PLA", "inherits": "Generic PLA @System"}, name="Inland PLA", file=mine),
            Preset({"filament_type": "PLA"}, name="Spool 7", file=root),
        ])
        ids = FilamentIds()
        self.assertEqual([ids.of(c) for c in presets.collect(bundle)], ["OFDSrzZ8", "P5ba6812"])

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


    def test_the_spoolman_filament_id_comes_first(self):
        generic = self.write(os.path.join(self.system, "V", "filament", "Generic PLA.json"), {"filament_id": "OFDSrzZ8"})
        lite = self.write(os.path.join(self.system, "V", "filament", "Polymaker PolyLite PLA.json"), {"filament_id": "OF5CgdDq"})
        terra = self.write(os.path.join(self.system, "V", "filament", "Polymaker PolyTerra PLA.json"), {"filament_id": "OFterra1"})
        candidates = [candidate("Polymaker PolyLite PLA", vendor="Polymaker", file=lite),
                      candidate("Polymaker PolyTerra PLA", vendor="Polymaker", file=terra),
                      candidate("Generic PLA", file=generic)]
        ids = FilamentIds()
        spool = lane(product="PolyLite")
        self.assertEqual(presets.match(spool, candidates, ids), "OF5CgdDq")
        spool.filament_id = "OFterra1"
        self.assertEqual(presets.match(spool, candidates, ids), "OFterra1")
        self.assertEqual(presets.best(spool, candidates, ids), "Polymaker PolyTerra PLA")
        spool.filament_id = "OFgone00"  # no preset carries it: matched as before
        self.assertEqual(presets.match(spool, candidates, ids), "OF5CgdDq")

    def test_product_id_needs_the_vendor_and_product(self):
        generic = self.write(os.path.join(self.system, "V", "filament", "Generic PLA.json"), {"filament_id": "OFDSrzZ8"})
        lite = self.write(os.path.join(self.system, "V", "filament", "Polymaker PolyLite PLA.json"), {"filament_id": "OF5CgdDq"})
        mine = self.write(os.path.join(self.root, "user", "1", "filament", "Sunlu PLA.json"), {"filament_id": "P5ba6812"})
        candidates = [Candidate(name="Polymaker PolyLite PLA", type="PLA", vendor="Polymaker", system=True, file=lite,
                                base_system=True),
                      Candidate(name="Sunlu PLA", type="PLA", vendor="Sunlu", system=False, file=mine),
                      Candidate(name="Generic PLA", type="PLA", vendor="Generic", system=True, file=generic,
                                base_system=True)]
        ids = FilamentIds()
        self.assertEqual(presets.product_id(lane(product="PolyLite"), candidates, ids), "OF5CgdDq")
        # Another product of the vendor, a generic, or the user's own preset is never saved.
        self.assertEqual(presets.product_id(lane(product="PolyTerra"), candidates, ids), "")
        self.assertEqual(presets.product_id(lane(vendor="Overture", product="PLA"), candidates, ids), "")
        self.assertEqual(presets.product_id(lane(vendor="Sunlu", product="PLA"), candidates, ids), "")
        self.assertEqual(presets.product_id(lane(vendor="", product="PolyLite"), candidates, ids), "")

    def test_a_bambu_tag_id_translates_to_the_preset_carrying_ours(self):
        from orca_afc import bambu_ids
        self.addCleanup(bambu_ids.set_map, {})
        bambu_ids.set_map({"OFoiVqVM": "GFA00"})
        basic = self.write(os.path.join(self.system, "V", "filament", "Bambu PLA Basic.json"), {"filament_id": "OFoiVqVM"})
        generic = self.write(os.path.join(self.system, "V", "filament", "Generic PLA.json"), {"filament_id": "OFDSrzZ8"})
        candidates = [Candidate(name="Bambu PLA Basic", type="PLA", vendor="Bambu Lab", system=True, file=basic,
                                base_system=True),
                      Candidate(name="Generic PLA", type="PLA", vendor="Generic", system=True, file=generic,
                                base_system=True)]
        ids = FilamentIds()
        spool = lane(vendor="", product="")
        self.assertEqual(presets.match(spool, candidates, ids), "OFDSrzZ8")
        spool.bambu_id = "GFA00"
        self.assertEqual(presets.match(spool, candidates, ids), "OFoiVqVM")
        self.assertEqual(presets.product_id(spool, candidates, ids), "OFoiVqVM")
        spool.bambu_id = "GFZ99"  # no row: matched as before, nothing saved
        self.assertEqual(presets.match(spool, candidates, ids), "OFDSrzZ8")
        self.assertEqual(presets.product_id(spool, candidates, ids), "")


class SpoolPresetNameTest(unittest.TestCase):
    """The names OrcaSlicer's sync looks for the spool's own preset by, as the Snapmaker build names them."""

    def test_vendor_and_filament(self):
        self.assertEqual(presets.spool_preset_names(lane(product="PolyLite PLA")),
                         ["Polymaker PolyLite PLA", "PolyLite PLA"])

    def test_material_added_when_the_filament_name_lacks_it(self):
        self.assertEqual(presets.spool_preset_names(lane()), ["Polymaker PLA PolyLite", "PolyLite"])

    def test_vendor_already_in_the_filament_name(self):
        self.assertEqual(presets.spool_preset_name(lane(product="Polymaker PolyLite PLA")), "Polymaker PolyLite PLA")

    def test_vendor_and_material_only(self):
        self.assertEqual(presets.spool_preset_names(lane(product="", sub_type="Support")), ["Polymaker Support For PLA"])
        self.assertEqual(presets.spool_preset_names(lane(product="", sub_type="Matte")), ["Polymaker PLA Matte"])

    def test_no_vendor_is_afcs_lane_preset(self):
        spool = lane(vendor="Generic", product="")
        spool.nozzle_temp, spool.bed_temp = 210, 60
        self.assertEqual(presets.spool_preset_names(spool), ["AFC PLA 210C bed 60C"])
        spool.nozzle_temp = spool.bed_temp = 0
        self.assertEqual(presets.spool_preset_names(spool), [])

    def test_characters_a_preset_name_cannot_hold(self):
        self.assertEqual(presets.spool_preset_name(lane(vendor="A/B  Co", product="Silk@Gold")), "A-B Co PLA Silk-Gold")

    def test_best_is_the_spools_preset_else_the_generic(self):
        self.assertEqual(presets.best(lane(), RankTest.CANDIDATES), "Polymaker PolyLite PLA @U1")
        self.assertEqual(presets.best(lane(vendor="Elegoo", product="Rapid"), RankTest.CANDIDATES), "Generic PLA @U1")
        self.assertEqual(presets.best(lane(material="ASA"), RankTest.CANDIDATES), "")


class PickedSpoolInfoTest(unittest.TestCase):
    CANDIDATES = [candidate("Polymaker PolyLite PLA @Voron", vendor="Polymaker"),
                  candidate("Bambu PLA Basic @BBL X1C", vendor="Bambu Lab"),
                  candidate("Generic PLA @System"),
                  candidate("My Sunlu PLA", vendor="Sunlu")]

    def test_vendor_and_name_as_spoolman_has_them(self):
        info = lambda name: presets.picked_spool_info(name, self.CANDIDATES)
        self.assertEqual(info("Polymaker PolyLite PLA @Voron"), ("Polymaker", "PolyLite PLA"))
        self.assertEqual(info("Bambu PLA Basic @BBL X1C"), ("Bambu Lab", "PLA Basic"))
        self.assertEqual(info("Generic PLA @System"), ("", "Generic PLA"))
        self.assertEqual(info("My Sunlu PLA"), ("Sunlu", "My Sunlu PLA"))
        # A preset the printer does not list: its vendor is unknown, so AFC keeps its own.
        self.assertEqual(info("Elegoo PLA @Elegoo"), (None, "Elegoo PLA"))
        self.assertEqual(info(""), (None, None))


if __name__ == "__main__":
    unittest.main()
