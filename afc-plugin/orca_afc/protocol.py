"""What the AFC tab reads from and sends to a printer running AFC.

AFC is Armored Turtle's Klipper filament changer add-on (Box Turtle, Night Owl,
ACE, OpenAMS, Bambu AMS through BridgeBox, ...). It exposes every unit and lane
through Klipper, so an AFC printer can have many more filaments than toolheads.

The data comes from Moonraker:
  - /printer/afc/status          AFC's webhook: units -> lanes -> status
  - /printer/objects/query       the AFC object and per-lane objects (fallback)
  - /server/database/item        namespace "lane_data", keyed by T(n) or lane name

Nothing here talks to the network or to OrcaSlicer, so all of it is unit-tested.
"""

from __future__ import annotations

import json
import math
import re
import urllib.parse
from dataclasses import dataclass, field

# ------------------------------------------------------------------ model


@dataclass
class Lane:
    name: str = ""            # Klipper lane name, e.g. "lane4"
    unit: str = ""            # unit name, e.g. "Turtle_1", "Bambu_AMS_1"
    unit_type: str = ""       # unit type, e.g. "Box_Turtle", "BambuAMS"
    index: int = 0            # 1-based position inside the unit, as AFC reports it
    maps: list = field(default_factory=list)   # T(n) commands mapped to this lane, e.g. ["T4"]
    extruder: str = ""        # AFC extruder this lane feeds, e.g. "extruder1"
    extruder_index: int = -1  # toolhead index (extruder=0, extruder1=1, ...)
    color: str = ""           # "#RRGGBB", empty when unknown
    multi_colors: list = field(default_factory=list)
    material: str = ""
    sub_type: str = ""
    vendor: str = ""
    filament_name: str = ""
    spool_id: int = 0         # Spoolman spool id, 0 = none
    weight: float = 0.0       # remaining grams
    initial_weight: float = 0.0
    density: float = 0.0
    diameter: float = 0.0
    nozzle_temp: int = 0
    bed_temp: int = 0
    prep: bool = False
    load: bool = False
    tool_loaded: bool = False
    status: str = ""          # AFC lane status text ("Ready", "Loaded", ...)
    td: str = ""              # TD-1 transmission distance, when scanned
    flow_k: float = -1.0      # pressure advance K saved on the Spoolman spool, -1 when none

    @property
    def has_filament(self) -> bool:
        """A lane holds filament when its prep or load sensor sees it."""
        return self.prep or self.load or self.tool_loaded


@dataclass
class Dryer:
    """A unit's filament dryer (ACE / ACE 2 Pro, Bambu AMS 2 Pro and AMS HT
    through BridgeBox). AFC's webhook carries none of this; it comes from the
    unit's own Klipper object (see dryer_objects_query())."""
    kind: str = ""            # "ace" or "bambu": which commands drive it
    model: str = ""           # e.g. "ACE2", "ams2", "ht"
    online: bool = True
    drying: bool = False
    rotate: bool = False      # spools turn while drying
    max_temp: float = 0.0     # hottest the unit allows, C
    target: float = 0.0       # target C while drying
    temperature: float = -1.0
    humidity: float = -1.0
    remaining_s: int = 0
    duration_s: int = 0
    state: str = ""           # the unit's own dryer state text
    note: str = ""            # why it cannot dry right now, or what it reported


@dataclass
class Unit:
    name: str = ""
    type: str = ""
    lanes: list = field(default_factory=list)  # lane names in AFC order
    dryer: Dryer | None = None


@dataclass
class U1PrintOptions:
    """The Snapmaker U1's own print-start switches, from its print_task_config
    object. Other printers have no such object, so `present` stays False."""
    present: bool = False
    bed_level: bool = False
    flow_calibrate: bool = False
    time_lapse: bool = False

    @classmethod
    def from_page(cls, value) -> "U1PrintOptions":
        """{bed_level, flow_calibrate, time_lapse_camera} as the page sends them."""
        if not isinstance(value, dict):
            return cls()
        return cls(True, bool(value.get("bed_level")), bool(value.get("flow_calibrate")),
                   bool(value.get("time_lapse_camera")))


