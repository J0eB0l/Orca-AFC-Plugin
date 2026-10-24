"""AFC's runout lanes (infinite spool): the chains, matching spools and SET_RUNOUT."""

import unittest

import fake_orca  # noqa: F401
from orca_afc import runout
from orca_afc.protocol import AFCError, Lane, Snapshot


def spool(name, material="PLA", color="#101010", weight=1000.0, **values):
    return Lane(name=name, material=material, color=color, weight=weight, prep=True, load=True, **values)


def empty(name, **values):
    return Lane(name=name, **values)


class SameFilamentTest(unittest.TestCase):
    def test_material_and_color(self):
        self.assertTrue(runout.same_filament(spool("a", "PLA+", "#F2F2F2"), spool("b", "pla basic", "#FFFFFF")))
        self.assertFalse(runout.same_filament(spool("a", "PLA"), spool("b", "PETG")))
        self.assertFalse(runout.same_filament(spool("a", color="#0A2989"), spool("b", color="#0086D6")))

    def test_not_without_a_spool_material_or_color(self):
        self.assertFalse(runout.same_filament(spool("a"), empty("b", material="PLA", color="#101010")))
        self.assertFalse(runout.same_filament(spool("a", material=""), spool("b", material="")))
        self.assertFalse(runout.same_filament(spool("a", color=""), spool("b", color="")))


class ChainTest(unittest.TestCase):
    def test_chains_from_their_first_lane(self):
        snap = Snapshot(lanes=[spool("lane1", runout_lane="lane2"), spool("lane2", runout_lane="lane3"), spool("lane3"),
                               spool("lane4"), spool("lane5", runout_lane="lane4"), spool("lane6", runout_lane="gone")])
        self.assertEqual([[lane.name for lane in chain] for chain in runout.chains(snap)],
                         [["lane1", "lane2", "lane3"], ["lane5", "lane4"], ["lane6"]])


class GcodeTest(unittest.TestCase):
    def setUp(self):
        self.snap = Snapshot(lanes=[spool("lane1", runout_lane="lane2"), spool("lane2"), spool("lane3")])

    def test_sets_and_clears(self):
        self.assertEqual(runout.links_gcode(self.snap, [("lane2", "lane3"), ("lane1", "")]),
                         ["SET_RUNOUT LANE=lane2 RUNOUT=lane3", "SET_RUNOUT LANE=lane1 RUNOUT=NONE"])

    def test_leaves_out_what_is_set(self):
        self.assertEqual(runout.links_gcode(self.snap, [("lane1", "lane2"), ("lane3", "")]), [])

    def test_refuses_unknown_lanes_and_itself(self):
        for links, error in (([("lane9", "lane1")], "unknown lane lane9"),
                             ([("lane1", "lane9")], "unknown runout lane lane9"),
                             ([("lane1", "lane1 M112")], "unknown runout lane lane1 M112"),
                             ([("lane3", "lane3")], "lane3 cannot run out onto itself")):
            with self.assertRaises(AFCError) as caught:
                runout.links_gcode(self.snap, links)
            self.assertEqual(str(caught.exception), error)

    def test_refuses_a_loop(self):
        with self.assertRaises(AFCError) as caught:
            runout.links_gcode(self.snap, [("lane2", "lane3"), ("lane3", "lane1")])
        self.assertEqual(str(caught.exception), "lane1 would run out back onto itself")
        # Taking lane1's own link off in the same go breaks the loop.
        self.assertEqual(runout.links_gcode(self.snap, [("lane2", "lane3"), ("lane3", "lane1"), ("lane1", "")]),
                         ["SET_RUNOUT LANE=lane2 RUNOUT=lane3", "SET_RUNOUT LANE=lane3 RUNOUT=lane1",
                          "SET_RUNOUT LANE=lane1 RUNOUT=NONE"])


