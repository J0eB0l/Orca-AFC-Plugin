"""AFC's lanes as the AMS trays OrcaSlicer's device model reads.

OrcaSlicer keeps a printer's filaments in Bambu's AMS shape: units of four
trays, a tray id being unit * 4 + slot. AFC's T numbers fit it directly: T(n)
is tray n, in unit n // 4, slot n % 4, as OrcaSlicer's own Moonraker agent
lays out AFC's lane data. Filament sync then puts tray n in project slot n + 1,
and the print dialog's tray for a filament is the T number it prints with.
"""

from __future__ import annotations

import json

from .protocol import Lane, Snapshot, tool_number

# OrcaSlicer reads the tray presence bits with stol(), which a bit past 30
# overflows on Windows (32-bit long), and the bit test itself shifts an int.
MAX_TRAY = 30
NO_TRAY = 0xFF


def lanes_by_tray(snapshot: Snapshot) -> dict:
    """T number -> the lane AFC maps to it, for T0 to T30."""
    out = {}
    for lane in snapshot.lanes:
        for m in lane.maps:
            tool = tool_number(m)
            if 0 <= tool <= MAX_TRAY and tool not in out:
                out[tool] = lane
    return out


def _color(hex_color: str) -> str:
    """"#RRGGBB" -> "RRGGBBFF", OrcaSlicer's tray color; "" for no color."""
    return hex_color[1:].upper() + "FF" if hex_color else ""


def remaining(lane: Lane) -> int:
    """Share of the spool left, 0-100, or -1 when AFC has no weight for it."""
    if not lane.has_filament or lane.weight <= 0:
        return -1
    initial = lane.initial_weight if lane.initial_weight > 0 else 1000.0
    return max(0, min(100, int(round(lane.weight / initial * 100))))


def tray_json(slot: int, lane: Lane | None, filament_id: str, extra: dict | None = None) -> dict:
    """A tray of the "ams" object. `extra` adds the spool and preset fields the
    agent works out for a loaded tray (see spool_json())."""
    tray = {"id": str(slot), "tag_uid": "0000000000000000"}
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
    if lane.flow_k >= 0:
        tray["k"] = lane.flow_k
    if lane.extruder_index >= 0:
        # The AFC build of OrcaSlicer keeps it per filament at sync, for "Purge in prime tower".
        tray["toolhead"] = lane.extruder_index
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
    """The tray of the lane AFC has in the active toolhead, NO_TRAY for none."""
    lane = snapshot.find_lane(snapshot.current_lane) if snapshot.current_lane else None
    tools = [tool_number(m) for m in lane.maps] if lane is not None else []
    return next((t for t in tools if 0 <= t <= MAX_TRAY), NO_TRAY)


def ams_json(snapshot: Snapshot, filament_ids: dict, extras: dict | None = None, reading=()) -> dict:
    """The "ams" object of a push_status message for AFC's lanes.
    `filament_ids` holds the filament id for each loaded T number, `extras`
    more tray fields by T number, `reading` the trays whose tag is being read."""
    trays = lanes_by_tray(snapshot)
    if not trays:
        return {"ams": [], "ams_exist_bits": "0", "tray_exist_bits": "0"}
    extras = extras or {}
    last = max(trays)
    units, ams_bits, tray_bits = [], 0, 0
    for unit in range(last // 4 + 1):
        ams_bits |= 1 << unit
        slots = []
        for slot in range(min(3, last - unit * 4) + 1):
            tray = unit * 4 + slot
            lane = trays.get(tray)
            if lane is not None and lane.has_filament:
                tray_bits |= 1 << tray
            slots.append(tray_json(slot, lane, filament_ids.get(tray, ""), extras.get(tray)))
        # "0002": an AMS lite on the first extruder, as OrcaSlicer's Moonraker agent reports lanes.
        units.append({"id": str(unit), "info": "0002", "tray": slots})
    now = str(current_tray(snapshot))
    reading_bits = sum(1 << t for t in set(reading) if 0 <= t <= MAX_TRAY)
    return {"ams": units, "ams_exist_bits": "%X" % ams_bits, "tray_exist_bits": "%X" % tray_bits,
            "tray_now": now, "tray_tar": now, "tray_reading_bits": "%X" % reading_bits}


def mapping_from_print(ams_mapping: str, ams_mapping2: str, filaments: int) -> dict:
    """The print dialog's choice as file filament -> AFC T number. The
    dialog sends one entry per project filament: a tray id in ams_mapping,
    and {ams_id, slot_id} in ams_mapping2 (0xFF for none)."""
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
        tray = -1
        if isinstance(first, list) and filament < len(first) and isinstance(first[filament], int):
            tray = first[filament]
        if not 0 <= tray <= MAX_TRAY and isinstance(second, list) and filament < len(second):
            item = second[filament]
            if isinstance(item, dict):
                unit, slot = item.get("ams_id"), item.get("slot_id")
                if isinstance(unit, int) and isinstance(slot, int) and 0 <= unit < NO_TRAY and 0 <= slot < 4:
                    tray = unit * 4 + slot
        if 0 <= tray <= MAX_TRAY:
            out[filament] = tray
    return out
