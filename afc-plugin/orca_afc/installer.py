"""AFC's installer and updater in a window on the Update page of the AFC
build of OrcaSlicer.

AFC (AFC_installer.py) runs the script on the printer in a terminal of its
own and writes what the terminal shows to AFC_installer.log, beside AFC.log.
While the window is open, the agent reads the log as it grows and keeps the
screen a terminal would show (Screen), which it sends to the window with the
answers the screen's prompt takes (choices()). The window's answers go to
AFC_INSTALLER_ANSWER as hex digits (answer_gcode()), since Klipper's G-code
drops what follows a ; or #. No answer goes to a password prompt, such as the
one su shows on a Snapmaker U1 to restart Klipper: G-code goes to Klipper's
log.

The script restarts Klipper when it changed AFC's files, which ends the
session with Klipper. Its log keeps what it showed, so the window reads the
rest of it once Klipper answers again, and says Klipper restarted.
"""

from __future__ import annotations

import codecs
import itertools
import re

from .moonraker import MoonrakerError

OBJECT = "AFC_installer"  # AFC's Klipper object that runs it
FIELDS = ["available", "why", "running", "mode", "session", "size", "result", "exit_code"]
SUMMARY = ["available", "why", "running", "mode"]  # what the Update page's buttons need, read at each push
LOG_NAME = "AFC_installer.log"
MODES = ("update", "install")
EVERY = 0.5          # seconds between reads while a window is open
WATCH_FOR = 15.0     # seconds a window's watch lasts; the window renews it sooner
KEEPALIVE = 5.0      # seconds after which an unchanged screen is sent again
LINES = 400          # lines the screen keeps
WIDTH = 300          # characters it keeps of a line
GCODE_TIMEOUT = 30.0
NO_PASSWORDS = "No answer is sent to a password prompt from here: G-code goes to Klipper's log."
_PENDING_MAX = 4096  # characters an unfinished escape sequence may hold before it is dropped

# A terminal's 16 colors (as VS Code's dark terminal draws them), then xterm's
# 256-color cube and grays.
_BASIC = ("#000000", "#cd3131", "#0dbc79", "#e5e510", "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
          "#666666", "#f14c4c", "#23d18b", "#f5f543", "#3b8eea", "#d670d6", "#29b8db", "#ffffff")
