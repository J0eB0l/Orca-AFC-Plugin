"""AFC's installer in the Update page's window: the screen of its log, the
answers its prompts take, and the session the window follows."""

import unittest

import fake_orca  # noqa: F401  (the orca module, before orca_afc)
from orca_afc import installer
from orca_afc.installer import Screen, Watch
from orca_afc.moonraker import MoonrakerError

CLEAR = b"\x1b[H\x1b[2J\x1b[3J"
GREEN = b"\x1b[38;5;143;49m"
OFF = b"\x1b[0m"
UPDATE_MENU = (b"Starting update process...\r\n" + CLEAR + GREEN + "╭─╮".encode() + OFF + b"\r\n"
               + b"\r\nU. Update AFC Klipper Add-On\r\n\r\nM. Return to Main Menu\r\nQ. Quit\r\n\r\n"
               + b"Enter your choice: ")


def texts(screen):
    return ["".join(run[1] for run in line) for line in screen.lines()]


def shown(data, chunk=None):
    screen = Screen()
    chunk = chunk or len(data) or 1
    for at in range(0, len(data), chunk):
        screen.feed(data[at:at + chunk])
    return screen


class PaletteTest(unittest.TestCase):
    def test_basic_cube_and_grays(self):
        self.assertEqual(installer.palette(1), "#cd3131")
        self.assertEqual(installer.palette(143), "#afaf5f")
        self.assertEqual(installer.palette(232), "#080808")