@dataclass
class Snapshot:
    present: bool = False     # AFC answered
    version: str = ""
    spoolman: bool = False    # AFC has Spoolman configured
    spoolman_url: str = ""    # Spoolman base URL when AFC reports one
    multiple_mapping: bool = False
    current_lane: str = ""    # lane loaded in the active toolhead
    extruders: list = field(default_factory=list)
    units: list = field(default_factory=list)  # [Unit]
    lanes: list = field(default_factory=list)  # [Lane], unit order
    u1_print: U1PrintOptions = field(default_factory=U1PrintOptions)
    top_cover: bool = False   # a U1 reports its top cover (filter and exhaust fan) attached

    def find_lane(self, name: str) -> Lane | None:
        return next((lane for lane in self.lanes if lane.name == name), None)

    def find_unit(self, name: str) -> Unit | None:
        return next((unit for unit in self.units if unit.name == name), None)

    def lane_for_tool(self, tool: int) -> Lane | None:
        """Lane currently mapped to T(tool), or None."""
        key = "T%d" % tool
        return next((lane for lane in self.lanes if key in lane.maps), None)

    def max_tool(self) -> int:
        """Highest T(n) mapped to any lane, -1 when none."""
        return max((tool_number(m) for lane in self.lanes for m in lane.maps), default=-1)


# ------------------------------------------------------------------ field readers


def _str(obj: dict, key: str) -> str:
    v = obj.get(key)
    if v is None or isinstance(v, bool):
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return "%f" % v
    return ""


def _num(obj: dict, key: str, fallback: float = 0.0) -> float:
    v = obj.get(key)
    if v is None:
        return fallback
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?", v)
        if m:
            return float(m.group(0))
    return fallback


def _bool(obj: dict, key: str) -> bool:
    v = obj.get(key)
    if v is None:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    if isinstance(v, str):
        return v in ("True", "true", "1")
    return False


def _string_list(value) -> list:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str) and v]
    if isinstance(value, str):
        # AFC saves a lane map as "T0,T5" in its vars file.
        return [part for part in re.split(r"[, ]", value) if part]
    return []


def _is_unknown(s: str) -> bool:
    return not s or s in ("NONE", "None", "none")


def _int(value: float) -> int:
    """C++ int(double): truncate toward zero."""
    return int(value) if math.isfinite(value) else 0


def _round(value: float) -> int:
    """std::lround: halves away from zero (Python's round() goes to even)."""
    return int(math.floor(abs(value) + 0.5)) * (1 if value >= 0 else -1)


def tool_number(map_: str) -> int:
    """"T12" -> 12, anything else -> -1."""
    if not re.fullmatch(r"[Tt]\d{1,4}", map_ or ""):
        return -1
    return int(map_[1:])


def extruder_index_from_name(extruder: str) -> int:
    """"extruder" -> 0, "extruder3" -> 3, AFC's "e1" (U1 toolheads) -> 1, else -1."""
    extruder = extruder or ""
    if extruder.startswith("extruder"):
        rest = extruder[len("extruder"):]
        if not rest:
            return 0
    elif len(extruder) > 1 and extruder[0] == "e":
        rest = extruder[1:]
    else:
        return -1
    return int(rest) if rest.isascii() and rest.isdigit() else -1


def normalize_color(color: str) -> str:
    """"#ff00aa", "ff00aa", "FF00AAFF" -> "#FF00AA". Empty when not a color."""
    s = (color or "")
    if s.startswith("#"):
        s = s[1:]
    if len(s) not in (6, 8) or not re.fullmatch(r"[0-9A-Fa-f]+", s):
        return ""
    return "#" + s[:6].upper()


def material_key(material: str) -> str:
    """Material names AFC and Spoolman write vary in case and decoration
    ("PLA+", "pla", "PLA Basic"); compare on the leading word."""
    m = re.split(r"[ \-_]", (material or "").upper(), maxsplit=1)[0]
    return m.rstrip("+")


def is_safe_lane_name(name: str) -> bool:
    """True for names AFC accepts as a lane (letters, digits, '_' and '-')."""
    return bool(name) and len(name) <= 64 and re.fullmatch(r"[A-Za-z0-9_\-]+", name) is not None


def _safe_value(v: str, max_len: int) -> bool:
    return bool(v) and len(v) <= max_len and re.fullmatch(r"[A-Za-z0-9_\-+.]+", v) is not None


def _natural_key(s: str):
    """"lane9" < "lane10": digit runs compare as numbers."""
    return [(0, int(part), "") if part.isdigit() else (1, 0, part) for part in re.split(r"(\d+)", s) if part]


def _format_number(v: float) -> str:
    s = "%.1f" % v
    return s[:-2] if s.endswith(".0") else s


# ------------------------------------------------------------------ parsing


