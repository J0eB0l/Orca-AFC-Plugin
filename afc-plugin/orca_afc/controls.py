"""What the Device tab's controls do on a Klipper printer with AFC.

OrcaSlicer sends its controls as Bambu's JSON commands; these turn them into
the printer's own G-code:

- The lamp switches the printer's lights: Klipper LEDs and output pins whose
  names say they light the printer (main_led, chamber, caselight, ...).
- A tray's refresh button reads the spool's tag again with the command of the
  lane's unit, when the lane has a reader.
- The tray edit popup sets the lane's color and material.
- Load and Unload swap a lane into its toolhead and pull it back out.
"""

from __future__ import annotations

import re

from . import protocol
from .protocol import AFCError, Lane, Snapshot
from .trays import MAX_TRAY, NO_TRAY, lanes_by_tray

# ------------------------------------------------------------------ lights

LIGHT_KINDS = ("led", "neopixel", "dotstar", "pca9533", "pca9632", "output_pin")
LIGHT_WORDS = ("main", "chamber", "case", "cabinet", "enclosure", "light", "lamp")
# Lights on the toolhead or a unit show status; the lamp leaves them alone.
NOT_LIGHT_WORDS = ("toolhead", "nozzle", "logo", "status", "sb_", "stealthburner")

_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def find_lights(objects) -> list:
    """The Klipper objects ("led main_led", "output_pin caselight") that light
    the printer, from Moonraker's object list."""
    out = []
    for obj in objects or []:
        kind, _, name = str(obj).partition(" ")
        low = name.lower()
        if (kind in LIGHT_KINDS and _NAME.match(name) and any(w in low for w in LIGHT_WORDS)
                and not any(w in low for w in NOT_LIGHT_WORDS)):
            out.append(str(obj))
    return out


def light_query(lights: list) -> dict:
    return {obj: ["value"] if obj.startswith("output_pin ") else ["color_data"] for obj in lights}


def lights_on(status: dict, lights: list):
    """Whether any of the lights is on, None when Klipper reported none of them."""
    seen = False
    for obj in lights:
        st = status.get(obj) if isinstance(status, dict) else None
        if not isinstance(st, dict):
            continue
        if obj.startswith("output_pin "):
            values = [st.get("value")]
        else:
            data = st.get("color_data")
            values = [c for led in data if isinstance(led, (list, tuple)) for c in led] if isinstance(data, list) else []
        numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if numbers:
            seen = True
            if any(v > 0 for v in numbers):
                return True
    return False if seen else None


def light_gcode(lights: list, on: bool) -> list:
    """Every light full on (white), or off."""
    v = 1 if on else 0
    out = []
    for obj in lights:
        kind, _, name = obj.partition(" ")
        if kind == "output_pin":
            out.append("SET_PIN PIN=%s VALUE=%d" % (name, v))
        else:
            out.append("SET_LED LED=%s RED=%d GREEN=%d BLUE=%d WHITE=%d" % (name, v, v, v, v))
    return out


# ------------------------------------------------------------------ RFID

# The reader modules that serve some lanes, and the status field naming them.
READERS = {"AFC_U1_rfid": "lane_channel_map", "AFC_BoxTurtle_rfid": "lane_slot_map",
           "AFC_Vivid_rfid": "lane_slot_map", "AFC_OpenAMS_rfid": "lane_slot_map", "AFC_ACE2_rfid": "lane_slot_map"}


def reader_query() -> dict:
    return {obj: [field] for obj, field in READERS.items()}


def reader_lanes(status: dict) -> dict:
    """Reader module -> {lane: its slot or channel}, for the modules the printer has."""
    out = {}
    for obj, field in READERS.items():
        st = status.get(obj) if isinstance(status, dict) else None
        lanes = st.get(field) if isinstance(st, dict) else None
        if isinstance(lanes, dict) and lanes:
            out[obj] = lanes
    return out