_LEVELS = (0, 95, 135, 175, 215, 255)
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def palette(n: int) -> str:
    """xterm's color n (0 to 255) as #rrggbb."""
    if n < 16:
        return _BASIC[n]
    if n < 232:
        n -= 16
        return "#%02x%02x%02x" % (_LEVELS[n // 36], _LEVELS[n // 6 % 6], _LEVELS[n % 6])
    gray = 8 + (n - 232) * 10
    return "#%02x%02x%02x" % (gray, gray, gray)


def _escape_end(text: str, at: int):
    """The index after the escape sequence that starts at text[at] (an ESC),
    None when the text ends inside it."""
    n = len(text)
    if at + 1 >= n:
        return None
    kind = text[at + 1]
    if kind == "[":
        for j in range(at + 2, n):
            if "\x40" <= text[j] <= "\x7e":
                return j + 1
            if not "\x20" <= text[j] <= "\x3f":
                return j  # broken off by a character no sequence holds
        return None
    if kind in "]PX^_":
        # A string to BEL (an OSC's only) or ESC \.
        for j in range(at + 2, n):
            if text[j] == "\x07" and kind == "]":
                return j + 1
            if text[j] == "\x1b":
                if j + 1 >= n:
                    return None
                if text[j + 1] == "\\":
                    return j + 2
        return None
    if kind in "()*+-./#%":
        return at + 3 if at + 2 < n else None
    return at + 2


class Screen:
    """What a terminal shows of a byte stream: its lines since the last clear,
    up to LINES, with colors and background colors. Lines are not wrapped, and
    the cursor stays on the last line, where a carriage return sends it back to
    the start: enough for a script that prints, clears and asks."""

    def __init__(self):
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._pending = ""   # an escape sequence the last feed ended inside
        self._fg = None      # None (the default), a palette index or #rrggbb
        self._bg = None      # the background, the same way
        self._bold = False
        self.clear()

    def clear(self) -> None:
        self._chars = [[]]   # each line's characters
        self._styles = [[]]  # and each one's (color, background), "" for the default
        self._col = 0

    def feed(self, data: bytes) -> None:
        text = self._pending + self._decoder.decode(data)
        self._pending = ""
        at, n = 0, len(text)
        while at < n:
            found = _CONTROL.search(text, at)
            end = found.start() if found else n
            if end > at:
                self._write(text[at:end])
            if found is None:
                return
            if text[end] != "\x1b":
                self._control(text[end])
                at = end + 1
                continue
            after = _escape_end(text, end)
            if after is None:
                if n - end < _PENDING_MAX:
                    self._pending = text[end:]
                    return
                after = end + 1  # never ends: the ESC alone is dropped
            self._escape(text[end:after])
            at = after

    def lines(self) -> list:
        """Each line as runs of [color, text], or [color, text, background]
        where the background is not the terminal's own, without the spaces on
        the terminal's background at its end. Logos drawn with half blocks
        need the background: it is the other half of each cell."""
        out = []
        for chars, styles in zip(self._chars, self._styles):
            end = len(chars)
            while end and chars[end - 1] == " " and not styles[end - 1][1]:
                end -= 1
            runs, at = [], 0
            for (color, background), group in itertools.groupby(styles[:end]):
                size = sum(1 for _ in group)
                text = "".join(chars[at:at + size])
                runs.append([color, text, background] if background else [color, text])
                at += size
            out.append(runs)
        return out

    def prompt(self) -> str:
        """The last line, where the cursor is: the question or menu prompt the
        script waits on, or the line it is writing."""
        return "".join(self._chars[-1]).strip()

    # -- the stream

    def _style(self) -> tuple:
        fg, bg = self._fg, self._bg
        if isinstance(fg, int):
            # Bold makes the 8 basic colors bright, as most terminals draw them.
            fg = palette(fg + 8 if self._bold and fg < 8 else fg)
        if isinstance(bg, int):
            bg = palette(bg)
        return (fg or "", bg or "")

    def _write(self, chunk: str) -> None:
        chars, styles = self._chars[-1], self._styles[-1]
        col = self._col
        if col < WIDTH:
            if col > len(chars):
                pad = col - len(chars)
                chars.extend(" " * pad)
                styles.extend([("", "")] * pad)
            piece = chunk[:WIDTH - col]
            chars[col:col + len(piece)] = piece
            styles[col:col + len(piece)] = [self._style()] * len(piece)
        self._col = col + len(chunk)

    def _control(self, char: str) -> None:
        if char == "\n":
            self._chars.append([])
            self._styles.append([])
            self._col = 0
            if len(self._chars) > LINES:
                del self._chars[0], self._styles[0]
        elif char == "\r":
            self._col = 0
        elif char == "\b":
            self._col = max(0, self._col - 1)
        elif char == "\t":
            self._col = (self._col // 8 + 1) * 8

    def _escape(self, sequence: str) -> None:
        if sequence == "\x1bc":  # a full reset
            self.clear()
            self._fg, self._bg, self._bold = None, None, False
            return
        if not sequence.startswith("\x1b[") or sequence[2:3] in ("?", "<", "=", ">"):
            return  # other sequences, and a terminal's private modes, show nothing
        params = [int(p) if p.isdigit() else None for p in sequence[2:-1].replace(":", ";").split(";")]
        final, first = sequence[-1], params[0] or 0
        chars, styles = self._chars[-1], self._styles[-1]
        if final == "m":
            self._sgr(params)
        elif final == "J" and first in (2, 3):
            self.clear()
        elif final in "JK" and first == 0:  # to the end of the line (the screen's, for J)
            del chars[self._col:], styles[self._col:]
        elif final == "K" and first in (1, 2):
            end = len(chars) if first == 2 else min(self._col + 1, len(chars))
            chars[:end] = " " * end
            styles[:end] = [("", "")] * end
        elif final == "G":
            self._col = max(0, first - 1)
        elif final == "C":
            self._col += max(1, first)
        elif final == "D":
            self._col = max(0, self._col - max(1, first))

    def _sgr(self, params: list) -> None:
        at = 0
        while at < len(params):
            p = params[at] or 0
            if p == 0:
                self._fg, self._bg, self._bold = None, None, False
            elif p == 1:
                self._bold = True
            elif p == 22:
                self._bold = False
            elif 30 <= p <= 37:
                self._fg = p - 30
            elif 90 <= p <= 97:
                self._fg = p - 90 + 8
            elif p == 39:
                self._fg = None
            elif 40 <= p <= 47:
                self._bg = p - 40
            elif 100 <= p <= 107:
                self._bg = p - 100 + 8
            elif p == 49:
                self._bg = None
            elif p in (38, 48):  # 38;5;n or 38;2;r;g;b, and 48 the same for the background
                kind = params[at + 1] if at + 1 < len(params) else None
                values = params[at + 2:at + (3 if kind == 5 else 5)]
                color = None
                if kind == 5 and values and values[0] is not None and values[0] < 256:
                    color = values[0]
                elif kind == 2 and len(values) == 3:
                    color = "#%02x%02x%02x" % tuple(min(255, v or 0) for v in values)
                if color is not None and p == 38:
                    self._fg = color
                elif color is not None:
                    self._bg = color
                at += 1 + (len(values) if kind in (2, 5) else 0)
            at += 1


# -- what the window shows

_OPTION = re.compile(r"^\s*([0-9]{1,2}|[A-Za-z])\.\s+(\S.*)$")
_YES_NO = re.compile(r"\(\s*y\s*/\s*n\s*\)", re.I)
_LETTERS = re.compile(r"\[\s*([A-Za-z](?:\s*/\s*[A-Za-z])+)\s*\]")
_MENU = re.compile(r"\bchoice\b", re.I)
_ENTER = re.compile(r"\bpress enter\b", re.I)
_SUDO = re.compile(r"sudo: .*(?:password is required|terminal is required)", re.I)
_PASSWORD = re.compile(r"\bpass(?:word|phrase)\b[^?]*:$", re.I)  # not a question about one


def _texts(lines: list) -> list:
    return ["".join(run[1] for run in line) for line in lines]


def _option(text: str):
    """A menu line's [answer, label]: "U. Update AFC Klipper Add-On", or
    "1. Branch : main", whose value the label leaves out."""
    found = _OPTION.match(text)
    return [found.group(1), found.group(2).split(" : ", 1)[0].strip()] if found else None


def choices(lines: list, prompt: str) -> list:
    """The answers the prompt on the screen's last line takes, as [answer,
    label] pairs for buttons. A menu's prompt ("Enter your choice") takes the
    screen's menu lines, a question with (y/n) yes or no, one with [o/s/b]
    those letters, named after the prompt's (o)verwrite words, a "Press Enter"
    prompt an empty answer, and any other prompt the list just above it, as
    when the script lists serial ports to choose from."""
    if not prompt or _PASSWORD.search(prompt):
        return []
    if _YES_NO.search(prompt):
        return [["y", "Yes"], ["n", "No"]]
    letters = _LETTERS.search(prompt)
    if letters:
        out = []
        for letter in re.split(r"\s*/\s*", letters.group(1)):
            word = re.search(r"\(%s\)(\w*)" % letter, prompt, re.I)
            out.append([letter, (letter + word.group(1)).capitalize() if word else letter])
        return out
    if _ENTER.search(prompt):
        return [["", "Continue"]]
    texts = _texts(lines)[:-1]
    if _MENU.search(prompt):
        found = [option for option in map(_option, texts) if option]
    else:
        found = []
        for text in reversed(texts):
            option = _option(text)
            if option:
                found.insert(0, option)
            elif text.strip():
                break
    seen, out = set(), []
    for answer, label in found:
        if answer.lower() not in seen:
            seen.add(answer.lower())
            out.append([answer, label])
    return out


def hint(lines: list, prompt: str = "") -> str:
    """What the window explains about the session: "password" when the
    script waits on a password prompt, "sudo" when sudo wanted a password,
    neither of which can be answered here, else ""."""
    if _PASSWORD.search(prompt):
        return "password"
    return "sudo" if any(_SUDO.search(text) for text in _texts(lines)) else ""


def summary(status) -> dict:
    """The Update page's view of the installer, from AFC's object at a push:
    {} when AFC has none (an older AFC), so the page offers no window."""
    if not isinstance(status, dict) or not isinstance(status.get("available"), bool):
        return {}
    return {"available": status["available"], "why": str(status.get("why") or ""),
            "running": status.get("running") is True, "mode": str(status.get("mode") or "")}


def answer_gcode(text: str) -> str:
    """The G-code that types `text` and Enter into the running script."""
    data = text.encode("utf-8").hex()
    return "AFC_INSTALLER_ANSWER HEX=%s" % data if data else "AFC_INSTALLER_ANSWER"


def log_path(afc_log: str) -> str:
    """AFC_installer.log's path in Moonraker's logs root: beside AFC.log's."""
    return afc_log.rsplit("/", 1)[0] + "/" + LOG_NAME if "/" in afc_log else LOG_NAME


class Watch:
    """A printer's installer session as the window shows it: the screen of its
    log, read as AFC's status says the log grew, and the message for the
    window, sent when it changes. It runs on the agent's installer thread."""

    def __init__(self):
        self.until = 0.0       # when the window's watch ends
        self.session = None    # the session the screen shows, None before the first read
        self.offset = 0        # bytes of its log read
        self.screen = Screen()
        self.state = {}        # AFC's last status of it
        self.away = False      # Klipper stopped answering while it ran
        self.ended = ""        # "restarted" when Klipper restarted while it ran
        self.error = ""        # why the window's last start, answer or stop failed
        self.starting = False  # a start was sent and its session is not read yet
        self._message = None   # the window's message, None when it needs building again
        self._sent = None
        self._sent_at = None

    def watch(self, on: bool, now: float) -> None:
        self.until = now + WATCH_FOR if on else 0.0
        self._sent_at = None  # a window that opens gets the screen at once

    def watching(self, now: float) -> bool:
        return now < self.until

    def asks_password(self) -> bool:
        """Whether the script waits on a password prompt, which no answer from
        the window goes to."""
        return bool(_PASSWORD.search(self.screen.prompt()))

    def start(self) -> None:
        """A start was sent: the screen is cleared for the session it starts."""
        self.screen = Screen()
        self.offset = 0
        self.ended = self.error = ""
        self.starting = True
        self._message = None

    def note(self, error: str) -> None:
        """What came of the window's last start, answer or stop: why it
        failed, "" when it went through."""
        if error != self.error:
            self.error = error
            self._message = None
        if error:
            self.starting = False

    def update(self, status, read, now: float):
        """Take AFC's status of the installer (None when Klipper did not answer)
        and read what the log gained with read(offset); the message for the
        window when it changed or KEEPALIVE passed, else None."""
        if isinstance(status, dict) and isinstance(status.get("session"), int):
            self._status(status, read)
        elif self.state.get("running") and not self.away:
            self.away = True
            self._message = None
        if self._message is None:
            self._message = self._build()
        changed = self._message is not self._sent and self._message != self._sent
        if changed or self._sent_at is None or now - self._sent_at >= KEEPALIVE:
            self._sent, self._sent_at = self._message, now
            return self._message
        return None

    def _status(self, status: dict, read) -> None:
        session, size = status["session"], status.get("size")
        size = size if isinstance(size, int) else 0
        if session == 0 and self.session:
            # Klipper restarted since the session on the screen, which stays: when it ran, its
            # log still has the rest of what it showed.
            if self.state.get("running"):
                self._read(read)
                self.ended = "restarted"
            status = dict(self.state, available=status.get("available"), why=status.get("why"), running=False)
            self.session, self.offset = 0, 0
        elif session != self.session or size < self.offset:
            self.session = session
            self.screen = Screen()
            self.offset = 0
            self.ended = ""
            self.starting = False
            self._message = None
        if status != self.state or self.away:
            self.state = dict(status)
            self.away = False
            self._message = None
        if session and size > self.offset:
            self._read(read)

    def _read(self, read) -> None:
        try:
            data = read(self.offset)
        except MoonrakerError:
            return
        if data:
            self.screen.feed(data)
            self.offset += len(data)
            self._message = None

    def _build(self) -> dict:
        state = self.state
        running = state.get("running") is True
        lines = self.screen.lines()
        live = running and not self.away
        exit_code = state.get("exit_code")
        return {"available": state.get("available") is True, "why": str(state.get("why") or ""),
                "running": running, "mode": str(state.get("mode") or ""), "session": self.session or 0,
                "result": "" if self.ended else str(state.get("result") or ""),
                "exit_code": None if self.ended or not isinstance(exit_code, int) else exit_code,
                "starting": self.starting, "away": self.away, "ended": self.ended, "error": self.error,
                "hint": hint(lines, self.screen.prompt() if live else ""), "lines": lines,
                "choices": choices(lines, self.screen.prompt()) if live else []}