class ScreenTest(unittest.TestCase):
    def test_a_clear_leaves_only_what_follows_it(self):
        self.assertEqual(texts(shown(UPDATE_MENU)),
                         ["╭─╮", "", "U. Update AFC Klipper Add-On", "", "M. Return to Main Menu",
                          "Q. Quit", "", "Enter your choice:"])

    def test_colors(self):
        screen = shown(GREEN + b"box" + OFF + b" text \x1b[1;31mred\x1b[22m dim\x1b[39m plain")
        self.assertEqual(screen.lines(), [[["#afaf5f", "box"], ["", " text "], ["#f14c4c", "red"],
                                           ["#cd3131", " dim"], ["", " plain"]]])

    def test_true_and_bright_colors(self):
        screen = shown(b"\x1b[38;2;1;2;300ma\x1b[94mb\x1b[48;5;12;37mc\x1b[48;2;1;2;3;31md\x1b[mx")
        self.assertEqual(screen.lines(), [[["#0102ff", "a"], ["#3b8eea", "b"], ["#e5e5e5", "c", "#3b8eea"],
                                           ["#cd3131", "d", "#010203"], ["", "x"]]])

    def test_backgrounds(self):
        screen = shown(b"\x1b[41ma\x1b[103mb\x1b[49mc\x1b[48;5;232md\x1b[0me")
        self.assertEqual(screen.lines(), [[["", "a", installer.palette(1)], ["", "b", installer.palette(11)],
                                           ["", "c"], ["", "d", "#080808"], ["", "e"]]])

    def test_a_logo_of_half_blocks(self):
        # Each cell is two pixels: the half block's color over the background's.
        screen = shown(b"\x1b[38;5;143;48;5;16m\xe2\x96\x80\x1b[49m\xe2\x96\x84\x1b[m")
        self.assertEqual(screen.lines(), [[["#afaf5f", "\u2580", installer.palette(16)], ["#afaf5f", "\u2584"]]])

    def test_spaces_on_a_background_are_kept(self):
        screen = shown(b"ab\x1b[44m  \x1b[m  ")
        self.assertEqual(screen.lines(), [[["", "ab"], ["", "  ", installer.palette(4)]]])

    def test_erasing_takes_the_terminal_background(self):
        screen = shown(b"\x1b[44mabcdef\x1b[2Kx")
        self.assertEqual(screen.lines(), [[["", "      "], ["", "x", installer.palette(4)]]])

    def test_broken_color_sequences_change_nothing(self):
        screen = shown(b"\x1b[38;5;300ma\x1b[38;2;1ma\x1b[38ma\x1b[38;9ma")
        self.assertEqual(screen.lines(), [[["", "aaaa"]]])

    def test_a_carriage_return_writes_over_the_line(self):
        self.assertEqual(texts(shown(b"Receiving objects:  45%\rReceiving objects: 100%, done.\r\nnext")),
                         ["Receiving objects: 100%, done.", "next"])

    def test_a_bare_newline_starts_a_line(self):
        self.assertEqual(texts(shown(b"one\ntwo")), ["one", "two"])

    def test_backspace_tab_and_other_controls(self):
        self.assertEqual(texts(shown(b"ab\bc\td\x07e\x00")), ["ac      de"])
        self.assertEqual(texts(shown(b"\b\bx")), ["x"])

    def test_cursor_moves(self):
        self.assertEqual(texts(shown(b"abcdef\x1b[3Gx\x1b[2Cy\x1b[3Dz\x1b[Cw\x1b[Dv")), ["abxzev"])
        self.assertEqual(texts(shown(b"abc\x1b[Gd\x1b[9G!")), ["dbc     !"])

    def test_erasing_a_line(self):
        self.assertEqual(texts(shown(b"abcdef\r\x1b[2C\x1b[K")), ["ab"])
        self.assertEqual(texts(shown(b"abcdef\r\x1b[2C\x1b[0J")), ["ab"])
        self.assertEqual(texts(shown(b"abcdef\r\x1b[2C\x1b[1Kx")), ["  xdef"])
        self.assertEqual(texts(shown(b"abcdef\x1b[2Kx")), ["      x"])
        self.assertEqual(texts(shown(b"one\r\ntwo\x1b[1J")), ["one", "two"])

    def test_a_reset_clears_the_screen_and_its_color(self):
        self.assertEqual(shown(b"old" + GREEN + b"\x1bcnew").lines(), [[["", "new"]]])
        self.assertEqual(shown(b"\x1b[44mold\x1bcnew").lines(), [[["", "new"]]])

    def test_other_sequences_show_nothing(self):
        data = (b"\x1b[?25la\x1b]8;;http://x\x07b\x1b]0;title\x1b\\c\x1bP1$r\x1b\\d\x1b(Be\x1b=f\x1b[1 qg"
                b"\x1b]2;x\x1b0h")
        self.assertEqual(texts(shown(data)), ["abcdefg"])
        self.assertEqual(texts(shown(b"\x1b[3\nx")), ["", "x"])

    def test_a_sequence_split_between_reads(self):
        self.assertEqual(shown(GREEN + b"menu" + OFF + "─".encode(), chunk=1).lines(),
                         [[["#afaf5f", "menu"], ["", "─"]]])
        self.assertEqual(texts(shown(b"a\x1b]0;title\x1b\\b", chunk=1)), ["ab"])
        self.assertEqual(texts(shown(b"a\x1b(Bb", chunk=1)), ["ab"])

    def test_an_endless_sequence_is_dropped(self):
        screen = Screen()
        screen.feed(b"a\x1b]" + b"x" * 5000)
        self.assertEqual(texts(screen), ["a]" + "x" * (installer.WIDTH - 2)])

    def test_long_lines_and_many_lines_are_cut(self):
        screen = shown(b"x" * (installer.WIDTH + 50) + b"\ry")
        self.assertEqual(texts(screen), ["y" + "x" * (installer.WIDTH - 1)])
        screen = shown(b"\x1b[%dGz" % (installer.WIDTH + 5))
        self.assertEqual(texts(screen), [""])
        screen = shown(b"".join(b"line %d\r\n" % n for n in range(installer.LINES + 10)))
        self.assertEqual(len(screen.lines()), installer.LINES)
        self.assertEqual(texts(screen)[0], "line 11")

    def test_the_prompt_is_the_last_line(self):
        self.assertEqual(shown(UPDATE_MENU).prompt(), "Enter your choice:")
        self.assertEqual(shown(UPDATE_MENU + b"U\r\n").prompt(), "")


