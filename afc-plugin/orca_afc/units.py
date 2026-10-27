"""How long each AFC unit takes for a lane change, for the AFC build of
OrcaSlicer's suggested spool layout, which puts the filaments loaded most
often in the quickest units.

A unit's time is the median load plus the median unload of its lanes in
AFC.log. A unit with too few of either in the log takes the time of its kind
from DEFAULT_ORDER instead, quickest first: BoxTurtle, EMU, Bambu's AMS 2 Pro
and AMS HT, ACE 2, the other Bambu units, OpenAMS, the rest, and last the ACE
(V1), which is slow enough that its hub is staged near the toolhead. The
printer's own Preferred units list, set in OrcaSlicer, goes before both.
"""

from __future__ import annotations

import statistics

from .protocol import Snapshot, Unit
from .swaptimes import MIN_SAMPLES

# Unit kinds by AFC unit type, quickest first; any other type ranks as "other".
DEFAULT_ORDER = ("boxturtle", "emu", "bambu_dryer", "ace2", "bambu", "openams", "other", "ace")
# The time each kind takes for a lane change when the log has too few: only
# the order matters among them, and the size beside measured times.
FIRST_SECONDS, STEP_SECONDS = 60.0, 5.0


def kind(unit: Unit) -> str:
    """The DEFAULT_ORDER kind of a unit, from its AFC type ("Box_Turtle",
    "AFC_ACE2") and, for a Bambu unit, its dryer (AMS 2 Pro, AMS HT)."""
    name = unit.type.upper().replace("_", "")
    if name.startswith("AFC"):
        name = name[3:]
    if name.startswith("BAMBU"):
        return "bambu_dryer" if unit.dryer is not None else "bambu"
    for key in ("BOXTURTLE", "EMU", "ACE2", "OPENAMS", "ACE"):
        if name == key:
            return key.lower()
    return "other"


def default_seconds(unit: Unit) -> float:
    return FIRST_SECONDS + STEP_SECONDS * DEFAULT_ORDER.index(kind(unit))


def unit_times(snapshot: Snapshot, lanes: dict) -> list:
    """[{name, type, seconds, measured}] for every AFC unit with lanes, from
    swaptimes.lane_samples() of AFC.log; measured says the log gave the time."""
    out = []
    for unit in snapshot.units:
        if not unit.lanes:
            continue
        loads = [v for lane in unit.lanes for v in lanes.get(lane, {}).get("load", [])]
        unloads = [v for lane in unit.lanes for v in lanes.get(lane, {}).get("unload", [])]
        measured = len(loads) >= MIN_SAMPLES and len(unloads) >= MIN_SAMPLES
        seconds = statistics.median(loads) + statistics.median(unloads) if measured else default_seconds(unit)
        out.append({"name": unit.name, "type": unit.type, "seconds": round(seconds, 1), "measured": measured})
    return out
