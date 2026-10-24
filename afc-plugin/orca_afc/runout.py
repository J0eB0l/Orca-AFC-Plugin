"""AFC's infinite spool: a lane may name a runout lane, which AFC loads in its
place when it runs out mid-print (SET_RUNOUT LANE= RUNOUT=), into another
toolhead if that lane feeds one. Lanes link up into chains. The AFC page and
the Device tab of the AFC build of OrcaSlicer set them one lane at a time,
link every set of loaded lanes with the same filament, or clear them all.

AFC_RESET_MAPPING clears every runout lane unless it is given RUNOUT=no, so a
printer whose print end runs it keeps runout lanes for one print only. That is
why AMS filament backup (auto_links()) links matching spools when a print is
sent, for the lanes that print uses."""

from __future__ import annotations

import math

from .protocol import AFCError, Lane, Snapshot, _natural_key, is_safe_lane_name, material_key, normalize_color

SAME_COLOR = 60.0  # RGB distance under which two spools count as one color


def _rgb(color: str):
    c = normalize_color(color)
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)) if c else None


def color_distance(a: str, b: str) -> float:
    """How far apart two "#RRGGBB" colors are, infinite when either is unknown."""
    x, y = _rgb(a), _rgb(b)
    return math.dist(x, y) if x and y else math.inf


def same_filament(a: Lane, b: Lane) -> bool:
    """Two loaded lanes with the same material and color: what Link matching
    spools chains, and what the drop-downs list first."""
    if not a.has_filament or not b.has_filament or not material_key(a.material):
        return False
    return material_key(a.material) == material_key(b.material) and color_distance(a.color, b.color) <= SAME_COLOR


def chains(snapshot: Snapshot) -> list:
    """Each chain of runout lanes, as the lanes AFC goes through in order,
    from a lane no other lane runs out onto."""
    lanes = {lane.name: lane for lane in snapshot.lanes}
    into = {lane.runout_lane for lane in snapshot.lanes}
    out = []
    for lane in snapshot.lanes:
        if not lane.runout_lane or lane.name in into:
            continue
        chain = [lane]
        while chain[-1].runout_lane in lanes and lanes[chain[-1].runout_lane] not in chain:
            chain.append(lanes[chain[-1].runout_lane])
        out.append(chain)
    return out


def _loops(runouts: dict) -> list:
    """The lanes whose runout lanes lead back round to them."""
    out = []
    for start in runouts:
        name, seen = runouts.get(start, ""), set()
        while name and name not in seen:
            if name == start:
                out.append(start)
                break
            seen.add(name)
            name = runouts.get(name, "")
    return out


def links_gcode(snapshot: Snapshot, links: list) -> list:
    """SET_RUNOUT for each (lane, runout lane) pair that changes something,
    "" for none. Refused when a lane is unknown or names itself, or when the
    links would lead a lane back round to itself: AFC would then load an empty
    lane and pause."""
    runouts = {lane.name: lane.runout_lane for lane in snapshot.lanes}
    for name, runout in links:
        if not is_safe_lane_name(name) or name not in runouts:
            raise AFCError("unknown lane %s" % name)
        if runout and (not is_safe_lane_name(runout) or runout not in runouts):
            raise AFCError("unknown runout lane %s" % runout)
        if runout == name:
            raise AFCError("%s cannot run out onto itself" % name)
        runouts[name] = runout
    loops = _loops(runouts)
    if loops:
        raise AFCError("%s would run out back onto itself" % loops[0])
    out = []
    for name, runout in links:
        if snapshot.find_lane(name).runout_lane != runout:
            out.append("SET_RUNOUT LANE=%s RUNOUT=%s" % (name, runout or "NONE"))
    return list(dict.fromkeys(out))


def _order(lane: Lane):
    # The lane in a toolhead runs out first, then the emptiest; lanes AFC has no weight for last.
    return (not lane.tool_loaded, lane.weight <= 0, lane.weight, _natural_key(lane.name))


def _matching(snapshot: Snapshot) -> list:
    """Each set of two or more loaded lanes with the same filament."""
    groups = []
    for lane in snapshot.lanes:
        group = next((g for g in groups if same_filament(g[0], lane)), None)
        if group is None:
            groups.append([lane])
        else:
            group.append(lane)
    return [group for group in groups if len(group) > 1]


def matching_links(snapshot: Snapshot) -> list:
    """(lane, runout lane) pairs that chain each set of loaded lanes with the
    same filament: the lane in a toolhead first, then the emptiest. The last
    lane of a chain keeps its own runout lane unless it would lead back into
    the chain. Lanes with no match keep theirs."""
    links = []
    runouts = {lane.name: lane.runout_lane for lane in snapshot.lanes}
    for group in _matching(snapshot):
        group.sort(key=_order)
        for lane, after in zip(group, group[1:]):
            links.append((lane.name, after.name))
            runouts[lane.name] = after.name
        last = group[-1].name
        if last in _loops(runouts):
            links.append((last, ""))
            runouts[last] = ""
    return links


def clear_links(snapshot: Snapshot) -> list:
    """(lane, "") for every lane with a runout lane."""
    return [(lane.name, "") for lane in snapshot.lanes if lane.runout_lane]


def auto_links(snapshot: Snapshot, used, made: dict) -> tuple:
    """AMS filament backup for a print about to start on the lanes `used`:
    the (lane, runout lane) pairs to set, and the links it then made, kept for
    the next print as `made` was. A link it made comes off once its spools no
    longer match. Each set of loaded lanes with the same filament that holds a
    used lane is chained, the used lanes first, then the emptiest spares,
    through the lanes with no runout lane or one it made. A runout lane set by
    hand stays: a used lane with one is left out of the chain, and a spare
    with one ends it unless it runs out onto the next."""
    runouts = {lane.name: lane.runout_lane for lane in snapshot.lanes}
    # Its own links are those still as it set them: a reset or a hand changed the rest.
    mine = {lane: to for lane, to in made.items() if to and runouts.get(lane) == to}
    for name, to in list(mine.items()):
        spare = snapshot.find_lane(to)
        if spare is None or not same_filament(snapshot.find_lane(name), spare):
            runouts[name] = ""
            del mine[name]
    used = set(used)
    for group in _matching(snapshot):
        if not used & {lane.name for lane in group}:
            continue
        for lane in group:  # its own links in the set are made again
            if mine.pop(lane.name, None):
                runouts[lane.name] = ""
        # A lane of the print with a runout lane set by hand keeps it, out of the chain.
        chain = [lane for lane in group if not (lane.name in used and runouts[lane.name])]
        if not used & {lane.name for lane in chain}:
            continue
        # A spare in a toolhead now comes out when the print loads its own lanes.
        chain.sort(key=lambda lane: (lane.name not in used, lane.name in used and not lane.tool_loaded,
                                     lane.weight <= 0, lane.weight, _natural_key(lane.name)))
        into = ""  # the lane the chain so far runs out onto
        for lane, after in zip(chain, chain[1:]):
            if not runouts[lane.name] and (lane.name in used or lane.name == into):
                runouts[lane.name] = after.name
                if lane.name in _loops(runouts):  # a runout lane set by hand leads back
                    runouts[lane.name] = ""
                else:
                    mine[lane.name] = after.name
            into = runouts[lane.name]
    links = [(lane.name, runouts[lane.name]) for lane in snapshot.lanes if runouts[lane.name] != lane.runout_lane]
    return links, mine
