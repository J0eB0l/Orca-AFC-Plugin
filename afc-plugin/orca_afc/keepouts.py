"""The spots on the bed AFC's macros use: where AFC_POOP purges, the path
AFC_KICK pushes the purged filament along and the brush AFC_BRUSH wipes the
nozzle on, as full-height areas in OrcaSlicer's bed_exclude_volumes format
("XxY, XxY, ... | ..."). The AFC build of OrcaSlicer keeps parts off them when
"Keep parts off AFC's spots" is ticked (afc_keepouts in push_status). The cutter
path counts too (AFC_CUT moves to the pin and pushes into it at the print's
height).

Each macro reads its _AFC_<NAME>_VARS, updated by _AFC_<NAME>_VARS_<extruder>
for that extruder, so every set of variables gives a spot. A spot is left out
when AFC.cfg turns the macro off (poop, kick, wipe, tool_cut) or its location is still
AFC's placeholder (-99), and is clipped to the axes' range from 0 up, where
OrcaSlicer's bed is."""

import re

MARGIN = 15.0        # mm around the purge spot and the kick path: the blob and the nozzle around it
BRUSH_MARGIN = 5.0   # mm around the brush
CUT_MARGIN = 10.0    # mm around the cutter path: the nozzle and the cutter lever

MACROS = ("POOP", "KICK", "BRUSH", "CUT_TIP")
FLAGS = {"POOP": "poop", "KICK": "kick", "BRUSH": "wipe", "CUT_TIP": "tool_cut"}  # AFC.cfg's switch for each
_NAME = re.compile(r"gcode_macro _AFC_(POOP|KICK|BRUSH|CUT_TIP)_VARS(_\S+)?", re.IGNORECASE)
_DIRECTIONS = {"left": (-1, 0), "right": (1, 0), "front": (0, -1), "back": (0, 1)}


def macro_objects(names) -> list:
    """The Klipper objects holding the spots' variables, from /printer/objects/list."""
    return [name for name in names or () if isinstance(name, str) and _NAME.fullmatch(name)]


def _point(value, count=2):
    if not isinstance(value, (list, tuple)) or len(value) < count:
        return None
    try:
        point = [float(v) for v in value[:count]]
    except (TypeError, ValueError):
        return None
    # AFC's placeholder, and the macros' own default when the variable is missing.
    if any(v == -99 for v in point[:2]) or point[:2] == [-1.0, -1.0]:
        return None
    return point


def _number(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _box(x0, y0, x1, y1, margin):
    return (min(x0, x1) - margin, min(y0, y1) - margin, max(x0, x1) + margin, max(y0, y1) + margin)


def spot(kind: str, variables: dict):
    """The (xmin, ymin, xmax, ymax) one macro's variables cover, None when unset."""
    if kind == "POOP":
        at = _point(variables.get("purge_loc_xy"))
        return _box(at[0], at[1], at[0], at[1], MARGIN) if at else None
    if kind == "KICK":
        at = _point(variables.get("kick_start_loc"))
        step = _DIRECTIONS.get(str(variables.get("kick_direction") or "").lower())
        if not at or not step:
            return None
        dist = _number(variables.get("kick_move_dist"), 45.0)
        return _box(at[0], at[1], at[0] + step[0] * dist, at[1] + step[1] * dist, MARGIN)
    if kind == "CUT_TIP":
        # AFC_CUT parks pin_park_dist before the pin, then pushes cut_move_dist past it in cut_direction.
        at = _point(variables.get("pin_loc_xy")) or _point([variables.get("pin_loc_x", -99), variables.get("pin_loc_y", -99)])
        step = _DIRECTIONS.get(str(variables.get("cut_direction") or "").lower())
        if not at or not step:
            return None
        park = _number(variables.get("pin_park_dist"), 6.0)
        cut = _number(variables.get("cut_move_dist"), 0.0)
        return _box(at[0] - step[0] * park, at[1] - step[1] * park, at[0] + step[0] * cut, at[1] + step[1] * cut,
                    CUT_MARGIN)
    if kind == "BRUSH":
        at = _point(variables.get("brush_loc"))
        if not at:
            return None
        half_x = _number(variables.get("brush_width"), 30.0) / 2
        half_y = _number(variables.get("brush_depth"), 10.0) / 2
        return _box(at[0] - half_x, at[1] - half_y, at[0] + half_x, at[1] + half_y, BRUSH_MARGIN)
    return None


def _clip(box, axis_max):
    x0, y0, x1, y1 = box
    x0, y0 = max(x0, 0.0), max(y0, 0.0)
    if axis_max:
        x1, y1 = min(x1, axis_max[0]), min(y1, axis_max[1])
    return (x0, y0, x1, y1) if x1 - x0 >= 1.0 and y1 - y0 >= 1.0 else None


def _volume(box) -> str:
    x0, y0, x1, y1 = ("%g" % round(v, 1) for v in box)
    return "%sx%s, %sx%s, %sx%s, %sx%s" % (x0, y0, x1, y0, x1, y1, x0, y1)


def bed_keepouts(status: dict, afc_settings: dict, axis_max=None) -> str:
    """AFC's spots from Klipper's status of macro_objects() (`status`), AFC.cfg's
    [AFC] settings and the axes' maximum (x, y), "" when there are none."""
    afc_settings = afc_settings if isinstance(afc_settings, dict) else {}
    base = {}
    for name, variables in (status or {}).items():
        match = _NAME.fullmatch(name) if isinstance(name, str) else None
        if match and not match.group(2) and isinstance(variables, dict):
            base[match.group(1).upper()] = variables
    boxes = []
    for name in sorted(status or {}):
        match = _NAME.fullmatch(name) if isinstance(name, str) else None
        variables = (status or {}).get(name)
        if not match or not isinstance(variables, dict):
            continue
        kind = match.group(1).upper()
        if not afc_settings.get(FLAGS[kind]):
            continue
        merged = dict(base.get(kind, {}), **variables) if match.group(2) else variables
        box = spot(kind, merged)
        box = _clip(box, axis_max) if box else None
        if box:
            boxes.append(box)
    # A spot inside another (the purge spot at the start of the kick path) adds nothing.
    inside = lambda a, b: a[0] >= b[0] and a[1] >= b[1] and a[2] <= b[2] and a[3] <= b[3]
    kept = [box for i, box in enumerate(boxes)
            if not any(inside(box, other) and (box != other or j < i) for j, other in enumerate(boxes) if j != i)]
    return " | ".join(_volume(box) for box in kept)