def _fill_lane(st: dict, lane: Lane) -> None:
    """Fill one lane from an AFC lane status object (AFC_lane.get_status())."""
    if not lane.name:
        lane.name = _str(st, "name")
    if not lane.unit:
        lane.unit = _str(st, "unit")
    lane.index = _int(_num(st, "lane", 0))
    lane.extruder = _str(st, "extruder")
    lane.extruder_index = extruder_index_from_name(lane.extruder)
    if "map" in st:
        lane.maps.extend(m for m in _string_list(st["map"]) if m != "NONE")
    lane.color = normalize_color(_str(st, "color"))
    if "multi_color_hexes" in st:
        lane.multi_colors.extend(c for c in map(normalize_color, _string_list(st["multi_color_hexes"])) if c)
    lane.material = _str(st, "material")
    lane.sub_type = _str(st, "sub_type")
    lane.vendor = _str(st, "spool_vendor")
    lane.filament_name = _str(st, "filament_name")
    lane.spool_id = _int(_num(st, "spool_id", 0))
    lane.weight = _num(st, "weight", 0)
    lane.initial_weight = _num(st, "initial_weight", 0)
    lane.density = _num(st, "density", 0)
    lane.diameter = _num(st, "diameter", 0)
    lane.nozzle_temp = _int(_num(st, "extruder_temp", 0))
    lane.bed_temp = _int(_num(st, "bed_temp", 0))
    lane.prep = _bool(st, "prep")
    lane.load = _bool(st, "load")
    lane.tool_loaded = _bool(st, "tool_loaded")
    # AFC's own "In Tool" is its extruder holding the lane. A U1 toolhead lane keeps
    # tool_loaded false after a reset or a filament swap while its extruder still holds it.
    if not lane.tool_loaded and _str(st, "filament_status").startswith("In Tool"):
        lane.tool_loaded = True
    lane.status = _str(st, "status")
    lane.td = _str(st, "td1_td")


def _read_afc_object(afc: dict, out: Snapshot) -> None:
    out.version = _str(afc, "version")
    out.current_lane = _str(afc, "current_load")
    out.multiple_mapping = _bool(afc, "multiple_tool_mapping")
    spoolman = afc.get("spoolman")
    if spoolman is not None:
        out.spoolman = (isinstance(spoolman, str) and bool(spoolman)) or spoolman is True
        if isinstance(spoolman, str) and spoolman.startswith("http"):
            out.spoolman_url = spoolman
    extruders = afc.get("extruders")
    if isinstance(extruders, list):
        out.extruders = _string_list(extruders)
    elif isinstance(extruders, dict):
        out.extruders = list(extruders.keys())


def _read_u1_print_options(status: dict, out: Snapshot) -> None:
    """The U1's print_task_config object, when the status map has one. Klipper
    answers a query for an object it lacks with null fields, so only real
    values mark a U1."""
    cfg = status.get("print_task_config")
    if not isinstance(cfg, dict) or not any(v is not None for v in cfg.values()):
        return
    out.u1_print = U1PrintOptions(True, _bool(cfg, "auto_bed_leveling"), _bool(cfg, "flow_calibrate"),
                                  _bool(cfg, "time_lapse_camera"))


def _read_top_cover(status: dict, out: Snapshot) -> None:
    """The U1 purifier's power_detected: true while the top cover is on."""
    purifier = status.get("purifier")
    if isinstance(purifier, dict) and purifier.get("power_detected") is not None:
        out.top_cover = _bool(purifier, "power_detected")


def _unwrap_status(result: dict):
    cur = result
    # AFC's webhook answers {"status:": {"AFC": {...}}} (the colon is AFC's),
    # inside Moonraker's {"result": ...} when the caller has not taken it off.
    for key in ("result", "status:", "status"):
        if isinstance(cur.get(key), dict):
            cur = cur[key]
    afc = cur.get("AFC")
    return afc if isinstance(afc, dict) else None


def _order_snapshot(out: Snapshot) -> None:
    """The webhook arrives as a JSON object, so units and lanes come out in key
    order (lane10 before lane8). Put lanes in natural order inside each unit,
    units in the order of their first lane, and lane-less units (a toolchanger
    entry, unclaimed BridgeBox spares) last."""
    for unit in out.units:
        unit.lanes.sort(key=_natural_key)
    out.units.sort(key=lambda u: (0, _natural_key(u.lanes[0])) if u.lanes else (1, _natural_key(u.name)))
    ordered = [lane for unit in out.units for name in unit.lanes for lane in out.lanes
               if lane.name == name and lane.unit == unit.name]
    if len(ordered) == len(out.lanes):
        out.lanes = ordered


