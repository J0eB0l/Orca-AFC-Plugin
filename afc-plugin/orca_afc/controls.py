"""What the Device tab's controls do on a Klipper printer with AFC.

OrcaSlicer sends its controls as Bambu's JSON commands; these turn them into
the printer's own G-code:

- The lamp switches the printer's lights: Klipper LEDs and output pins whose
  names say they light the printer (main_led, chamber, caselight, the U1's
  cavity_led, ...).
- A tray's refresh button reads the spool's tag again with the command of the
  lane's unit, when the lane has a reader.
- The tray edit popup sets the lane's color and material.
- Load and Unload swap a lane into its toolhead and pull it back out.
- The camera plays the printer's first webcam that Moonraker lists.
- The fan popup shows every Klipper fan: the part fan and the fan_generic
  fans it sets, and those Klipper runs itself (heater, controller and
  temperature fans) as Auto.
- The speed popup's four levels set Klipper's speed factor (M220).
"""

from __future__ import annotations

import re
import urllib.parse

from . import protocol
from .protocol import AFCError, Lane, Snapshot
from .trays import NO_TRAY, Layout, lanes_by_tray

# ------------------------------------------------------------------ lights

LIGHT_KINDS = ("led", "neopixel", "dotstar", "pca9533", "pca9632", "output_pin")
LIGHT_WORDS = ("main", "chamber", "cavity", "case", "cabinet", "enclosure", "light", "lamp")  # cavity: the U1's
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


# ------------------------------------------------------------------ chamber

# A chamber heater first: it has a target.
CHAMBER_KINDS = ("heater_generic", "temperature_sensor", "temperature_fan")
CHAMBER_WORDS = ("chamber", "cavity", "enclosure", "cabinet")


def find_chamber(objects) -> str:
    """The Klipper object ("temperature_sensor chamber") that reads the
    chamber's temperature, from Moonraker's object list; "" for none."""
    found = {}
    for obj in objects or []:
        kind, _, name = str(obj).partition(" ")
        if kind in CHAMBER_KINDS and any(w in name.lower() for w in CHAMBER_WORDS):
            found.setdefault(kind, str(obj))
    return next((found[kind] for kind in CHAMBER_KINDS if kind in found), "")


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


# ------------------------------------------------------------------ fans

# Fans G-code sets, then those Klipper runs itself, which the fan popup shows as Auto.
SET_FAN_KINDS = ("fan", "fan_generic")
AUTO_FAN_KINDS = ("heater_fan", "controller_fan", "temperature_fan")
PART_FAN_ID = 1      # Bambu's part cooling fan, which OrcaSlicer names Parts
FIRST_FAN_ID = 16    # the others, past Bambu's fan ids
_M106 = re.compile(r"^\s*M106 P(\d+) S(\d+)\s*$")


def find_fans(objects) -> list:
    """The printer's fans ("fan", "fan_generic aux", "heater_fan hotend_fan",
    ...) from Moonraker's object list: the part fan, the fans G-code sets,
    then those Klipper runs itself."""
    found = [str(o) for o in objects or []]
    out = ["fan"] if "fan" in found else []
    for kind in SET_FAN_KINDS[1:] + AUTO_FAN_KINDS:
        for obj in found:
            k, _, name = obj.partition(" ")
            if k == kind and _NAME.match(name):
                out.append(obj)
    return out


def fan_ids(fans: list) -> dict:
    """Each fan's id in the fan popup: the part fan Bambu's, the others from FIRST_FAN_ID."""
    ids, n = {}, FIRST_FAN_ID
    for obj in fans:
        if obj == "fan":
            ids[obj] = PART_FAN_ID
        else:
            ids[obj], n = n, n + 1
    return ids


def fan_query(fans: list) -> dict:
    return {obj: ["speed"] for obj in fans}


def _fan_name(obj: str) -> str:
    """"heater_fan hotend_fan" is "Hotend fan"; the part fan has none, so OrcaSlicer names it Parts."""
    name = obj.partition(" ")[2].replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def airduct(status: dict, fans: list) -> dict:
    """The fans as the new protocol's airduct (print.device.airduct) with the
    AFC build of OrcaSlicer's agent_fans, so the fan popup shows each by its
    name with its speed, the ones Klipper runs itself as Auto."""
    parts = []
    for obj, fan_id in fan_ids(fans).items():
        st = status.get(obj) if isinstance(status, dict) else None
        speed = st.get("speed") if isinstance(st, dict) else None
        speed = speed if isinstance(speed, (int, float)) and not isinstance(speed, bool) else 0
        parts.append({"id": fan_id << 4, "func": fan_id, "state": max(0, min(100, int(round(speed * 100)))),
                      "range": 100 << 16, "name": _fan_name(obj),
                      "auto": obj.partition(" ")[0] in AUTO_FAN_KINDS})
    return {"modeCur": -1, "subMode": -1, "modeList": [], "parts": parts, "agent_fans": True}


def fan_gcode(fans: list, script: str) -> str:
    """The fan popup's "M106 P<id> S<0-255>" as Klipper's command for that
    fan; None when the line is not one of the popup's (Klipper's M106 has no P
    and would set the part fan), "" for a fan G-code cannot set."""
    match = _M106.match(script)
    if not match or not fans:
        return None
    fan_id, value = int(match.group(1)), min(255, int(match.group(2)))
    obj = next((o for o, i in fan_ids(fans).items() if i == fan_id), None)
    if obj == "fan":
        return "M106 S%d" % value
    if obj and obj.startswith("fan_generic "):
        return "SET_FAN_SPEED FAN=%s SPEED=%.2f" % (obj.partition(" ")[2], value / 255.0)
    return ""