class MatchingTest(unittest.TestCase):
    def test_in_the_toolhead_then_the_emptiest(self):
        snap = Snapshot(lanes=[spool("lane1", weight=800), spool("lane2", weight=200), spool("lane3", weight=0),
                               spool("lane4", weight=900, tool_loaded=True), spool("lane5", "PETG"), empty("lane6")])
        # lane3 has no weight, so it goes last; lane5 has no match and lane6 no spool.
        self.assertEqual(runout.matching_links(snap),
                         [("lane4", "lane2"), ("lane2", "lane1"), ("lane1", "lane3")])

    def test_keeps_the_last_lanes_own_link(self):
        snap = Snapshot(lanes=[spool("lane1", weight=100), spool("lane2", runout_lane="lane3"), spool("lane3", "PETG")])
        self.assertEqual(runout.matching_links(snap), [("lane1", "lane2")])

    def test_breaks_a_loop_back_into_the_chain(self):
        snap = Snapshot(lanes=[spool("lane1", weight=100), spool("lane2", runout_lane="lane3"),
                               spool("lane3", "PETG", runout_lane="lane1")])
        self.assertEqual(runout.matching_links(snap), [("lane1", "lane2"), ("lane2", "")])
        self.assertEqual(runout.links_gcode(snap, runout.matching_links(snap)),
                         ["SET_RUNOUT LANE=lane1 RUNOUT=lane2", "SET_RUNOUT LANE=lane2 RUNOUT=NONE"])

    def test_nothing_matches(self):
        self.assertEqual(runout.matching_links(Snapshot(lanes=[spool("lane1"), spool("lane2", "PETG")])), [])

    def test_clear(self):
        snap = Snapshot(lanes=[spool("lane1", runout_lane="lane2"), spool("lane2"), spool("lane3", runout_lane="lane1")])
        self.assertEqual(runout.clear_links(snap), [("lane1", ""), ("lane3", "")])


class AutoLinksTest(unittest.TestCase):
    """AMS filament backup when a print is sent: its lanes backed by matching spools."""

    def test_the_prints_lane_first_then_the_emptiest(self):
        snap = Snapshot(lanes=[spool("lane1", weight=200), spool("lane2", weight=800, tool_loaded=True),
                               spool("lane3", weight=500), spool("lane4", "PETG"), spool("lane5", "PETG")])
        # lane2 is in a toolhead, but the print runs on lane1; the PETG pair is not the print's.
        links, made = runout.auto_links(snap, ["lane1"], {})
        self.assertEqual(links, [("lane1", "lane3"), ("lane3", "lane2")])
        self.assertEqual(made, {"lane1": "lane3", "lane3": "lane2"})

    def test_a_runout_lane_set_by_hand_stays(self):
        snap = Snapshot(lanes=[spool("lane1", runout_lane="lane4"), spool("lane2", weight=300), spool("lane3"),
                               spool("lane4", "PETG"), spool("lane5", weight=900)])
        # lane1 runs out onto PETG by hand, so the spares past it are left alone.
        self.assertEqual(runout.auto_links(snap, ["lane1"], {}), ([], {}))
        # Another lane of the print still gets the spares.
        self.assertEqual(runout.auto_links(snap, ["lane1", "lane5"], {}),
                         ([("lane2", "lane3"), ("lane5", "lane2")], {"lane5": "lane2", "lane2": "lane3"}))

    def test_its_own_links_follow_the_spools(self):
        # lane2 got PETG since the last print, and a reset took lane3's link off.
        snap = Snapshot(lanes=[spool("lane1", runout_lane="lane2"), spool("lane2", "PETG"), spool("lane3"),
                               spool("lane4", weight=0)])
        links, made = runout.auto_links(snap, ["lane1"], {"lane1": "lane2", "lane3": "lane4"})
        self.assertEqual(links, [("lane1", "lane3"), ("lane3", "lane4")])
        self.assertEqual(made, {"lane1": "lane3", "lane3": "lane4"})
        # Nothing matches any more: its link comes off, and a print on no matching spool links none.
        snap = Snapshot(lanes=[spool("lane1", runout_lane="lane2"), spool("lane2", "PETG")])
        self.assertEqual(runout.auto_links(snap, ["lane1"], {"lane1": "lane2"}), ([("lane1", "")], {}))
        self.assertEqual(runout.auto_links(snap, ["lane2"], {}), ([], {}))

    def test_never_a_loop(self):
        # lane2 runs out onto lane1 by hand, so lane1 cannot run out onto lane2.
        snap = Snapshot(lanes=[spool("lane1"), spool("lane2", weight=200, runout_lane="lane1")])
        self.assertEqual(runout.auto_links(snap, ["lane1"], {}), ([], {}))
        # Its own link from the last print would close the new chain: it comes off.
        snap = Snapshot(lanes=[spool("lane1", weight=900), spool("lane2", weight=200),
                               spool("lane3", weight=500, runout_lane="lane1")])
        links, made = runout.auto_links(snap, ["lane1"], {"lane3": "lane1"})
        self.assertEqual(links, [("lane1", "lane2"), ("lane2", "lane3"), ("lane3", "")])
        self.assertEqual(runout.links_gcode(snap, links)[-1], "SET_RUNOUT LANE=lane3 RUNOUT=NONE")
        self.assertEqual(made, {"lane1": "lane2", "lane2": "lane3"})