def parse_status_endpoint(result) -> Snapshot | None:
    """Parse AFC's webhook answer (/printer/afc/status). Accepts the raw result
    or one wrapped in "status"/"status:" (AFC sends the latter). None when no
    AFC data is present."""
    if not isinstance(result, dict):
        return None
    afc = _unwrap_status(result)
    if afc is None:
        return None
    out = Snapshot()
    system = afc.get("system")
    if isinstance(system, dict):
        _read_afc_object(system, out)
        if not out.current_lane:
            out.current_lane = _str(system, "current_load")
    for unit_name, unit_obj in afc.items():
        if unit_name == "system" or not isinstance(unit_obj, dict):
            continue
        unit = Unit(name=unit_name)
        if isinstance(unit_obj.get("system"), dict):
            unit.type = _str(unit_obj["system"], "type")
        for lane_name, lane_obj in unit_obj.items():
            if lane_name == "system" or not isinstance(lane_obj, dict):
                continue
            lane = Lane(name=lane_name, unit=unit.name, unit_type=unit.type)
            _fill_lane(lane_obj, lane)
            lane.unit = unit.name  # the webhook groups by unit; trust the grouping
            unit.lanes.append(lane.name)
            out.lanes.append(lane)
        out.units.append(unit)
    _order_snapshot(out)
    out.present = True
    return out


def afc_object_query() -> dict:
    """Klipper object fields the webhook leaves out, plus the U1's
    print_task_config switches and whether its top cover is on. A printer
    without those objects answers them with null fields (see
    _read_u1_print_options)."""
    return {"AFC": ["multiple_tool_mapping"],
            "print_task_config": ["auto_bed_leveling", "flow_calibrate", "time_lapse_camera"],
            "purifier": ["power_detected"]}


def merge_afc_object(status, out: Snapshot) -> None:
    """Read an objects query answer for afc_object_query() into a snapshot
    parsed from the webhook. A field the answer lacks keeps its value."""
    st = status.get("status") if isinstance(status, dict) and isinstance(status.get("status"), dict) else status
    if not isinstance(st, dict):
        return
    _read_u1_print_options(st, out)
    _read_top_cover(st, out)
    afc = st.get("AFC")
    if isinstance(afc, dict) and afc.get("multiple_tool_mapping") is not None:
        out.multiple_mapping = _bool(afc, "multiple_tool_mapping")


def lane_object_names(objects_list, lane_names: list) -> list:
    """Names of the per-lane Klipper objects in an objects list answer
    ({"objects": [...]}) for the given lane names, e.g. "AFC_stepper lane1"."""
    lst = objects_list.get("objects") if isinstance(objects_list, dict) else objects_list
    if not isinstance(lst, list):
        return []
    wanted, seen, out = set(lane_names), set(), []
    for name in lst:
        if not isinstance(name, str) or not name.startswith("AFC_") or " " not in name:
            continue
        lane = name.rsplit(" ", 1)[1]
        # Lanes are registered once, as AFC_stepper/AFC_lane/AFC_BambuAMS...
        # Units and hubs share the prefix but not a lane's name.
        if lane in wanted and lane not in seen:
            seen.add(lane)
            out.append(name)
    return out


def parse_objects_status(status) -> Snapshot | None:
    """Build a snapshot from an objects query status map holding the "AFC"
    object and the per-lane objects named by lane_object_names()."""
    if not isinstance(status, dict):
        return None
    st = status["status"] if isinstance(status.get("status"), dict) else status
    afc = st.get("AFC")
    if not isinstance(afc, dict):
        return None
    out = Snapshot()
    _read_afc_object(afc, out)
    _read_u1_print_options(st, out)
    _read_top_cover(st, out)

    unit_types = {}
    for entry in _string_list(afc.get("units")):
        utype, _, name = entry.rpartition(" ")
        unit_types[name] = utype
        out.units.append(Unit(name=name, type=utype))

    by_lane = {key.rsplit(" ", 1)[1]: value for key, value in st.items()
               if key.startswith("AFC_") and " " in key and isinstance(value, dict)}
    for name in _string_list(afc.get("lanes")):
        obj = by_lane.get(name)
        if obj is None:
            continue
        lane = Lane(name=name)
        _fill_lane(obj, lane)
        lane.unit_type = unit_types.get(lane.unit, "")
        placed = False
        for unit in out.units:
            if unit.name == lane.unit:
                unit.lanes.append(name)
                placed = True
        if not placed:
            out.units.append(Unit(name=lane.unit, type=lane.unit_type, lanes=[name]))
        out.lanes.append(lane)
    # Drop units with no lanes (AFC lists only units that have lanes, but be safe).
    out.units = [u for u in out.units if u.lanes]
    out.present = True
    return out


