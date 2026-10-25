import unittest

import fake_orca  # noqa: F401  (puts afc-plugin/ on sys.path)
from orca_afc import afclog

# AFC.log as AFC_logger.py writes it; a tail starts inside a line.
LOG = """\
ing_for_ace():685]   - DEBUG:ACE Ace2_1: still not found
16:58:21 335689.275 [AFC_BambuAMS_bridge.py:_say():1143]        - WARN:  AFC_BridgeBox chain1: can't reach the BridgeBox (timed out).
16:58:29 335697.800 [AFC_prep.py:PREP():97]                     - RAW:  O  |    X ERROR    |
16:59:05 335732.853 [AFC_BambuAMS_bridge.py:_check_quiet():3780] - WARN:  AFC bambu: bridge has been unreachable for 47s
16:59:09 335737.728 [AFC_ACE.py:_deferred_ace_connect():639]    - ERROR:ACE Ace2_1: failed to connect
16:59:09 335737.728 [AFC_ACE.py:_deferred_ace_connect():639]    - ERROR:after 5 attempts
17:00:00 335788.100 [AFC.py:cmd_AFC_STATUS():900]               - INFO: lane1 loaded
17:04:09 336036.971 [AFC_BambuAMS_bridge.py:_check_quiet():3780] - WARN:  AFC bambu: bridge has been unreachable for 351s
"""


class EntriesTest(unittest.TestCase):
    def test_lines_in_afcs_format(self):
        found = afclog.entries(LOG)
        self.assertEqual([e.level for e in found], ["w", "r", "w", "e", "e", "i", "w"])  # the cut line left out
        self.assertEqual(found[0].text, "AFC_BridgeBox chain1: can't reach the BridgeBox (timed out).")
        self.assertEqual(found[3].clock, "335737.728")

    def test_a_line_as_the_page_shows_it(self):
        found = afclog.entries(LOG)
        self.assertEqual(found[3].shown(), "16:59:09 ERROR ACE Ace2_1: failed to connect")
        self.assertEqual(found[5].shown(), "17:00:00 INFO  lane1 loaded")

    def test_the_clear_mark_is_the_last_line(self):
        self.assertTrue(afclog.last_line(LOG).startswith("17:04:09 336036.971"))
        self.assertEqual(afclog.last_line("cut inside a line"), "")


class ViewTest(unittest.TestCase):
    def test_errors_and_warnings_newest_first(self):
        view = afclog.view(LOG, "")
        self.assertEqual(view["name"], "AFC.log")
        self.assertEqual(view["file"], "logs/AFC.log")
        self.assertEqual(view["lines"][0], ["w", "16:58:21 WARN  AFC_BridgeBox chain1: can't reach the BridgeBox "
                                                 "(timed out)."])
        self.assertEqual(view["problems"], [
            # A warning that repeats with other numbers is counted once, with its latest text.
            {"type": "warning", "time": "17:04:09", "text": "AFC bambu: bridge has been unreachable for 351s",
             "count": 2},
            # A message's lines are one problem.
            {"type": "error", "time": "16:59:09", "text": "ACE Ace2_1: failed to connect\nafter 5 attempts",
             "count": 1},
            {"type": "warning", "time": "16:58:21",
             "text": "AFC_BridgeBox chain1: can't reach the BridgeBox (timed out).", "count": 1},
        ])

    def test_only_what_came_after_the_clear_mark(self):
        mark = afclog.entries(LOG)[4].raw  # the error's last line
        problems = afclog.view(LOG, mark)["problems"]
        self.assertEqual([p["text"] for p in problems], ["AFC bambu: bridge has been unreachable for 351s"])
        self.assertEqual(problems[0]["count"], 1)

    def test_a_mark_the_tail_no_longer_holds_lists_all(self):
        self.assertEqual(len(afclog.view(LOG, "16:00:00 1.000 - ERROR:gone")["problems"]), 3)

    def test_cleared_at_the_last_line_lists_none(self):
        self.assertEqual(afclog.view(LOG, afclog.last_line(LOG))["problems"], [])

    def test_the_logs_path_in_moonrakers_logs_root(self):
        files = [{"path": "moonraker.log"}, {"path": "klippylogs/klippy.log"}, {"path": "klippylogs/AFC.log"}]
        self.assertEqual(afclog.log_path(files), "klippylogs/AFC.log")
        self.assertEqual(afclog.log_path(files + [{"path": "AFC.log"}]), "AFC.log")  # the usual place first
        self.assertEqual(afclog.log_path([{"path": "old/AFC.log"}, "junk", {"size": 1}]), "old/AFC.log")
        self.assertEqual(afclog.log_path([{"path": "klippy.log"}]), "")
        self.assertEqual(afclog.view("", "", "klippylogs/AFC.log")["file"], "logs/klippylogs/AFC.log")
        self.assertEqual(afclog.view("", "")["file"], "logs/AFC.log")

    def test_the_last_lines_only(self):
        text = "".join("10:00:%02d %d.000 - DEBUG:line %d\n" % (n % 60, n, n) for n in range(afclog.SHOWN + 5))
        lines = afclog.view(text, "")["lines"]
        self.assertEqual(len(lines), afclog.SHOWN)
        self.assertEqual(lines[-1], ["d", "10:00:04 DEBUG line %d" % (afclog.SHOWN + 4)])


if __name__ == "__main__":
    unittest.main()
