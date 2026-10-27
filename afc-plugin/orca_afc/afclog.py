"""AFC.log for the Device tab's Assistant page in the AFC build of OrcaSlicer:
the log's last lines, and the errors and warnings AFC logged since they were
last cleared there.

AFC's logger (AFC_logger.py) writes each line of a message as
`HH:MM:SS <Klipper clock> [<file>:<function>():<line>] - <LEVEL>:<text>`, and
every error or warning it logs also goes into AFC's message queue, of which
Klipper's status shows the oldest. So the log's ERROR and WARN lines are the
queue's messages with their history. Clear empties the queue and remembers
the log's last line, the clear mark: only the errors and warnings after it
are listed. A tail that no longer holds the mark came after it entirely (or
the log rotated), so all of its errors and warnings are listed."""

from __future__ import annotations

import re
from dataclasses import dataclass

LOG_NAME = "AFC.log"
TAIL = 128 * 1024              # bytes of AFC.log read each time after the first
FIRST = 1 << 20                # bytes read the first time
SHOWN = 1000                   # lines other than DEBUG the page keeps
DEBUG_SHOWN = 1000             # newest lines among which it keeps the DEBUG ones
PROBLEMS = 30                  # errors and warnings it lists
WIDTH = 400                    # characters kept of a line

# The clock is padded to 10 characters, so a Klipper up for less than about
# 28 hours has more than one space before it.
_LINE = re.compile(r"^(\d\d:\d\d:\d\d) +(\d+\.\d+) (?:\[[^\]]*\] *)?- *([A-Z]+):(.*)$")
_LEVELS = {"ERROR": "e", "WARN": "w", "INFO": "i", "DEBUG": "d", "RAW": "r"}
_NAMES = {"e": "ERROR", "w": "WARN", "i": "INFO", "d": "DEBUG", "r": "RAW"}
_NUMBER = re.compile(r"\d+(\.\d+)?")


@dataclass
class Entry:
    raw: str     # the line as AFC.log has it, which the clear mark names
    time: str    # HH:MM:SS
    clock: str   # Klipper's clock, the same on every line of one message
    level: str   # e, w, i, d or r
    text: str

    def shown(self) -> str:
        return ("%s %-5s %s" % (self.time, _NAMES[self.level], self.text))[:WIDTH]


@dataclass
class Problem:
    error: bool  # an error, else a warning
    time: str    # when it was last logged
    text: str
    count: int = 1

    def to_json(self) -> dict:
        return {"type": "error" if self.error else "warning", "time": self.time, "text": self.text,
                "count": self.count}


def entries(text: str) -> list:
    """The log's lines in AFC's format. A tail starts inside a line, and a
    line in no other format is left out."""
    out = []
    for raw in text.splitlines():
        match = _LINE.match(raw)
        if match:
            level = _LEVELS.get(match.group(3), "i")
            out.append(Entry(raw, match.group(1), match.group(2), level, match.group(4).strip()))
    return out


def last_line(text: str) -> str:
    """The clear mark for a tail: its last line."""
    found = entries(text)
    return found[-1].raw if found else ""


def problems(found: list) -> list:
    """The errors and warnings among the entries, newest first: a message's
    lines joined, and one that repeats (numbers aside) counted once."""
    messages = []
    for entry in found:
        if entry.level not in ("e", "w"):
            continue
        last = messages[-1] if messages else None
        if last is not None and last[0].clock == entry.clock and last[0].level == entry.level:
            last[1].append(entry.text)
        else:
            messages.append((entry, [entry.text]))
    seen = {}
    for entry, lines in messages:
        text = "\n".join(lines)
        key = (entry.level, _NUMBER.sub("#", text))
        problem = seen.pop(key, None)
        count = problem.count + 1 if problem else 1
        seen[key] = Problem(entry.level == "e", entry.time, text, count)  # re-added: newest last
    return list(reversed(list(seen.values())))[:PROBLEMS]


def log_path(files: list) -> str:
    """AFC.log's path in Moonraker's logs root, given the root's file list
    (/server/files/list?root=logs): the newest file named AFC.log anywhere in
    it, else "" for no log. It is beside klippy.log on most printers, and in
    klippylogs/ on a Snapmaker U1, whose root can also hold an old one."""
    logs = [f for f in files if isinstance(f, dict) and isinstance(f.get("path"), str)
            and f["path"].rsplit("/", 1)[-1] == LOG_NAME]
    if not logs:
        return ""
    return max(logs, key=lambda f: f.get("modified") or 0)["path"]


def merge(kept: list, text: str) -> list:
    """The entries kept so far with a newer tail's entries added after them,
    trimmed to what the page shows: the last SHOWN lines other than DEBUG, and
    the DEBUG lines among the last DEBUG_SHOWN. A tail that no longer holds
    the last kept line came after it (or the log rotated), so all of it is
    added."""
    found = entries(text)
    if kept:
        last = kept[-1].raw
        at = next((n for n in range(len(found) - 1, -1, -1) if found[n].raw == last), None)
        found = kept + (found if at is None else found[at + 1:])
    cut = len(found) - DEBUG_SHOWN
    found = [entry for n, entry in enumerate(found) if n >= cut or entry.level != "d"]
    others = [n for n, entry in enumerate(found) if entry.level != "d"]
    if len(others) > SHOWN:
        found = found[others[-SHOWN]:]
    return found


def view(found, cleared: str, path: str = LOG_NAME) -> dict:
    """The Assistant page's agent_log for AFC.log's entries (merge()) or a tail
    of it, its clear mark and its path in Moonraker's logs root."""
    if isinstance(found, str):
        found = merge([], found)
    start = 0
    if cleared:
        start = next((n + 1 for n in range(len(found) - 1, -1, -1) if found[n].raw == cleared), 0)
    return {"name": LOG_NAME, "file": "logs/" + path,
            "lines": [[entry.level, entry.shown()] for entry in found],
            "problems": [problem.to_json() for problem in problems(found[start:])]}
