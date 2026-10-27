"""Lane swap times for OrcaSlicer's time estimate, from what AFC measured.

AFC keeps running sums of its load, unload and tool swap times, with their
counts, in Moonraker's afc_stats database. A sum over the printer's life also
holds every stand-alone load or unload, which AFC times from its last mark, so
one can count hours: printer averages of 108 s against 24 s per lane change
were seen. The times sent are medians of single changes instead: the ones
AFC.log names, read once a connection, and the ones the sums grow by between
two reads while connected.
"""

from __future__ import annotations

import re
import statistics

KEYS = ("load", "unload", "tool_change")
KEEP = 25            # changes a median is taken over
MIN_SAMPLES = 3      # changes known before a time is sent
LONGEST = 600.0      # seconds; anything longer is a stand-alone move timed from an old mark
LOG_TAIL = 1 << 20   # bytes of AFC.log read when connecting

_LOG = {
    "load": re.compile(r"is now loaded in toolhead t:(\d+(?:\.\d+)?)"),
    "unload": re.compile(r"unload done t:(\d+(?:\.\d+)?)"),
    "tool_change": re.compile(r"Tool swap done \(\S*?t:(\d+(?:\.\d+)?)s"),
}
# The same loads and unloads with the lane they moved, for each unit's own times.
_LANE_LOG = {
    "load": re.compile(r"(\S+) is now loaded in toolhead t:(\d+(?:\.\d+)?)"),
    "unload": re.compile(r"Lane (\S+) unload done t:(\d+(?:\.\d+)?)"),
}


def log_samples(text: str) -> dict:
    """{key: [seconds]} of the changes AFC.log names, oldest first."""
    return {key: [float(v) for v in pattern.findall(text or "")] for key, pattern in _LOG.items()}


def lane_samples(text: str) -> dict:
    """{lane: {"load": [seconds], "unload": [seconds]}} of the changes AFC.log
    names, oldest first, without the stand-alone ones timed from an old mark."""
    out = {}
    for key, pattern in _LANE_LOG.items():
        for lane, value in pattern.findall(text or ""):
            if 0 < float(value) <= LONGEST:
                out.setdefault(lane, {"load": [], "unload": []})[key].append(float(value))
    return out


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def totals(stats):
    """{key: (seconds, count)} from Moonraker's afc_stats namespace, or None
    while AFC keeps its older halving averages, which no count goes with.
    The counts are summed over the extruders: "extruder" keeps its loads and
    unloads under change_count, the others under cut, and all keep their
    tool pickups under change_count."""
    value = stats.get("value") if isinstance(stats, dict) and isinstance(stats.get("value"), dict) else stats
    if not isinstance(value, dict) or not isinstance(value.get("average_time"), dict):
        return None
    average = value["average_time"]
    if int(_number(average.get("new_average_calc"))) != 1:
        return None
    load = unload = selected = 0.0
    for entry in value.values():
        if not isinstance(entry, dict) or not isinstance(entry.get("change_count"), dict):
            continue
        change = entry["change_count"]
        moves = change if "tool_load" in change else entry.get("cut") if isinstance(entry.get("cut"), dict) else {}
        load += _number(moves.get("tool_load"))
        unload += _number(moves.get("tool_unload"))
        selected += _number(change.get("tool_selected"))
    return {"load": (_number(average.get("tool_load")), load),
            "unload": (_number(average.get("tool_unload")), unload),
            "tool_change": (_number(average.get("tool_swap")), selected)}


class Tracker:
    """The last KEEP changes of each kind for one printer."""

    def __init__(self):
        self.samples = {key: [] for key in KEYS}
        self.seeded = False
        self.lanes = {}  # lane_samples() of AFC.log, read once a connection
        self._last = {}

    def _add(self, key: str, values) -> None:
        kept = self.samples[key]
        kept.extend(v for v in values if 0 < v <= LONGEST)
        del kept[:-KEEP]

    def seed(self, log_text: str) -> None:
        self.seeded = True
        self.lanes = lane_samples(log_text)
        for key, values in log_samples(log_text).items():
            self._add(key, values)

    def update(self, stats) -> None:
        now = totals(stats)
        if now is None:
            self._last = {}
            return
        for key, (seconds, count) in now.items():
            last = self._last.get(key)
            if last is None or count < last[1] or seconds < last[0]:
                self._last[key] = (seconds, count)  # first read, or AFC_RESET_STATS
            elif count > last[1] and seconds > last[0]:
                self._add(key, [(seconds - last[0]) / (count - last[1])])
                self._last[key] = (seconds, count)
            # One grown without the other: AFC writes the time and the count apart, so wait for both.

    def times(self) -> dict:
        """{key: seconds} for the kinds with enough changes known."""
        return {key: round(statistics.median(values), 1)
                for key, values in self.samples.items() if len(values) >= MIN_SAMPLES}