def merge_lane_data(lane_data, out: Snapshot) -> None:
    """Fill blanks (vendor, temps, Spoolman id, extruder index, weights) from
    Moonraker's "lane_data" database namespace ({"T0": {...}} from newer AFC,
    {"lane1": {...}} from older releases). Values the live status carries stay."""
    data = lane_data.get("value") if isinstance(lane_data, dict) and isinstance(lane_data.get("value"), dict) else lane_data
    if not isinstance(data, dict):
        return
    for key, v in data.items():
        if not isinstance(v, dict):
            continue
        # Newer AFC keys each record by the T(n) it serves, older releases by lane name.
        lane = None
        for candidate in out.lanes:
            if candidate.name == key:
                lane = candidate
        if lane is None and tool_number(key) >= 0:
            for candidate in out.lanes:
                if key in candidate.maps:
                    lane = candidate
        if lane is None:
            continue
        if not lane.vendor:
            lane.vendor = _str(v, "vendor_name")
        if not lane.filament_name:
            lane.filament_name = _str(v, "name")
        if not lane.material:
            lane.material = _str(v, "material")
        if not lane.color:
            lane.color = normalize_color(_str(v, "color"))
        if lane.spool_id == 0:
            lane.spool_id = _int(_num(v, "spool_id", 0))
        if lane.nozzle_temp == 0:
            lane.nozzle_temp = _int(_num(v, "nozzle_temp", 0))
        if lane.bed_temp == 0:
            lane.bed_temp = _int(_num(v, "bed_temp", 0))
        if lane.weight == 0:
            lane.weight = _num(v, "weight", 0)
        if lane.initial_weight == 0:
            lane.initial_weight = _num(v, "initial_weight", 0)
        if not lane.td:
            lane.td = _str(v, "td")
        if lane.extruder_index < 0:
            idx = _num(v, "extruder_index", -1)
            if idx >= 0:
                lane.extruder_index = _int(idx)


# ------------------------------------------------------------------ dryers

ACE_DRYER_FIELDS = ["ace_connected", "ace_temp", "ace_humidity", "ace_dryer",
                    "ace_dryer_target", "ace_dryer_remain", "ace_dryer_duration", "ace_dry_rotate"]
BAMBU_DRYER_FIELDS = ["has_heater", "dry_max_temp", "drying", "dry_remaining", "dry_minutes",
                      "dry_target", "dry_rotate", "dry_state", "dry_error", "dry_note",
                      "dry_blocked", "temperature", "humidity", "ams_model", "bridge_online"]


def _is_ace(utype: str) -> bool:
    return utype.upper().startswith("ACE")


def _is_bambu(utype: str) -> bool:
    return utype.upper().startswith("BAMBU")


def _note_from(obj: dict, key: str) -> str:
    v = obj.get(key)
    if isinstance(v, str):
        return v
    if v is True:
        return "Drying is blocked right now"
    return ""


def dryer_objects_query(snapshot: Snapshot) -> dict:
    """Klipper objects to query for unit dryers: {"AFC_ACE2 Ace2_1": [fields]}
    for every ACE / Bambu unit with lanes. Empty when there are none."""
    q = {}
    for unit in snapshot.units:
        if not unit.lanes or not unit.type:
            continue
        if _is_ace(unit.type):
            q["AFC_%s %s" % (unit.type, unit.name)] = list(ACE_DRYER_FIELDS)
        elif _is_bambu(unit.type):
            q["AFC_%s %s" % (unit.type, unit.name)] = list(BAMBU_DRYER_FIELDS)
    return q


def merge_dryer_status(status, out: Snapshot) -> None:
    """Read an objects query answer for dryer_objects_query() into the units."""
    st = status["status"] if isinstance(status, dict) and isinstance(status.get("status"), dict) else status
    if not isinstance(st, dict):
        return
    for key, o in st.items():
        if not key.startswith("AFC_") or " " not in key or not isinstance(o, dict) or not o:
            continue
        prefix, name = key[4:].rsplit(" ", 1)
        unit = out.find_unit(name)
        if unit is None:
            continue
        d = Dryer()
        if _is_ace(prefix):
            d.kind = "ace"
            d.model = prefix
            d.online = _bool(o, "ace_connected") if "ace_connected" in o else True
            d.state = _str(o, "ace_dryer")
            state = d.state.upper()
            d.drying = bool(state) and state not in ("STOP", "STOPPED", "STOPPING", "IDLE") and "ERROR" not in state
            d.target = _num(o, "ace_dryer_target", 0)
            d.remaining_s = _int(_num(o, "ace_dryer_remain", 0))
            # ACE 2 reports the duration in seconds, the first ACE in minutes.
            duration = _num(o, "ace_dryer_duration", 0)
            d.duration_s = _int(duration * 60 if prefix.upper() == "ACE" else duration)
            d.temperature = _num(o, "ace_temp", -1)
            d.humidity = _num(o, "ace_humidity", -1)
            d.rotate = _bool(o, "ace_dry_rotate")
            d.max_temp = 55 if prefix.upper() == "ACE" else 70
            if "ERROR" in state:
                d.note = "Dryer reports " + d.state
        elif _is_bambu(prefix):
            if "has_heater" in o and not _bool(o, "has_heater"):
                continue  # AMS 1 / AMS lite: no heater
            d.kind = "bambu"
            d.model = _str(o, "ams_model")
            d.online = _bool(o, "bridge_online") if "bridge_online" in o else True
            d.drying = _bool(o, "drying")
            d.target = _num(o, "dry_target", 0)
            d.remaining_s = _int(_num(o, "dry_remaining", 0))
            d.duration_s = _int(_num(o, "dry_minutes", 0) * 60)
            d.temperature = _num(o, "temperature", -1)
            d.humidity = _num(o, "humidity", -1)
            d.rotate = _bool(o, "dry_rotate")
            d.max_temp = _num(o, "dry_max_temp", 0)
            d.state = _str(o, "dry_state")
            for k in ("dry_error", "dry_blocked", "dry_note"):
                if not d.note:
                    d.note = _note_from(o, k)
            if d.max_temp <= 0:
                continue  # no heater reported
        else:
            continue
        unit.dryer = d