class ChoicesTest(unittest.TestCase):
    def choices(self, data):
        screen = shown(data)
        return installer.choices(screen.lines(), screen.prompt())

    def test_a_menu_takes_its_lines(self):
        self.assertEqual(self.choices(UPDATE_MENU), [["U", "Update AFC Klipper Add-On"],
                                                     ["M", "Return to Main Menu"], ["Q", "Quit"]])

    def test_a_setting_line_leaves_its_value_out(self):
        data = (b"  1. Printer Config Directory : /home/pi/printer_data/config \r\n  5. Branch : DEV\r\n"
                b"I. Install New System\r\nQ. Exit\r\nq. Again\r\nEnter your choice: ")
        self.assertEqual(self.choices(data), [["1", "Printer Config Directory"], ["5", "Branch"],
                                              ["I", "Install New System"], ["Q", "Exit"]])

    def test_yes_or_no(self):
        self.assertEqual(self.choices(b"Do you want to update the AFC provided macros? (y/n): "),
                         [["y", "Yes"], ["n", "No"]])

    def test_letters_named_by_the_prompt(self):
        self.assertEqual(self.choices(b"  (o)verwrite / (s)kip / (b)ackup and copy? [o/s/b]: "),
                         [["o", "Overwrite"], ["s", "Skip"], ["b", "Backup"]])
        self.assertEqual(self.choices(b"Keep it? [k/d]: "), [["k", "k"], ["d", "d"]])

    def test_press_enter(self):
        self.assertEqual(self.choices(b"Press Enter to continue or CTRL+C to cancel..."), [["", "Continue"]])

    def test_the_list_just_above_another_prompt(self):
        data = (b"I. Install New System\r\nEnter your choice: C\r\nPick one\r\n"
                b"A. auto: Klipper finds the BridgeBox on USB by itself.\r\n\r\n1. /dev/serial/by-id/usb-1\r\n"
                b"USB serial port for this BridgeBox (A, a number, or a path; Enter keeps auto): ")
        self.assertEqual(self.choices(data), [["A", "auto: Klipper finds the BridgeBox on USB by itself."],
                                              ["1", "/dev/serial/by-id/usb-1"]])

    def test_a_question_with_no_list(self):
        self.assertEqual(self.choices(UPDATE_MENU + b"1\r\nEnter new directory (blank to keep): "), [])

    def test_no_prompt(self):
        self.assertEqual(self.choices(UPDATE_MENU + b"U\r\n"), [])

    def test_a_password_prompt_takes_no_answers(self):
        self.assertEqual(self.choices(UPDATE_MENU + b"Q\r\nPassword: "), [])
        self.assertEqual(self.choices(b"1. one\r\n[sudo] password for pi: "), [])
        self.assertEqual(self.choices(b"Enter passphrase for key '/home/pi/.ssh/id_ed25519': "), [])
        self.assertEqual(self.choices(b"Set a password? (y/n): "), [["y", "Yes"], ["n", "No"]])


class HelpersTest(unittest.TestCase):
    def test_hint(self):
        sudo = shown(b"sudo: a terminal is required to read the password; either use the -S option\r\n")
        self.assertEqual(installer.hint(sudo.lines()), "sudo")
        self.assertEqual(installer.hint(shown(b"sudo: a password is required\r\n").lines()), "sudo")
        self.assertEqual(installer.hint(shown(UPDATE_MENU).lines()), "")
        self.assertEqual(installer.hint(shown(UPDATE_MENU).lines(), "Password:"), "password")
        self.assertEqual(installer.hint(sudo.lines(), "[sudo] password for pi:"), "password")
        self.assertEqual(installer.hint(sudo.lines(), "Enter your choice:"), "sudo")
        self.assertEqual(installer.hint([], "Set a password? (y/n):"), "")

    def test_summary(self):
        self.assertEqual(installer.summary({"available": True, "why": "", "git": True, "branch": "DEV",
                                            "running": True, "mode": "update"}),
                         {"available": True, "why": "", "git": True, "branch": "DEV", "running": True,
                          "mode": "update"})
        self.assertEqual(installer.summary({"available": True, "why": "", "git": False, "branch": None,
                                            "running": False, "mode": ""}),
                         {"available": True, "why": "", "git": False, "branch": "", "running": False, "mode": ""})
        # An AFC from before branches answers them null, and the page gets neither.
        self.assertEqual(installer.summary({"available": False, "why": "root", "git": None, "branch": None,
                                            "running": None, "mode": None}),
                         {"available": False, "why": "root", "running": False, "mode": ""})
        # An AFC without the installer: Klipper answers its fields null.
        self.assertEqual(installer.summary({"available": None, "why": None}), {})
        self.assertEqual(installer.summary(None), {})

    def test_branch_ok(self):
        for branch in ("", "DEV", "BridgeBox-turtle", "feature/two_spools", "v1.2"):
            self.assertTrue(installer.branch_ok(branch), branch)
        for branch in ("-b", "a b", "a;b", "a#b", "a\nFIRMWARE_RESTART", "a..b", "DEV/", ".x", "a" * 101, 5, None):
            self.assertFalse(installer.branch_ok(branch), branch)

    def test_start_gcode(self):
        self.assertEqual(installer.start_gcode("update"), "AFC_INSTALLER MODE=UPDATE")
        self.assertEqual(installer.start_gcode("install", "DEV"), "AFC_INSTALLER MODE=INSTALL BRANCH=DEV")

    def test_answer_gcode(self):
        self.assertEqual(installer.answer_gcode("U"), "AFC_INSTALLER_ANSWER HEX=55")
        self.assertEqual(installer.answer_gcode("a;b #é"), "AFC_INSTALLER_ANSWER HEX=613b622023c3a9")
        self.assertEqual(installer.answer_gcode(""), "AFC_INSTALLER_ANSWER")

    def test_log_path(self):
        self.assertEqual(installer.log_path("AFC.log"), "AFC_installer.log")
        self.assertEqual(installer.log_path("klippylogs/AFC.log"), "klippylogs/AFC_installer.log")


