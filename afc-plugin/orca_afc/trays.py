"""AFC's lanes as the AMS trays OrcaSlicer's device model reads.

OrcaSlicer keeps a printer's filaments in Bambu's AMS shape: units of up to
four trays (ids 0-7, a tray id being unit * 4 + slot) and one-tray AMS HT units
(ids 128-135, a tray id being the unit id). Two layouts fit AFC's T numbers to it:

- By T number, as OrcaSlicer's own Moonraker agent lays out AFC's lane data:
  T(n) is tray n, in unit n // 4, slot n % 4. Filament sync then puts tray n in
  project slot n + 1, and the print dialog's tray for a filament is the T
  number it prints with.
- By AFC unit, when OrcaSlicer syncs each tray to the slot of the T number the
  tray reports (the AFC build says so in its pushall): each unit's lanes in
  its own AMS, four to one, and a unit with one lane, or a toolchanger's tool,
  an AMS HT. A unit with a dryer is an AMS 2 Pro (one lane: an AMS HT), which
  shows its humidity and takes OrcaSlicer's drying popup; one without is an AMS
  that shows no humidity. A Bambu AMS Lite of four lanes is drawn as one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .protocol import Dryer, Lane, Snapshot, tool_number

# OrcaSlicer reads the tray presence bits with stol(), which a bit past 30
# overflows on Windows (32-bit long), and the bit test itself shifts an int.
MAX_TRAY = 30
NO_TRAY = 0xFF
# An AMS HT's bits sit at 4 + n (unit) and 16 + n (tray) for unit id 128 + n,
# so four-tray units beside them keep ids 0-3.
HT_ID, MAX_HT = 128, 8
MAX_BOXES, MAX_BOXES_WITH_HT = 7, 4
# AMS types in a unit's "info": an AMS, an AMS Lite, an AMS 2 Pro, an AMS HT.
AMS, AMS_LITE, AMS_2_PRO, AMS_HT = 1, 2, 3, 4
LITE = "lite"  # a Bambu unit's ams_model for an AMS Lite
DRYING, HEATING = 2, 1  # the dry status and sub status OrcaSlicer reads in "info"
DRY_DISCONNECTED = 100  # the AFC build of OrcaSlicer's "cannot dry" reason for a unit not connected
TOOLCHANGER = "Toolchanger"  # AFC's unit type for a toolchanger's tools


@dataclass
class Box:
    """A unit OrcaSlicer shows: up to four trays, or one for an AMS HT."""
    ams_id: int
    tools: list = field(default_factory=list)  # the T number in each slot
    unit: str = ""                             # the AFC unit it shows ("" by T number)

    @property
    def single(self) -> bool:
        return self.ams_id >= HT_ID


class Layout:
    """Which unit and slot OrcaSlicer shows each T number in, and back."""

    def __init__(self, boxes: list, by_unit: bool = False, aliases: dict | None = None):
        self.boxes, self.by_unit = boxes, by_unit
        self._at = {tool: (box, slot) for box in boxes for slot, tool in enumerate(box.tools)}
        # A T number a lane holds beside the one its tray shows lives in that tray.
        for alias, tool in (aliases or {}).items():
            if tool in self._at:
                self._at[alias] = self._at[tool]

    def box(self, ams_id) -> Box | None:
        return next((box for box in self.boxes if box.ams_id == ams_id), None)

    def tool(self, ams_id, slot_id) -> int:
        """The T number in a unit's slot, -1 for none."""
        if not _is_int(ams_id) or not _is_int(slot_id) or not 0 <= slot_id < 4 or ams_id < 0:
            return -1
        if not self.by_unit:
            tray = ams_id * 4 + slot_id
            return tray if tray <= MAX_TRAY else -1
        box = self.box(ams_id)
        return box.tools[slot_id] if box is not None and slot_id < len(box.tools) else -1

    def tool_of_tray(self, tray) -> int:
        """The T number of a tray id, -1 for none."""
        if not _is_int(tray) or tray < 0:
            return -1
        return self.tool(tray, 0) if HT_ID <= tray < HT_ID + MAX_HT else self.tool(tray // 4, tray % 4)

    def tray(self, tool: int) -> int:
        """The tray id OrcaSlicer knows T(tool) by, NO_TRAY for none."""
        at = self._at.get(tool)
        if at is None:
            return NO_TRAY
        box, slot = at
        return box.ams_id if box.single else box.ams_id * 4 + slot


def _is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def lanes_by_tray(snapshot: Snapshot) -> dict:
    """T number -> the lane AFC maps to it, for T0 to T30."""
    out = {}
    for lane in snapshot.lanes:
        for m in lane.maps:
            tool = tool_number(m)
            if 0 <= tool <= MAX_TRAY and tool not in out:
                out[tool] = lane
    return out


def layout(snapshot: Snapshot | None, by_unit: bool = False) -> Layout:
    """The layout of AFC's T numbers: by AFC unit when asked and it fits
    OrcaSlicer's units, else by T number."""
    trays = lanes_by_tray(snapshot) if snapshot is not None else {}
    if by_unit and trays:
        fit = _unit_boxes(snapshot, trays)
        if fit is not None:
            return Layout(fit[0], True, fit[1])
    last = max(trays) if trays else -1
    return Layout([Box(unit, [unit * 4 + slot for slot in range(min(3, last - unit * 4) + 1)])
                   for unit in range(last // 4 + 1)])


def _unit_boxes(snapshot: Snapshot, trays: dict):
    """Each AFC unit's lanes, in lane order, as OrcaSlicer units, and the T
    numbers that are aliases of a shown one; None when they do not fit.

    A lane is one tray, under its first T number in AFC's map order. The
    others it holds (a print maps its filaments onto lanes that way, T0 onto
    lane14 beside its own T14) are aliases: as trays they would split the unit
    in two and could leave too many units to lay out by unit."""
    own = {}  # lane name -> the T number its tray shows
    for tool in sorted(trays):
        lane = trays[tool]
        if lane.name not in own:
            own[lane.name] = next(t for t in map(tool_number, lane.maps) if trays.get(t) is lane)
    groups = {}  # AFC unit -> its lanes' T numbers, its units first and in lane order
    for unit in snapshot.units:
        for name in unit.lanes:
            if name in own and trays[own[name]].unit == unit.name:
                groups.setdefault(unit.name, []).append(own[name])
    for tool in sorted(own.values()):
        if not any(tool in tools for tools in groups.values()):
            groups.setdefault(trays[tool].unit, []).append(tool)
    boxes, singles = [], []
    for name, tools in groups.items():
        unit = snapshot.find_unit(name)
        if len(tools) == 1 or (unit is not None and unit.type == TOOLCHANGER):
            singles += [(tool, name) for tool in tools]
        else:
            boxes += [(tools[i:i + 4], name) for i in range(0, len(tools), 4)]
    if len(singles) > MAX_HT or len(boxes) > (MAX_BOXES_WITH_HT if singles else MAX_BOXES):
        return None
    aliases = {tool: own[lane.name] for tool, lane in trays.items() if own[lane.name] != tool}
    return ([Box(i, tools, name) for i, (tools, name) in enumerate(boxes)] +
            [Box(HT_ID + i, [tool], name) for i, (tool, name) in enumerate(sorted(singles))]), aliases


def _color(hex_color: str) -> str:
    """"#RRGGBB" -> "RRGGBBFF", OrcaSlicer's tray color; "" for no color."""
    return hex_color[1:].upper() + "FF" if hex_color else ""


def remaining(lane: Lane) -> int:
    """Share of the spool left, 0-100, or -1 when AFC has no weight for it."""
    if not lane.has_filament or lane.weight <= 0:
        return -1
    initial = lane.initial_weight if lane.initial_weight > 0 else 1000.0
    return max(0, min(100, int(round(lane.weight / initial * 100))))


def tray_json(slot: int, lane: Lane | None, filament_id: str, extra: dict | None = None, tool: int = -1) -> dict:
    """A tray of the "ams" object, T(tool). `extra` adds the spool and preset
    fields the agent works out for a loaded tray (see spool_json())."""
    tray = {"id": str(slot), "tag_uid": "0000000000000000"}
    if tool >= 0:
        # The AFC build of OrcaSlicer syncs the tray to that T number's slot.
        tray["tool"] = tool
    if lane is None or not lane.has_filament:
        tray.update(tray_info_idx="", tray_type="", tray_color="00000000", tray_slot_placeholder="1")
        return tray
    tray.update(tray_info_idx=filament_id, tray_type=lane.material or "", tray_color=_color(lane.color) or "00000000")
    if len(lane.multi_colors) > 1:
        tray["cols"] = [_color(c) for c in lane.multi_colors if c]
    if lane.nozzle_temp > 0:
        tray["nozzle_temp_max"] = tray["nozzle_temp_min"] = str(lane.nozzle_temp)
    if lane.bed_temp > 0:
        tray["bed_temp"] = str(lane.bed_temp)
    if lane.diameter > 0:
        tray["tray_diameter"] = "%.2f" % lane.diameter
    if lane.filament_name:
        tray["tray_sub_brands"] = lane.filament_name
    left = remaining(lane)
    if left >= 0:
        tray["remain"] = left
        # The AFC build of OrcaSlicer draws the share left on any spool that has it, and checks a print fits.
        tray["remain_g"] = int(round(lane.weight))
    # 0 for none: OrcaSlicer keeps a tray's last K when the field is missing.
    tray["k"] = max(lane.flow_k, 0.0)
    if lane.extruder_index >= 0:
        # The AFC build of OrcaSlicer keeps it per filament at sync, for "Purge in prime tower".
        tray["toolhead"] = lane.extruder_index
    if lane.tool_loaded:
        # The AFC build of OrcaSlicer shows it loaded, in a docked tool too.
        tray["in_tool"] = True
    tray.update(extra or {})
    return tray


def spool_json(lane: Lane, names: list, base: str) -> dict:
    """What OrcaSlicer's filament sync makes the spool's own preset from: the
    names it may have (presets.spool_preset_names()), the preset to copy when
    there is none (`base`), and the spool's values to put in it."""
    return {"names": list(names), "base": base, "vendor": lane.vendor, "nozzle_temp": lane.nozzle_temp,
            "bed_temp": lane.bed_temp, "density": lane.density, "diameter": lane.diameter,
            "pressure_advance": lane.flow_k}


def current_tray(snapshot: Snapshot) -> int:
    """The T number of the lane AFC has in the active toolhead, -1 for none."""
    lane = snapshot.find_lane(snapshot.current_lane) if snapshot.current_lane else None
    tools = [tool_number(m) for m in lane.maps] if lane is not None else []
    return next((t for t in tools if 0 <= t <= MAX_TRAY), -1)


def humidity_level(percent: float) -> int:
    """Bambu's humidity level for a percentage, 5 the driest."""
    return 5 if percent < 20 else 4 if percent < 40 else 3 if percent < 60 else 2 if percent < 80 else 1


def dryer_json(dryer: Dryer, filament: str = "") -> dict:
    """A dryer unit's humidity, temperature and drying, as an AMS 2 Pro or AMS
    HT reports them; `filament` is what the drying popup named."""
    out = {"dry_time": -(-dryer.remaining_s // 60) if dryer.drying else 0,
           # Why it cannot dry, sent every time so the AFC build of OrcaSlicer clears it once plugged in.
           "dry_sf_reason": [] if dryer.online else [DRY_DISCONNECTED]}
    if dryer.humidity >= 0:
        out["humidity_raw"] = str(int(round(dryer.humidity)))
        out["humidity"] = str(humidity_level(dryer.humidity))
    if dryer.temperature >= 0:
        out["temp"] = "%.1f" % dryer.temperature
    if dryer.drying:
        out["dry_setting"] = {"dry_filament": filament, "dry_temperature": int(round(dryer.target)),
                              "dry_duration": max(1, -(-dryer.duration_s // 3600))}
    return out


def unit_name(shown: Layout, box: Box) -> str:
    """The AFC unit a four-tray unit shows, numbered when it takes more than one."""
    parts = [b for b in shown.boxes if b.unit == box.unit and not b.single]
    return box.unit if len(parts) == 1 else "%s-%d" % (box.unit, parts.index(box) + 1)


def ams_json(snapshot: Snapshot, filament_ids: dict, extras: dict | None = None, reading=(),
             by_unit: bool = False, dry_filaments: dict | None = None) -> dict:
    """The "ams" object of a push_status message for AFC's lanes, laid out by
    layout(). `filament_ids` holds the filament id for each loaded T number,
    `extras` more tray fields by T number, `reading` the T numbers whose tag is
    being read, `dry_filaments` the filament each drying unit was started for."""
    trays = lanes_by_tray(snapshot)
    if not trays:
        return {"ams": [], "ams_exist_bits": "0", "tray_exist_bits": "0"}
    extras, dry_filaments = extras or {}, dry_filaments or {}
    shown = layout(snapshot, by_unit)
    units, ams_bits, tray_bits, reading_bits = [], 0, 0, 0
    for box in shown.boxes:
        unit = snapshot.find_unit(box.unit) if box.unit else None
        dryer = unit.dryer if unit is not None else None
        ams_bits |= 1 << (4 + box.ams_id - HT_ID if box.single else box.ams_id)
        slots = []
        for slot, tool in enumerate(box.tools):
            bit = 16 + box.ams_id - HT_ID if box.single else box.ams_id * 4 + slot
            lane = trays.get(tool)
            if lane is not None and lane.has_filament:
                tray_bits |= 1 << bit
            if tool in reading:
                reading_bits |= 1 << bit
            slots.append(tray_json(slot, lane, filament_ids.get(tool, ""), extras.get(tool), tool))
        kind = AMS_HT if box.single else AMS_2_PRO if dryer is not None else AMS
        # OrcaSlicer draws an AMS Lite's four trays in a square, and none at all for fewer.
        if kind == AMS and unit is not None and unit.model == LITE and len(box.tools) == 4:
            kind = AMS_LITE
        # "info": the AMS type, on the first extruder, and its dryer's state.
        info = kind | ((DRYING << 4 | HEATING << 22) if dryer is not None and dryer.drying else 0)
        entry = {"id": str(box.ams_id), "info": "%04X" % info, "tray": slots}
        if box.unit and not box.single:
            entry["unit_name"] = unit_name(shown, box)
        elif unit is not None and unit.type == TOOLCHANGER and trays.get(box.tools[0]) is not None:
            # A toolchanger's tool shows its extruder's name, not "AMS HT".
            tool_lane = trays[box.tools[0]]
            entry["unit_name"] = tool_lane.extruder or tool_lane.name
        if dryer is not None:
            entry.update(dryer_json(dryer, dry_filaments.get(box.unit, "")))
        elif box.single:
            entry["dryer"] = False
        else:
            entry["humidity"] = "0"  # an AMS shows a humidity level 1-5 (5 when not sent)
        units.append(entry)
    now = "%d" % shown.tray(current_tray(snapshot))
    # calibrate_remain_flag: OrcaSlicer draws how full each spool is only while the printer says it tracks it.
    return {"ams": units, "ams_exist_bits": "%X" % ams_bits, "tray_exist_bits": "%X" % tray_bits,
            "tray_now": now, "tray_tar": now, "tray_reading_bits": "%X" % reading_bits,
            "calibrate_remain_flag": True}


def _refill_bit(tray: int):
    """The bit of a tray id in Bambu's refill group masks, None past them:
    trays 0-15 and 24-27 at their own bit, AMS HT units at 16-23."""
    if 0 <= tray <= 15 or 24 <= tray <= 27:
        return tray
    if HT_ID <= tray < HT_ID + MAX_HT:
        return 16 + tray - HT_ID
    return None


def refill_groups(snapshot: Snapshot, shown: Layout) -> list:
    """AFC's runout lanes as Bambu's auto refill groups ("filam_bak"): a mask
    of the trays of each set of lanes that run out onto one another, as far as
    the masks reach. The print dialog lists them under "Auto Refill"."""
    lanes = {lane.name: lane for lane in snapshot.lanes}
    parent = {}

    def root(name):
        while parent.get(name, name) != name:
            name = parent[name]
        return name

    linked = set()
    for lane in snapshot.lanes:
        if lane.runout_lane in lanes and lane.runout_lane != lane.name:
            parent[root(lane.name)] = root(lane.runout_lane)
            linked |= {lane.name, lane.runout_lane}
    groups = {}  # root lane -> (tray bits, lanes with a tray)
    for tool, lane in sorted(lanes_by_tray(snapshot).items()):
        bit = _refill_bit(shown.tray(tool)) if lane.name in linked else None
        if bit is not None:
            bits, names = groups.setdefault(root(lane.name), (set(), set()))
            bits.add(bit)
            names.add(lane.name)
    return [sum(1 << bit for bit in bits) for bits, names in groups.values() if len(names) > 1]


def mapping_from_print(ams_mapping: str, ams_mapping2: str, filaments: int, shown: Layout | None = None) -> dict:
    """The print dialog's choice as file filament -> AFC T number. The
    dialog sends one entry per project filament: a tray id in ams_mapping,
    and {ams_id, slot_id} in ams_mapping2 (0xFF for none). `shown` is the
    layout the dialog picked from (by T number when not given)."""
    shown = shown or Layout([])
    out = {}
    try:
        first = json.loads(ams_mapping or "[]")
    except ValueError:
        first = []
    try:
        second = json.loads(ams_mapping2 or "[]")
    except ValueError:
        second = []
    for filament in range(filaments):
        tool = -1
        if isinstance(first, list) and filament < len(first):
            tool = shown.tool_of_tray(first[filament])
        if tool < 0 and isinstance(second, list) and filament < len(second):
            item = second[filament]
            if isinstance(item, dict):
                tool = shown.tool(item.get("ams_id"), item.get("slot_id"))
        if tool >= 0:
            out[filament] = tool
    return out