# ------------------------------------------------------------------ speed

# The speed popup's levels (Silent, Standard, Sport, Ludicrous) as Bambu's speed percents.
SPEED_LEVELS = {1: 50, 2: 100, 3: 124, 4: 166}


def speed_gcode(level) -> str:
    """The speed popup's level ("1" to "4") as Klipper's speed factor; "" for another."""
    try:
        percent = SPEED_LEVELS.get(int(level))
    except (TypeError, ValueError):
        percent = None
    return "M220 S%d" % percent if percent else ""


def speed_report(factor) -> dict:
    """spd_mag (the speed in percent) and spd_lvl (the nearest level) from
    gcode_move's speed_factor; {} when Klipper did not report it."""
    if not isinstance(factor, (int, float)) or isinstance(factor, bool) or factor <= 0:
        return {}
    percent = int(round(factor * 100))
    level = min(SPEED_LEVELS, key=lambda lvl: abs(SPEED_LEVELS[lvl] - percent))
    return {"spd_mag": percent, "spd_lvl": level}


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
        # The insert's pull-in and read; AFC_BAMBU_SCAN only reads, and it
        # pulls on a spool held in a toolhead until the AMS stalls.
        return "AFC_BAMBU_CAPSCAN LANE=" + name
    # A V1 ACE's firmware reads the tag; an ACE 2 needs AFC's reader on the lane.
    if unit_ok and kind.startswith("ACE") and (not kind.startswith("ACE2") or name in readers.get("AFC_ACE2_rfid", {})):
        return "AFC_ACE_RFID_RESCAN UNIT=%s LANE=%s" % (unit, name)
    return ""


# ------------------------------------------------------------------ trays


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


def change_filament_gcode(snapshot: Snapshot, request: dict, shown: Layout) -> str:
    """Load (a tray) or Unload (the unit's lane in a toolhead, the active
    toolhead's lane first) from the Device tab, whose units are `shown`."""
    ams_id, slot_id = request.get("ams_id"), request.get("slot_id")
    if slot_id == NO_TRAY or request.get("target") == NO_TRAY:
        trays, box = lanes_by_tray(snapshot), shown.box(ams_id)
        tools = box.tools if box is not None else []
        loaded = [trays[t] for t in tools if t in trays and trays[t].tool_loaded]
        current = snapshot.find_lane(snapshot.current_lane) if snapshot.current_lane else None
        if current is not None and current.tool_loaded and (not loaded or current in loaded):
            lane = current
        elif loaded:
            lane = loaded[0]
        else:
            raise AFCError("no lane is loaded in a toolhead")
        return protocol.build_lane_action_gcode(snapshot, lane.name, "unload")
    lane = lane_of(snapshot, shown.tool(ams_id, slot_id))
    if lane is None:
        raise AFCError("no lane on that tray")
    return protocol.build_lane_action_gcode(snapshot, lane.name, "load")


# ------------------------------------------------------------------ camera

# Moonraker webcam services whose stream_url is an MJPEG stream, which the Device
# tab's player opens. For any other (WebRTC, HLS) the camera shows the snapshot.
MJPEG_SERVICES = ("mjpegstreamer", "uv4l-mjpeg")
# A webcam that is a web page or the viewer's own device has no picture to take.
NOT_CAMERA_SERVICES = ("iframe", "device")
# A nozzle or alignment camera (plugged in for tool offsets) is shown only when no other camera is set up.
NOZZLE_CAMERA_WORDS = ("nozzle", "tool", "align", "calib", "ktamv", "offset")


def camera(webcams, printer_url: str) -> tuple:
    """("http", MJPEG stream URL) or ("http_snapshot", snapshot URL) for the
    first enabled webcam of Moonraker's /server/webcams/list, a nozzle camera
    last; ("", "") for none. A relative URL is on the printer's web server: its
    address without Moonraker's own port, as Fluidd and Mainsail open it."""
    cams = [c for c in webcams or [] if isinstance(c, dict)]
    cams.sort(key=lambda c: any(w in str(c.get("name") or "").lower() for w in NOZZLE_CAMERA_WORDS))
    for cam in cams:
        if cam.get("enabled") is False or cam.get("service") in NOT_CAMERA_SERVICES:
            continue
        stream, snapshot = cam.get("stream_url"), cam.get("snapshot_url")
        # Fluidd's "adaptive" MJPEG reads snapshots; the stream is ustreamer's or mjpg-streamer's all the same.
        if cam.get("service") in MJPEG_SERVICES + ("mjpegstreamer-adaptive",) and isinstance(stream, str) and stream:
            return "http", _web_url(stream, printer_url)
        if isinstance(snapshot, str) and snapshot:
            return "http_snapshot", _web_url(snapshot, printer_url)
    return "", ""


def _web_url(url: str, printer_url: str) -> str:
    if "://" in url:
        return url
    parts = urllib.parse.urlsplit(printer_url)
    host = parts.hostname or ""
    if ":" in host:
        host = "[%s]" % host
    try:
        port = parts.port
    except ValueError:
        port = None
    if port and port != 7125:
        host += ":%d" % port
    return urllib.parse.urljoin("%s://%s/" % (parts.scheme or "http", host), url)