def status(session, size=0, running=True, result="running", **more):
    out = {"available": True, "why": "", "running": running, "mode": "update", "session": session, "size": size,
           "result": result, "exit_code": None}
    out.update(more)
    return out


class Log:
    """AFC_installer.log as Moonraker serves it from a byte on."""

    def __init__(self, data=b""):
        self.data = data
        self.asked = []
        self.error = None

    def __call__(self, offset):
        self.asked.append(offset)
        if self.error:
            raise self.error
        return self.data[offset:]


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.watch = Watch()
        self.log = Log(UPDATE_MENU)

    def update(self, state, now=0.0):
        return self.watch.update(state, self.log, now)

    def test_the_running_session_from_its_log(self):
        self.watch.watch(True, 0.0)
        message = self.update(status(2, len(UPDATE_MENU)))
        self.assertEqual(self.log.asked, [0])
        self.assertEqual((message["running"], message["session"], message["mode"], message["result"]),
                         (True, 2, "update", "running"))
        self.assertEqual(message["choices"][0], ["U", "Update AFC Klipper Add-On"])
        self.assertEqual(message["lines"][-1], [["", "Enter your choice:"]])

    def test_only_what_the_log_gained_is_read(self):
        self.log.data = UPDATE_MENU[:10]
        self.update(status(2, 10))
        self.update(status(2, 10))
        self.log.data = UPDATE_MENU
        self.update(status(2, len(UPDATE_MENU)))
        self.assertEqual(self.log.asked, [0, 10])
        self.assertEqual(texts(self.watch.screen)[-1], "Enter your choice:")

    def test_sent_when_it_changes_or_after_a_while(self):
        self.assertIsNotNone(self.update(status(2, len(UPDATE_MENU)), 0.0))
        self.assertIsNone(self.update(status(2, len(UPDATE_MENU)), 1.0))
        self.assertIsNotNone(self.update(status(2, len(UPDATE_MENU), result="done", running=False), 2.0))
        self.assertIsNone(self.update(status(2, len(UPDATE_MENU), result="done", running=False), 3.0))
        self.assertIsNotNone(self.update(status(2, len(UPDATE_MENU), result="done", running=False),
                                         3.0 + installer.KEEPALIVE))

    def test_a_window_that_opens_gets_it_at_once(self):
        self.update(status(2, len(UPDATE_MENU)), 0.0)
        self.watch.watch(True, 1.0)
        self.assertIsNotNone(self.update(status(2, len(UPDATE_MENU)), 1.0))

    def test_an_unchanged_rebuild_is_not_sent(self):
        self.update(status(2, 5), 0.0)
        self.watch.note("")
        self.watch._message = None
        self.assertIsNone(self.update(status(2, 5), 1.0))

    def test_the_watch_runs_out(self):
        self.watch.watch(True, 0.0)
        self.assertTrue(self.watch.watching(installer.WATCH_FOR - 1))
        self.assertFalse(self.watch.watching(installer.WATCH_FOR))
        self.watch.watch(False, 1.0)
        self.assertFalse(self.watch.watching(1.0))

    def test_a_new_session_starts_a_new_screen(self):
        self.update(status(2, len(UPDATE_MENU)))
        self.log.data = b"fresh"
        message = self.update(status(3, 5))
        self.assertEqual(message["lines"], [[["", "fresh"]]])
        self.assertEqual(self.log.asked, [0, 0])

    def test_a_shorter_log_is_a_new_session(self):
        self.update(status(1, len(UPDATE_MENU)))
        self.log.data = b"again"
        self.assertEqual(self.update(status(1, 5))["lines"], [[["", "again"]]])

    def test_klipper_away_then_restarted(self):
        self.update(status(2, len(UPDATE_MENU)))
        away = self.update(None, 1.0)
        self.assertTrue(away["away"])
        self.assertEqual(away["choices"], [])
        self.assertIsNone(self.update(None, 2.0))
        self.log.data = UPDATE_MENU + b"Q\r\nRestarting Klipper service to apply changes...\r\n"
        message = self.update(status(0, 0, running=False, result=""), 3.0)
        self.assertEqual((message["ended"], message["running"], message["away"], message["result"],
                          message["session"]), ("restarted", False, False, "", 0))
        self.assertEqual(message["lines"][-2], [["", "Restarting Klipper service to apply changes..."]])
        # The screen stays while nothing new runs.
        self.assertIsNone(self.update(status(0, 0, running=False, result=""), 4.0))
        self.assertEqual(self.update(status(1, 5), 5.0)["ended"], "")

    def test_a_restart_with_nothing_more_in_the_log(self):
        self.update(status(2, len(UPDATE_MENU)))
        message = self.update(status(0, 0, running=False, result=""), 1.0)
        self.assertEqual(self.log.asked, [0, len(UPDATE_MENU)])
        self.assertEqual((message["ended"], message["lines"][-1]), ("restarted", [["", "Enter your choice:"]]))

    def test_a_session_that_ended_before_a_restart_keeps_its_result(self):
        self.update(status(2, len(UPDATE_MENU), running=False, result="done", exit_code=0))
        message = self.update(status(0, 0, running=False, result="", available=False, why="root"), 1.0)
        self.assertEqual((message["ended"], message["result"], message["exit_code"], message["why"]),
                         ("", "done", 0, "root"))
        self.assertEqual(self.log.asked, [0])

    def test_klipper_away_with_nothing_running(self):
        self.update(status(2, 0, running=False, result="done"))
        self.assertFalse(self.update(None, 9.0)["away"])

    def test_nothing_ran_since_klipper_started(self):
        message = self.update(status(0, 0, running=False, result=""))
        self.assertEqual((message["session"], message["lines"]), (0, [[]]))
        self.assertEqual(self.log.asked, [])

    def test_a_failed_read_is_tried_again(self):
        self.log.error = MoonrakerError("416")
        self.update(status(2, len(UPDATE_MENU)))
        self.log.error = None
        self.assertEqual(self.update(status(2, len(UPDATE_MENU)), 1.0)["lines"][-1], [["", "Enter your choice:"]])

    def test_a_start(self):
        self.update(status(2, len(UPDATE_MENU), running=False, result="done"))
        self.watch.start()
        self.assertTrue(self.update(status(2, len(UPDATE_MENU), running=False, result="done"), 1.0)["starting"])
        self.log.data = b"new"
        message = self.update(status(3, 3), 2.0)
        self.assertEqual((message["starting"], message["lines"]), (False, [[["", "new"]]]))

    def test_a_start_afc_refuses(self):
        self.watch.start()
        self.watch.note("AFC's installer cannot run while printing")
        message = self.update(status(0, 0, running=False, result=""))
        self.assertEqual((message["starting"], message["error"]), (False, "AFC's installer cannot run while printing"))
        self.watch.note("")
        self.assertEqual(self.update(status(0, 0, running=False, result=""), 1.0)["error"], "")

    def test_a_password_prompt(self):
        self.log.data = UPDATE_MENU + b"Q\r\nRestarting Klipper...\r\nPassword: "
        self.assertFalse(self.watch.asks_password())
        message = self.update(status(2, len(self.log.data)))
        self.assertTrue(self.watch.asks_password())
        self.assertEqual((message["hint"], message["choices"]), ("password", []))
        # Once the session ended, nothing waits on the prompt the screen still shows.
        message = self.update(status(2, len(self.log.data), running=False, result="stopped"), 1.0)
        self.assertEqual((message["hint"], message["choices"]), ("", []))

    def test_the_password_box_follows_afcs_terminal(self):
        self.log.data = UPDATE_MENU + b"Q\r\nRestarting Klipper...\r\nPassword: "
        message = self.update(dict(status(2, len(self.log.data)), asks_password=True))
        self.assertTrue(message["password"] and self.watch.takes_password())
        # An older AFC says nothing of its terminal, so the window offers no box.
        self.assertFalse(self.update(status(2, len(self.log.data)), 1.0)["password"])
        # Nor once Klipper stops answering, or the session ended.
        self.update(dict(status(2, len(self.log.data)), asks_password=True), 2.0)
        self.assertFalse(self.update(None, 3.0)["password"])
        message = self.update(dict(status(2, len(self.log.data), running=False, result="done"), asks_password=True),
                              4.0)
        self.assertFalse(message["password"] or self.watch.takes_password())

    def test_a_failed_session(self):
        message = self.update(status(4, 0, running=False, result="failed", exit_code=1))
        self.assertEqual((message["result"], message["exit_code"], message["choices"]), ("failed", 1, []))

    def test_odd_status(self):
        self.assertEqual(self.update({"session": None})["session"], 0)
        self.assertEqual(self.update(status(2, None))["lines"], [[]])


if __name__ == "__main__":
    unittest.main()