class AFCError(ValueError):
    """A request from the page that cannot be turned into G-code."""


def build_dry_gcode(snapshot: Snapshot, unit_name: str, request: dict) -> str:
    """G-code to start or stop a unit's dryer. `request` holds action
    ("start"/"stop"), temp (C), minutes and rotate, checked against the unit."""
    unit = snapshot.find_unit(unit_name) if is_safe_lane_name(unit_name) else None
    if unit is None:
        raise AFCError("Unknown unit " + str(unit_name))
    if unit.dryer is None:
        raise AFCError(unit_name + " has no dryer")
    d = unit.dryer
    action = request.get("action") if isinstance(request, dict) else None
    if action == "stop":
        return ("ACE_DRY_STOP UNIT=" if d.kind == "ace" else "AFC_BAMBU_HEATER_STOP UNIT=") + unit_name
    if action != "start":
        raise AFCError("Unknown dryer action")
    temp_v, minutes_v = request.get("temp"), request.get("minutes")
    if not _is_number(temp_v) or not _is_number(minutes_v):
        raise AFCError("Drying needs a temperature and a time")
    temp, minutes = _round(float(temp_v)), _round(float(minutes_v))
    top = _int(d.max_temp) if d.max_temp > 0 else 85
    if temp < 35 or temp > top:
        raise AFCError("Temperature must be 35-%d C on %s" % (top, unit_name))
    if minutes < 1 or minutes > 48 * 60:
        raise AFCError("Drying time must be 1 minute to 48 hours")
    rotate_v = request.get("rotate", False)
    rotate = rotate_v if isinstance(rotate_v, bool) else (_is_number(rotate_v) and rotate_v != 0)
    if d.kind == "ace":
        return "ACE_DRY UNIT=%s TEMP=%d DURATION=%d ROTATE=%d" % (unit_name, temp, minutes, 1 if rotate else 0)
    return "AFC_BAMBU_HEATER_START UNIT=%s TEMP=%d TIME=%d ROTATE=%d" % (unit_name, temp, minutes, 1 if rotate else 0)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _is_integer(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


# ------------------------------------------------------------------ Spoolman


def spool_flow_k(spool) -> float:
    """Flow K (pressure advance) saved on a Spoolman spool: the "flow_k" extra
    field AFC's autocal writes. Takes the spool record, or Moonraker's Spoolman
    proxy answer around it ({"response": spool, "error": ...}). -1 when none."""
    if not isinstance(spool, dict):
        return -1.0
    if "response" in spool:
        return spool_flow_k(spool["response"])
    extra = spool.get("extra")
    if not isinstance(extra, dict):
        return -1.0
    # Spoolman keeps every extra field JSON-encoded, so a float reads "0.032".
    value = extra.get("flow_k")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return -1.0
    if not _is_number(value):
        return -1.0
    k = float(value)
    return k if math.isfinite(k) and k >= 0 else -1.0


# ------------------------------------------------------------------ page state


def to_json(s: Snapshot) -> dict:
    """Snapshot as the AFC page reads it."""
    units = []
    for u in s.units:
        dryer = None
        if u.dryer is not None:
            d = u.dryer
            dryer = {"kind": d.kind, "model": d.model, "online": d.online, "drying": d.drying,
                     "rotate": d.rotate, "max_temp": d.max_temp, "target": d.target,
                     "temperature": d.temperature, "humidity": d.humidity,
                     "remaining_s": d.remaining_s, "duration_s": d.duration_s, "state": d.state, "note": d.note}
        units.append({"name": u.name, "type": u.type, "lanes": list(u.lanes), "dryer": dryer})
    lanes = [{"name": l.name, "unit": l.unit, "unit_type": l.unit_type, "index": l.index, "maps": list(l.maps),
              "extruder": l.extruder, "extruder_index": l.extruder_index, "color": l.color,
              "multi_colors": list(l.multi_colors), "material": l.material, "sub_type": l.sub_type,
              "vendor": l.vendor, "filament_name": l.filament_name, "spool_id": l.spool_id,
              "weight": l.weight, "initial_weight": l.initial_weight, "density": l.density,
              "nozzle_temp": l.nozzle_temp, "bed_temp": l.bed_temp, "prep": l.prep, "load": l.load,
              "tool_loaded": l.tool_loaded, "has_filament": l.has_filament, "status": l.status, "td": l.td,
              "flow_k": l.flow_k if l.flow_k >= 0 else None}
             for l in s.lanes]
    u1 = s.u1_print
    return {
        "present": s.present, "version": s.version, "spoolman": s.spoolman, "spoolman_url": s.spoolman_url,
        "multiple_mapping": s.multiple_mapping, "current_lane": s.current_lane, "extruders": list(s.extruders),
        "u1_print": {"bed_level": u1.bed_level, "flow_calibrate": u1.flow_calibrate,
                     "time_lapse_camera": u1.time_lapse} if u1.present else None,
        "units": units, "lanes": lanes, "max_tool": s.max_tool(),
    }


# ------------------------------------------------------------------ G-code from the page

_EDIT_ORDER = ("spool_id", "map", "material", "color", "weight", "nozzle_temp", "bed_temp")


def build_lane_edit_gcode(snapshot: Snapshot, lane_name: str, changes: dict) -> list:
    """G-code for a lane edit from the AFC page. `changes` may hold color
    ("#RRGGBB"), material, weight (g), spool_id (0 clears), map ("T5"),
    nozzle_temp, bed_temp. Values are checked and never interpolated raw."""
    if not is_safe_lane_name(lane_name) or snapshot.find_lane(lane_name) is None:
        raise AFCError("unknown lane")
    if not isinstance(changes, dict) or not changes:
        raise AFCError("no changes")
    for key in changes:
        if key not in _EDIT_ORDER:
            raise AFCError("unsupported field " + str(key))
    lane_arg = " LANE=" + lane_name
    out = []
    # Fixed order: a Spoolman id first, since AFC then pulls color, material
    # and weight from Spoolman and the explicit values after it must win.
    for key in _EDIT_ORDER:
        if key not in changes:
            continue
        val = changes[key]
        if key == "color":
            c = normalize_color(val) if isinstance(val, str) else ""
            if not c:
                raise AFCError("color must be #RRGGBB")
            out.append("SET_COLOR" + lane_arg + " COLOR=" + c[1:])
        elif key == "material":
            if not isinstance(val, str) or not _safe_value(val, 32):
                raise AFCError("material may only use letters, digits and + - _ .")
            out.append("SET_MATERIAL" + lane_arg + " MATERIAL=" + val)
        elif key == "weight":
            if not _is_number(val) or val < 0 or val > 100000:
                raise AFCError("weight must be 0-100000 g")
            out.append("SET_WEIGHT" + lane_arg + " WEIGHT=" + _format_number(float(val)))
        elif key == "spool_id":
            if not _is_integer(val) or val < 0 or val > 99999999:
                raise AFCError("spool id must be a positive whole number")
            out.append("SET_SPOOL_ID" + lane_arg + " SPOOL_ID=" + (str(val) if val > 0 else ""))
        elif key == "map":
            m = val.upper() if isinstance(val, str) else ""
            if tool_number(m) < 0:
                raise AFCError("map must be T<number>")
            out.append("SET_MAP" + lane_arg + " MAP=" + m)
        else:  # nozzle_temp, bed_temp
            if not _is_number(val) or val < 0 or val > 500:
                raise AFCError("temperature must be 0-500")
            # AFC_SET_SPOOL_TEMP keeps the other temperature when it is left out.
            out.append("AFC_SET_SPOOL_TEMP" + lane_arg + (" EXTRUDER_TEMP=" if key == "nozzle_temp" else " BED_TEMP=")
                       + str(_round(float(val))))
    return out


def build_tool_mapping_gcode(snapshot: Snapshot, tool_to_lane: list) -> list:
    """G-code that makes each T(tool) load the given lane, using AFC's SET_MAP
    (which swaps with the lane that held the T(n) before). Pairs whose lane
    already carries the tool are skipped. Two tools on one lane need AFC's
    multiple mapping on."""
    tools, lanes = set(), set()
    max_tool = snapshot.max_tool()
    for tool, lane in tool_to_lane:
        if tool < 0 or tool > max_tool:
            raise AFCError("T%d is not a tool AFC knows (it has T0-T%d)" % (tool, max(max_tool, 0)))
        if not is_safe_lane_name(lane) or snapshot.find_lane(lane) is None:
            raise AFCError("unknown lane " + str(lane))
        if tool in tools:
            raise AFCError("T%d is mapped twice" % tool)
        tools.add(tool)
        if lane in lanes and not snapshot.multiple_mapping:
            raise AFCError(lane + " is used for two filaments; enable AFC multiple tool mapping to allow that")
        lanes.add(lane)

    # Replay SET_MAP's swap so lanes already in place are skipped.
    maps = {l.name: list(l.maps) for l in snapshot.lanes}

    def holder(key):
        return next((name for name, m in maps.items() if key in m), "")

    out = []
    for tool, lane in tool_to_lane:
        key = "T%d" % tool
        cur = holder(key)
        if cur == lane:
            continue
        out.append("SET_MAP LANE=%s MAP=%s" % (lane, key))
        if not cur:
            continue
        if snapshot.multiple_mapping:
            maps[cur] = [m for m in maps[cur] if m != key]
            maps[lane].append(key)
        else:
            maps[cur] = maps[lane]
            maps[lane] = [key]
    return out


def build_lane_action_gcode(snapshot: Snapshot, lane: str, action: str) -> str:
    """G-code for a lane action from the AFC tab:
      "load"   -> CHANGE_TOOL LANE=<lane>  (swap this lane into its toolhead)
      "unload" -> TOOL_UNLOAD LANE=<lane>  (pull it back out of the toolhead)
      "eject"  -> LANE_UNLOAD LANE=<lane>  (retract it out of the unit)
    The caller sends these only while the printer is idle."""
    l = snapshot.find_lane(lane) if is_safe_lane_name(lane) else None
    if l is None:
        raise AFCError("unknown lane")
    if action == "load":
        if not l.has_filament:
            raise AFCError(lane + " has no filament")
        return "CHANGE_TOOL LANE=" + lane
    if action == "unload":
        if not l.tool_loaded:
            raise AFCError(lane + " is not loaded in a toolhead")
        return "TOOL_UNLOAD LANE=" + lane
    if action == "eject":
        if l.tool_loaded:
            raise AFCError("unload " + lane + " from the toolhead first")
        return "LANE_UNLOAD LANE=" + lane
    raise AFCError("unknown action " + str(action))


def u1_print_options_gcode(options: U1PrintOptions) -> str:
    """G-code that sets the U1's print-start switches the way its own print
    popup does on U1 firmware 1.3.0 and later, one line per switch."""
    def line(key, on):
        return "SET_PRINT_PREFERENCE KEY=%s VALUE=%d" % (key, 1 if on else 0)
    return "\n".join([line("bed_level", options.bed_level), line("flow_calibrate", options.flow_calibrate),
                      line("time_lapse_camera", options.time_lapse)])


def u1_file_print_options_gcode(options: U1PrintOptions) -> str:
    """The line a file uploaded to a U1 carries so it starts with these
    switches however it is started later. The U1 refuses those preferences
    while a print runs unless FORCE=1 is given."""
    def flag(on):
        return "1" if on else "0"
    return "SET_PRINT_PREFERENCES BED_LEVEL=%s FLOW_CALIBRATE=%s TIME_LAPSE_CAMERA=%s FORCE=1" % (
        flag(options.bed_level), flag(options.flow_calibrate), flag(options.time_lapse))


def spoolman_for_printer(spoolman_url: str, printer_url: str) -> str:
    """Spoolman's address as this computer reaches it. AFC reports it as the
    printer sees it, so a Spoolman on the printer itself ("localhost",
    127.0.0.1) is at the printer's address, on Spoolman's own port."""
    parts = urllib.parse.urlsplit(spoolman_url)
    host = (parts.hostname or "").lower()
    if host not in ("localhost", "0.0.0.0", "::1", "::") and not host.startswith("127."):
        return spoolman_url
    printer_host = urllib.parse.urlsplit(printer_url).hostname if printer_url else ""
    if not printer_host:
        return spoolman_url
    netloc = "[%s]" % printer_host if ":" in printer_host else printer_host
    try:
        port = parts.port
    except ValueError:
        port = None
    if port:
        netloc += ":%d" % port
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def spoolman_page(spoolman_url: str, spool_id: int) -> str:
    """Spoolman's own page for a spool, or its spool list for no spool."""
    base = spoolman_url.rstrip("/")
    return base + ("/spool/show/%d" % spool_id if spool_id > 0 else "/spool")
