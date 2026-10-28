"""Klipper's action prompts read from the G-code store."""

import unittest

import fake_orca  # noqa: F401  (puts afc-plugin/ on sys.path)

from orca_afc import prompt


def lines(*messages, start=1.0):
    return [{"message": m, "time": start + i, "type": "response"} for i, m in enumerate(messages)]


CALIBRATION = ("// action:prompt_begin AFC Calibration",
               "// action:prompt_text Select a unit to calibrate.",
               "// action:prompt_button Turtle_1|UNIT_CALIBRATION UNIT=Turtle_1|primary",
               "// action:prompt_button_group_start",
               "// action:prompt_button Lane 1|CALIBRATE_AFC LANE=lane1",
               "// action:prompt_button Lane 2",
               "// action:prompt_button_group_end",
               "// action:prompt_footer_button Cancel|RESPOND TYPE=command MSG=action:prompt_end|warning",
               "// action:prompt_show")


class WatchTest(unittest.TestCase):
    def test_reads_an_open_prompt(self):
        watch = prompt.Watch()
        self.assertTrue(watch.feed(lines("echo: hello", *CALIBRATION)))
        self.assertEqual(watch.json(), {
            "id": 1, "open": True, "running": False, "errors": [], "title": "AFC Calibration", "text": ["Select a unit to calibrate."],
            "rows": [[{"label": "Turtle_1", "gcode": "UNIT_CALIBRATION UNIT=Turtle_1", "style": "primary"}],
                     [{"label": "Lane 1", "gcode": "CALIBRATE_AFC LANE=lane1", "style": ""},
                      {"label": "Lane 2", "gcode": "Lane 2", "style": ""}]],
            "footer": [{"label": "Cancel", "gcode": "RESPOND TYPE=command MSG=action:prompt_end",
                        "style": "warning"}]})

    def test_lines_read_once(self):
        watch = prompt.Watch()
        store = lines(*CALIBRATION)
        watch.feed(store)
        self.assertFalse(watch.feed(store))
        self.assertEqual(watch.id, 1)

    def test_end_closes_it(self):
        watch = prompt.Watch()
        store = lines(*CALIBRATION)
        watch.feed(store)
        self.assertTrue(watch.feed(store + lines("// action:prompt_end", start=20.0)))
        self.assertEqual(watch.json(), {"id": 2, "open": False, "running": False, "errors": []})

    def test_a_new_prompt_replaces_the_old(self):
        watch = prompt.Watch()
        watch.feed(lines(*CALIBRATION))
        watch.feed(lines("// action:prompt_begin Turtle_1", "// action:prompt_button All lanes|CALIBRATE_AFC LANE=all",
                         "// action:prompt_show", start=20.0))
        self.assertEqual((watch.id, watch.prompt["title"], len(watch.prompt["rows"])), (2, "Turtle_1", 1))

    def test_unshown_prompt_stays_closed(self):
        watch = prompt.Watch()
        self.assertFalse(watch.feed(lines(*CALIBRATION[:-1])))
        self.assertEqual(watch.json(), {"id": 0, "open": False, "running": False, "errors": []})

    def test_calibration_runs_on_after_its_prompt_ends(self):
        # CALIBRATE_AFC ends the unit's prompt as it starts; the window waits for its result.
        watch = prompt.Watch()
        watch.feed(lines(*CALIBRATION))
        watch.start()
        watch.mark(9.0)
        watch.feed(lines("// action:prompt_end", start=20.0))
        self.assertEqual((watch.json()["open"], watch.json()["running"]), (False, True))
        watch.feed(lines("// action:prompt_begin AFC Calibration", "// action:prompt_text Calibration was completed for lane1.",
                         "// action:prompt_show", start=30.0))
        watch.finish()
        self.assertTrue(watch.json()["running"])  # until the store is read after it ended
        watch.feed([], watch.token())
        shown = watch.json()
        self.assertEqual((shown["id"], shown["open"], shown["running"], shown["text"]),
                         (5, True, False, ["Calibration was completed for lane1."]))

    def test_errors_while_gcode_runs(self):
        watch = prompt.Watch()
        watch.feed(lines("!! old error", start=1.0))
        watch.start()
        watch.mark(5.0)
        self.assertTrue(watch.feed(lines("!! Tool must be unloaded to calibrate system", "echo: x", start=6.0)))
        watch.finish()
        watch.feed([])
        self.assertEqual((watch.json()["errors"], watch.json()["running"]), (["Tool must be unloaded to calibrate system"], False))
        # The next G-code starts with none.
        watch.start()
        self.assertEqual(watch.json()["errors"], [])

    def test_a_read_begun_before_the_end_does_not_settle_it(self):
        # The G-code's prompt may land in the store after a read began: only a later read counts.
        watch = prompt.Watch()
        watch.start()
        token = watch.token()
        watch.finish()
        watch.feed([], token)
        self.assertTrue(watch.json()["running"])
        watch.feed(lines(*CALIBRATION, start=40.0), watch.token())
        self.assertEqual((watch.json()["running"], watch.json()["open"]), (False, True))

    def test_errors_before_the_store_was_first_read_are_old(self):
        watch = prompt.Watch()
        watch.start()
        watch.mark(None)
        watch.feed(lines("!! old error", start=1.0))
        self.assertEqual(watch.errors, [])


if __name__ == "__main__":
    unittest.main()