def rfid_scan_gcode(lane: Lane, readers: dict) -> str:
    """The command that reads the tag of the spool in a lane again, as the
    lane's unit does on an insert: turning the spool past the reader where the
    unit does that. "" when the lane has no reader."""
    name, unit = lane.name, lane.unit
    if not protocol.is_safe_lane_name(name):
        return ""
    channel = readers.get("AFC_U1_rfid", {}).get(name)
    if isinstance(channel, int) and not isinstance(channel, bool):
        return "FILAMENT_DT_UPDATE CHANNEL=%d" % channel
    if name in readers.get("AFC_BoxTurtle_rfid", {}):
        return "AFC_BT_RFID_STAGE LANE=" + name
    if name in readers.get("AFC_Vivid_rfid", {}):
        return "VIVID_RFID_READ LANE=" + name
    unit_ok = protocol.is_safe_lane_name(unit)
    if unit_ok and name in readers.get("AFC_OpenAMS_rfid", {}):
        return "AFC_OAMS_RFID_SCAN UNIT=%s LANE=%s" % (unit, name)
    kind = lane.unit_type.upper()
    if kind.startswith("BAMBU"):
        return "AFC_BAMBU_SCAN LANE=" + name
    # A V1 ACE's firmware reads the tag; an ACE 2 needs AFC's reader on the lane.
    if unit_ok and kind.startswith("ACE") and (not kind.startswith("ACE2") or name in readers.get("AFC_ACE2_rfid", {})):
        return "ACE_RFID_RESCAN UNIT=%s LANE=%s" % (unit, name)
    return ""


# ------------------------------------------------------------------ trays


def tray_of(ams_id, slot_id) -> int:
    """The tray (T number) of a unit and slot OrcaSlicer names, -1 for none."""
    if isinstance(ams_id, int) and isinstance(slot_id, int) and 0 <= slot_id < 4 and 0 <= ams_id * 4 + slot_id <= MAX_TRAY:
        return ams_id * 4 + slot_id
    return -1


def lane_of(snapshot: Snapshot, tray: int):
    return lanes_by_tray(snapshot).get(tray) if snapshot is not None and tray >= 0 else None


def filament_setting_gcode(snapshot: Snapshot, lane: Lane, request: dict) -> list:
    """The tray edit popup's color and material for the lane, where they differ
    from what AFC has."""
    changes = {}
    color = request.get("tray_color")
    # "RRGGBBAA"; a clear one ("00000000") is no color.
    if isinstance(color, str) and re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", color) and color[6:] != "00":
        if "#" + color[:6].upper() != (lane.color or "").upper():
            changes["color"] = "#" + color[:6].upper()
    material = request.get("tray_type")
    if isinstance(material, str) and material and material.upper() != (lane.material or "").upper():
        changes["material"] = material
    return protocol.build_lane_edit_gcode(snapshot, lane.name, changes) if changes else []


def change_filament_gcode(snapshot: Snapshot, request: dict) -> str:
    """Load (a tray) or Unload (the unit's lane in a toolhead, the active
    toolhead's lane first) from the Device tab."""
    ams_id, slot_id = request.get("ams_id"), request.get("slot_id")
    if slot_id == NO_TRAY or request.get("target") == NO_TRAY:
        trays = lanes_by_tray(snapshot)
        loaded = [trays[t] for t in sorted(trays) if isinstance(ams_id, int) and t // 4 == ams_id and trays[t].tool_loaded]
        current = snapshot.find_lane(snapshot.current_lane) if snapshot.current_lane else None
        if current is not None and current.tool_loaded and (not loaded or current in loaded):
            lane = current
        elif loaded:
            lane = loaded[0]
        else:
            raise AFCError("no lane is loaded in a toolhead")
        return protocol.build_lane_action_gcode(snapshot, lane.name, "unload")
    lane = lane_of(snapshot, tray_of(ams_id, slot_id))
    if lane is None:
        raise AFCError("no lane on that tray")
    return protocol.build_lane_action_gcode(snapshot, lane.name, "load")
