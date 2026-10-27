"""The AFC build of OrcaSlicer's suggested spool layout, written back to AFC.

OrcaSlicer suggests which lane each filament of a print on several toolheads
goes in, and once the spools are moved, "Sync layout to AFC" sends the moves
(afc_layout): each lane a spool went to, the T number it keeps, so the
project's filament slots stay as they are, and what the spool's old lane knew
of it, so nothing has to be scanned or typed in again. A lane that knows a
spool it did not know when OrcaSlicer suggested the layout read it from a tag
(or a scan) and keeps it; it only takes the T number.
"""

from __future__ import annotations

from .protocol import AFCError, Snapshot, build_lane_edit_gcode, is_safe_lane_name, normalize_color

_SPOOL_KEYS = ("spool_id", "material", "color", "weight", "nozzle_temp", "bed_temp")


def _signature(spool_id, material, color) -> tuple:
    return (int(spool_id or 0), str(material or "").upper(), normalize_color(str(color or "")))


def apply_gcode(snapshot: Snapshot, moves) -> tuple:
    """(G-code lines, {lane: outcome}) for the moves: [{lane, tool, spool_id,
    material, color, weight, nozzle_temp, bed_temp, was: {spool_id, material,
    color}}], `was` being what `lane` knew when the layout was suggested.
    Outcomes: "written", "kept" (it read its spool itself) and "empty" (no
    spool in it yet, left alone)."""
    if not isinstance(moves, list) or not moves:
        raise AFCError("no moves")
    outcome, writes, maps = {}, [], []
    for move in moves:
        name = move.get("lane") if isinstance(move, dict) else None
        tool = move.get("tool") if isinstance(move, dict) else None
        lane = snapshot.find_lane(name) if isinstance(name, str) and is_safe_lane_name(name) else None
        if lane is None or not isinstance(tool, int) or isinstance(tool, bool) or tool < 0:
            raise AFCError("bad move")
        if not lane.has_filament:
            outcome[lane.name] = "empty"
            continue
        was = move.get("was") if isinstance(move.get("was"), dict) else {}
        now = _signature(lane.spool_id, lane.material, lane.color)
        if now != _signature(0, "", "") and now != _signature(was.get("spool_id"), was.get("material"), was.get("color")):
            outcome[lane.name] = "kept"
        else:
            # Unknown values stay off: no spool, weight or temperature to set.
            writes.append((lane, {key: move[key] for key in _SPOOL_KEYS if move.get(key) not in (None, 0, "", -1)}))
            outcome[lane.name] = "written"
        if "T%d" % tool not in lane.maps:
            maps.append((lane.name, "T%d" % tool))
    # AFC refuses a spool another lane holds: a lane letting go of a moved spool clears it first,
    # unless that lane read the spool itself, which wins.
    kept = {name for name, result in outcome.items() if result == "kept"}
    lines = []
    for lane, changes in writes:
        holders = [other.name for other in snapshot.lanes
                   if other is not lane and other.spool_id > 0 and other.spool_id == changes.get("spool_id")]
        if any(name in kept for name in holders):
            del changes["spool_id"]
        else:
            lines.extend("SET_SPOOL_ID LANE=%s SPOOL_ID=" % name for name in holders)
    for lane, changes in writes:
        if changes:
            lines.extend(build_lane_edit_gcode(snapshot, lane.name, changes))
    # A T number set on a lane swaps with the lane that had it; no later line asks for a T number
    # given here, so each lane keeps its own.
    lines.extend("SET_MAP LANE=%s MAP=%s" % pair for pair in maps)
    return lines, outcome
