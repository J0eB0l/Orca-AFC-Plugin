"""AFC's measured change times for OrcaSlicer's time estimate."""

import unittest

import fake_orca  # noqa: F401

from orca_afc import swaptimes
from orca_afc.swaptimes import Tracker

LOG = """\
06:01:10 lane1 is now loaded in toolhead t:34.244
06:01:30 Total change time: t:58.273
06:02:00 Lane lane1 unload done t:23.118
06:02:30 lane2 is now loaded in toolhead t:51.434
06:02:40 Tool swap done (Δt:5.676s, t:5.677)
06:03:00 Lane lane2 unload done t:3600.5
"""


def stats(load=(0, 0), unload=(0, 0), swap=(0, 0), new=1):
    """afc_stats with two extruders, as AFC lays them out: "extruder" counts
    under change_count, "extruder1" under cut."""
    return {"average_time": {"tool_load": load[0], "tool_unload": unload[0], "tool_swap": swap[0],
                             "tool_change": 0, "new_average_calc": new},
            "extruder": {"change_count": {"total": 1, "tool_load": load[1] - 1, "tool_unload": unload[1] - 1,
                                          "tool_selected": swap[1] - 1}, "cut": {"last_blade_changed": "N/A"}},
            "extruder1": {"cut": {"total": 1, "tool_load": "1", "tool_unload": 1},
                          "change_count": {"tool_selected": 1}},
            "lane1": {"load_count": "5"}}


class SwapTimesTest(unittest.TestCase):
    def test_log_samples(self):
        self.assertEqual(swaptimes.log_samples(LOG), {"load": [34.244, 51.434], "unload": [23.118, 3600.5],
                                                      "tool_change": [5.676]})

    def test_lane_samples(self):
        self.assertEqual(swaptimes.lane_samples(LOG), {"lane1": {"load": [34.244], "unload": [23.118]},
                                                       "lane2": {"load": [51.434], "unload": []}})

    def test_totals_sum_the_counts_of_every_extruder(self):
        self.assertEqual(swaptimes.totals({"value": stats((100, 4), (50, 2), (12, 2))}),
                         {"load": (100, 4), "unload": (50, 2), "tool_change": (12, 2)})

    def test_older_halving_averages_have_no_counts(self):
        self.assertIsNone(swaptimes.totals(stats(new=0)))
        self.assertIsNone(swaptimes.totals({}))

    def test_one_change_between_reads_is_one_sample(self):
        tracker = Tracker()
        tracker.update(stats((5000, 10), (9000, 10)))   # a lifetime of sums: no sample
        for n in range(3):
            tracker.update(stats((5000 + 30 * (n + 1), 11 + n), (9000 + 24 * (n + 1), 11 + n)))
        self.assertEqual(tracker.times(), {"load": 30.0, "unload": 24.0})

    def test_a_count_without_its_time_waits(self):
        tracker = Tracker()
        tracker.update(stats(unload=(100, 2)))
        tracker.update(stats(unload=(100, 3)))   # AFC counted the unload before writing its time
        tracker.update(stats(unload=(124, 3)))
        self.assertEqual(tracker.samples["unload"], [24.0])

    def test_reset_stats_starts_over(self):
        tracker = Tracker()
        tracker.update(stats(load=(500, 10)))
        tracker.update(stats(load=(0, 0)))
        tracker.update(stats(load=(31, 1)))
        self.assertEqual(tracker.samples["load"], [31.0])

    def test_median_leaves_out_stand_alone_moves(self):
        tracker = Tracker()
        tracker.seed(LOG + "06:04 Lane lane3 unload done t:25.0\n06:05 Lane lane3 unload done t:93.2\n")
        # 3600 s is a stand-alone unload timed from an old mark: dropped; 93 s is outvoted.
        self.assertEqual(tracker.samples["unload"], [23.118, 25.0, 93.2])
        self.assertEqual(tracker.times(), {"unload": 25.0})
        self.assertTrue(tracker.seeded)

    def test_keeps_the_latest_changes(self):
        tracker = Tracker()
        tracker.seed("".join("lane1 is now loaded in toolhead t:%d\n" % v for v in range(1, 41)))
        self.assertEqual(tracker.samples["load"], [float(v) for v in range(16, 41)])


if __name__ == "__main__":
    unittest.main()
