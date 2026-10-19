"""The AFC printer agent: OrcaSlicer's Device tab, filament sync and print
dialog for a Klipper printer that runs AFC.

OrcaSlicer talks to the printer through this agent as it talks to a Bambu
printer on the LAN, in Bambu's JSON messages:

- AFC's lanes are the printer's AMS trays, one tray per T number, in AMS
  units by T number or, for the AFC build of OrcaSlicer, by AFC unit (trays.py).
  Each loaded tray describes its spool, so filament sync picks (or makes) the
  spool's own preset, and names the filament id of the preset that fits it
  best, which an OrcaSlicer without spool presets picks instead (presets.py).
- The Device tab's controls run AFC's own commands (controls.py).
- A print from the print dialog prints each filament from the tray the dialog
  picked for it: the file's tool changes are renumbered to those T numbers,
  with AFC's lane-change purges and spool flow K written in (gcode.py), and the
  file goes to Moonraker and starts.

OrcaSlicer calls the agent on its UI thread (connecting, messages) and on its
print job threads (prints). It checks the network, file and thread work a
plugin does in those calls and refuses it off the UI thread, so the work runs
on the agent's own threads, started while connecting, and what OrcaSlicer must
hear on its UI thread goes through its queue_on_main.
"""

from __future__ import annotations

import json
import math
import os
import posixpath
import re
import threading
import time
import traceback
import urllib.parse
import zipfile
from dataclasses import dataclass, field

import orca

from . import controls, gcode, presets, protocol, swaptimes, trays
from .manager import LANE_MOVE_TIMEOUT, AFCManager
from .moonraker import Moonraker, MoonrakerError, base_url, opens_without_key
from .protocol import AFCError
from .worker import Worker

AGENT_ID = "afc"
POLL = 5.0            # seconds between status pushes
PRESETS_EVERY = 60.0  # seconds between reads of OrcaSlicer's filament presets
LOST_AFTER = 3        # failed reads in a row before OrcaSlicer hears the printer is gone
JOB_RETRY = 30.0      # seconds before a print file's metadata is asked for again while Moonraker has none
CAMERA_EVERY = 60.0   # seconds between reads of Moonraker's webcam list
STATS_EVERY = 30.0    # seconds between reads of AFC's change times (swaptimes.py)
NOZZLES_EVERY = 600.0  # seconds between reads of the toolheads' nozzle sizes in Klipper's config
TRAY_RELEASE_S = 12.0  # seconds a tray that left the toolhead is still named, its switch off

# What OrcaSlicer sends as the access code when the printer's API key is empty.
NO_ACCESS_CODE = "88888888"

# OrcaSlicer's result codes and print stages (bambu_networking.hpp).
SUCCESS = 0
FAILED = -1
ERR_FILE_NOT_EXIST = -14
ERR_CANCELED = -18
ERR_INVALID_RESULT = -19
ERR_UPLOAD_FAILED = -4020
ERR_START_FAILED = -4030
# The printer has no hardware for a Device tab control (IPrinterAgent.hpp).
ERR_CAP_NOT_AVAILABLE = -7020
CONNECT_OK, CONNECT_LOST = 0, 2
# The print job shows these; its "finished" stage counts down to the Device tab
# itself once the agent returns, so the agent never sends that one.
STAGE_CREATE, STAGE_UPLOAD, STAGE_SENDING = 0, 1, 3

# home_flag bits: the storage OrcaSlicer prints from (bits 8-9, 1 = normal), and
# the print dialog's flow and pressure advance calibration switches.
SD_CARD_NORMAL = 1 << 8
AUTO_REFILL = 1 << 10  # the printer goes on with a refill group's next tray when one runs out
FLOW_CALIBRATION = 1 << 15
PA_CALIBRATION = 1 << 16
# flag3 bit: the printer takes up to 32 filaments. Without it the print dialog
# refuses a file with more than 16, the limit of Bambu's older firmware.
FILAMENTS_32 = 1 << 17
# flag3 bit: a tray's filament may be set while printing (AFC takes a lane edit any time).
FILAMENT_EDIT_PRINTING = 1 << 3

_STATES = {"printing": "RUNNING", "paused": "PAUSE", "complete": "FINISH", "error": "FAILED", "cancelled": "FAILED"}
_STAGES = {"printing": 1, "paused": 2, "complete": 3, "error": 4}
_PRINT_ENDPOINTS = {"pause": "/printer/print/pause", "resume": "/printer/print/resume", "stop": "/printer/print/cancel"}


def _first(value) -> str:
    return presets._first(value)


# ------------------------------------------------------------------ the printer's model


def printer_model(bundle) -> tuple:
    """(printer_model, the file of a system printer preset: the one the
    printer preset comes from, else any, which shows where the vendors'
    profiles are), read on OrcaSlicer's UI thread."""
    preset = bundle.current_printer_preset() if bundle is not None else None
    if preset is None:
        return "", ""
    model = _first(preset.config_value("printer_model"))
    seen = set()
    while preset is not None and not preset.is_system and preset.name not in seen:
        seen.add(preset.name)
        parent = _first(preset.config_value("inherits"))
        preset = bundle.printers.find_preset(parent) if parent else None
    if preset is not None and preset.file:
        return model, str(preset.file)
    printers = bundle.printers
    for index in range(printers.size()):
        other = printers.preset(index)
        if other is not None and other.is_system and other.file:
            return model, str(other.file)
    return model, ""


def preset_name(bundle, dev_id: str) -> str:
    """The name of the printer preset that prints to the printer at `dev_id`
    (the selected preset first), "" for none; the device list shows it, as
    printers often share a host name. UI thread."""
    if bundle is None:
        return ""
    printers = bundle.printers
    for preset in [bundle.current_printer_preset()] + [printers.preset(i) for i in range(printers.size())]:
        host = _first(preset.config_value("print_host")) if preset is not None else ""
        if host and dev_id_from_address(host, _first(preset.config_value("printhost_port"))) == dev_id:
            return str(preset.name or "")
    return ""


def _https(bundle) -> bool:
    """The printer preset's address is https (OrcaSlicer hands the agent the host without it)."""
    try:
        preset = bundle.current_printer_preset() if bundle is not None else None
        return preset is not None and _first(preset.config_value("print_host")).lower().startswith("https://")
    except Exception:
        return False


def model_id(model: str, preset_file: str) -> str:
    """The printer model's model_id, which OrcaSlicer matches printers and
    their presets by. Like OrcaSlicer, it looks the model up by name among
    every vendor's models (machine_model_list, the preset's own vendor
    first); the model's own file holds the id. preset_file is a system
    printer preset's file, which shows where the vendors' profiles are."""
    if not model or not preset_file:
        return ""
    machine_dir = os.path.dirname(preset_file)
    vendor_dir = os.path.dirname(machine_dir)
    own = vendor_dir + ".json"
    root = os.path.dirname(vendor_dir)
    try:
        others = sorted(os.path.join(root, name) for name in os.listdir(root) if name.endswith(".json"))
    except OSError:
        others = []
    for vendor_file in [own] + [f for f in others if f != own]:
        vendor = presets._load(vendor_file)
        entries = (vendor.get("machine_model_list") or []) if isinstance(vendor, dict) else []
        files = [os.path.join(vendor_file[:-len(".json")], str(e["sub_path"])) for e in entries
                 if isinstance(e, dict) and e.get("name") == model and e.get("sub_path")]
        if vendor_file == own:
            files.append(os.path.join(machine_dir, model + ".json"))
        for path in files:
            data = presets._load(path)
            if isinstance(data, dict) and data.get("model_id"):
                return str(data["model_id"])
    return ""


def dev_id_from_address(host: str, port: str) -> str:
    """The id OrcaSlicer gives the printer at a print host, as
    MachineObject::dev_id_from_address makes it: "http://10.0.0.5/" with
    port 7125 is "10.0.0.5:7125"."""
    for scheme in ("http://", "https://"):
        if host.startswith(scheme):
            host = host[len(scheme):]
            break
    host = host.split("/", 1)[0]
    if port:
        host = host.split(":", 1)[0] + ":" + port
    return host


def announcement(dev_id: str, name: str, dev_type: str) -> str:
    """The discovery message that puts a printer in OrcaSlicer's device list."""
    return json.dumps({
        "dev_name": name, "dev_id": dev_id, "dev_ip": dev_id, "dev_type": dev_type, "dev_signal": "0",
        "connect_type": "lan", "bind_state": "free", "sec_link": "secure", "ssdp_version": "v1"})


# ------------------------------------------------------------------ messages


def status_query(extruders: list, lights=(), chamber: str = "") -> dict:
    # display_status holds the slicer's M73 progress, print_stats.info the layers SET_PRINT_STATS_INFO sets.
    query = {"print_stats": ["state", "filename", "print_duration", "info"], "toolhead": ["homed_axes", "extruder"],
             "heater_bed": ["temperature", "target"], "fan": ["speed"], "virtual_sdcard": ["progress"],
             "display_status": ["progress"], "gcode_move": ["gcode_position"]}
    for name in extruders or ["extruder"]:
        query[name] = ["temperature", "target"]
    if chamber:
        query[chamber] = ["temperature", "target"]
    query.update(controls.light_query(list(lights)))
    query.update(controls.reader_query())
    return query


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def toolhead_nozzles(settings) -> list:
    """Each toolhead's nozzle_diameter from Klipper's configfile settings, by
    extruder number (extruder, extruder1, ...), 0 for a number it lacks."""
    sizes = {}
    for name, section in (settings.items() if isinstance(settings, dict) else ()):
        number = re.fullmatch(r"extruder(\d*)", name)
        size = _number(section.get("nozzle_diameter")) if number and isinstance(section, dict) else None
        if size:
            sizes[int(number.group(1) or 0)] = float(size)
    return [sizes.get(i, 0.0) for i in range(max(sizes) + 1)] if sizes else []


def job_info(metadata, filename: str, url: str) -> dict:
    """What the Device tab shows of a print besides Klipper's status, from
    Moonraker's metadata for its file: the slicer's time estimate, the layer
    count and heights, and the URL of the largest thumbnail."""
    meta = metadata if isinstance(metadata, dict) else {}
    info = {}
    for key in ("estimated_time", "layer_count", "first_layer_height", "layer_height"):
        value = _number(meta.get(key))
        if value is not None and value > 0:
            info[key] = value
    thumbs = [t for t in meta.get("thumbnails") or [] if isinstance(t, dict) and isinstance(t.get("relative_path"), str)]
    if thumbs and url:
        best = max(thumbs, key=lambda t: _number(t.get("width")) or 0)
        path = posixpath.join(posixpath.dirname(filename), best["relative_path"])
        info["thumbnail_url"] = "%s/server/files/gcodes/%s" % (url, urllib.parse.quote(path))
    return info


def _layers(state: str, stats: dict, position, job: dict):
    """(current layer, total layers) of the print, None when not known. Klipper's
    own count when the file sets it (SET_PRINT_STATS_INFO), else the file's
    layer count with the current layer from the nozzle's height, once the print
    has extruded."""
    info = stats.get("info") if isinstance(stats.get("info"), dict) else {}
    total = _number(info.get("total_layer")) or job.get("layer_count")
    if not total:
        return None
    current = _number(info.get("current_layer"))
    if state == "complete":
        current = total
    elif current is None:
        z = _number(position[2]) if isinstance(position, list) and len(position) > 2 else None
        first, height = job.get("first_layer_height"), job.get("layer_height")
        current = 0
        if z is not None and first and height and (_number(stats.get("print_duration")) or 0) > 0:
            current = math.ceil((z - first) / height + 1 - 1e-6)
    return max(0, min(int(current), int(total))), int(total)


def push_status(objects: dict, snapshot, filament_ids: dict, extras=None, reading=(), lights=(), job=None,
                by_unit: bool = False, dry_filaments=None, chamber: str = "", swap_times=None,
                nozzles=()) -> dict:
    """A push_status message from Klipper's objects (status_query()) and the
    AFC snapshot, None when AFC did not answer (OrcaSlicer keeps the trays it
    has). `extras`, `reading`, `by_unit` and `dry_filaments` go to the trays
    (trays.ams_json()), `lights` are the printer's lights, which the lamp
    shows, `job` the print file's job_info(), `chamber` the Klipper object
    that reads the chamber's temperature (controls.find_chamber()),
    `swap_times` AFC's measured change times (swaptimes.Tracker.times()) and
    `nozzles` each toolhead's nozzle size (toolhead_nozzles())."""
    job = job or {}
    def obj(name):
        value = objects.get(name) if isinstance(objects, dict) else None
        return value if isinstance(value, dict) else {}

    stats, toolhead = obj("print_stats"), obj("toolhead")
    state = str(stats.get("state") or "").lower()
    u1 = snapshot is not None and snapshot.u1_print.present
    homed = str(toolhead.get("homed_axes") or "").lower()
    home_flag = SD_CARD_NORMAL  # Moonraker's gcodes folder is always there to print from
    for axis, bit in (("x", 1), ("y", 2), ("z", 4)):
        if axis in homed:
            home_flag |= bit
    if u1:
        home_flag |= FLOW_CALIBRATION | PA_CALIBRATION
    out = {
        "command": "push_status", "msg": 0, "support_mqtt_alive": True,
        "gcode_state": _STATES.get(state, "IDLE"), "mc_print_stage": _STAGES.get(state, 0),
        # OrcaSlicer reads these as HMS codes; anything but 0 opens its error dialog.
        "mc_print_error_code": 0, "print_error": 0,
        "home_flag": home_flag, "flag3": FILAMENTS_32 | FILAMENT_EDIT_PRINTING, "sdcard": True,
        "support_send_to_sd": True,
        # The AFC build of OrcaSlicer then adds Upload to the print dialog: stored with the dialog's trays, not started.
        "support_upload_only": True,
        # ... and shows the tray popup's Factor K, saved as the spool's flow K.
        "support_tray_k": True,
        # ... and takes the drying popup on the units with a dryer (trays.ams_json()).
        "support_remote_dry": True,
        # AFC's runout lanes, as Bambu's auto refill groups below.
        "support_filament_backup": True,
        # The print dialog's switches, which only a U1 has a use for (its print_task_config).
        "support_bed_leveling": 1 if u1 else 0, "support_flow_calibration": u1, "support_timelapse": u1,
        "nozzle_temp_range": [100, 370], "bed_temp_range": [0, 120],
        # The AFC build of OrcaSlicer shows the chamber's temperature on the Device tab.
        "support_chamber": bool(chamber),
    }
    if swap_times:
        # The AFC build of OrcaSlicer puts these in the printer's load, unload and tool change times on a sync.
        out["swap_times"] = dict(swap_times)
    if nozzles:
        # ... and its print dialog checks the file's toolheads and nozzle sizes against them.
        out["toolhead_nozzles"] = list(nozzles)
    nozzle = obj(str(toolhead.get("extruder") or "extruder"))
    bed = obj("heater_bed")
    for key, source, name in (("nozzle_temper", nozzle, "temperature"), ("nozzle_target_temper", nozzle, "target"),
                              ("bed_temper", bed, "temperature"), ("bed_target_temper", bed, "target")):
        value = _number(source.get(name))
        if value is not None:
            out[key] = value
    # A chamber heater's target too (a temperature_fan's is the fan's, not a heat target).
    for key, name in (("chamber_temper", "temperature"), ("ctt", "target")):
        value = _number(obj(chamber).get(name)) if chamber else None
        if value is not None and (key != "ctt" or chamber.startswith("heater_generic ")):
            out[key] = value
    speed = _number(obj("fan").get("speed"))
    if speed is not None:
        out["fan_gear"] = max(0, min(255, int(round(speed * 255))))
    if isinstance(stats.get("filename"), str):
        out["subtask_name"] = out["gcode_file"] = stats["filename"]
    out["thumbnail_url"] = job.get("thumbnail_url", "")
    # The slicer's M73 progress, which runs by print time; Klipper falls back to the file position without it.
    progress = _number(obj("display_status").get("progress"))
    if progress is None:
        progress = _number(obj("virtual_sdcard").get("progress"))
    if progress is not None and progress >= 0:
        progress = min(progress, 1.0)
        out["mc_percent"] = int(round(progress * 100))
        elapsed = _number(stats.get("print_duration"))
        if state in ("printing", "paused"):
            if job.get("estimated_time"):
                out["mc_remaining_time"] = int(job["estimated_time"] * (1 - progress) / 60)
            elif elapsed and progress > 0:
                out["mc_remaining_time"] = int((elapsed / progress - elapsed) / 60)
    layers = None
    if state in ("printing", "paused", "complete"):
        layers = _layers(state, stats, obj("gcode_move").get("gcode_position"), job)
    if layers is not None:
        out["layer_num"], out["total_layer_num"] = layers
    lit = controls.lights_on(objects, list(lights)) if isinstance(objects, dict) else None
    if lit is not None:
        out["lights_report"] = [{"node": "chamber_light", "mode": "on" if lit else "off"}]
    if snapshot is not None:
        out["ams"] = trays.ams_json(snapshot, filament_ids, extras, reading, by_unit, dry_filaments)
        # A Bambu printer's filament switch at the extruder: set, the Device tab draws the tray_now tray's
        # path to the extruder. Never with no tray (255), which OrcaSlicer reads as the external spool.
        now = out["ams"].get("tray_now")
        out["hw_switch_state"] = 1 if now not in (None, "%d" % trays.NO_TRAY) else 0
        groups = trays.refill_groups(snapshot, trays.layout(snapshot, by_unit))
        # Always sent, so a runout lane taken off leaves OrcaSlicer's groups too.
        out["filam_bak"] = groups
        if groups:
            out["home_flag"] |= AUTO_REFILL
        # The AFC build of OrcaSlicer shows AFC's oldest error or warning on the Device tab, where Clear
        # sends clear_agent_message. Always sent, so a cleared message leaves the Device tab too.
        out["agent_message"] = {"text": snapshot.message, "type": snapshot.message_type} if snapshot.message else {}
    return {"print": out, "t_utc": int(time.time() * 1000)}


def release_tray(out: dict, printer, clock: float) -> None:
    """OrcaSlicer clears a tray's path to the extruder only on a push that names
    the tray with the filament switch off, as a Bambu printer reports an unload
    before its tray_now goes to 255; a push with 255 leaves the path drawn. So
    the tray that left the toolhead is still named, switched off, for
    TRAY_RELEASE_S before `out` (push_status()'s "print") says what is there
    now. `printer` keeps the tray drawn and when its release ends."""
    ams = out.get("ams")
    if not isinstance(ams, dict) or "tray_now" not in ams:
        return
    left = printer.drawn_tray
    if left and ams["tray_now"] != left:
        if printer.release_until is None:
            printer.release_until = clock + TRAY_RELEASE_S
        if clock < printer.release_until:
            ams["tray_now"] = ams["tray_tar"] = left
            out["hw_switch_state"] = 0
            return
    printer.release_until = None
    printer.drawn_tray = ams["tray_now"] if out.get("hw_switch_state") == 1 else ""


# ------------------------------------------------------------------ the print file


def plate_gcode(path: str, plate: int):
    """The sliced G-code of a print job: plate `plate`'s file inside the
    project OrcaSlicer exported for it, else the G-code file beside it. None
    when there is none."""
    if path and os.path.isfile(path):
        if not zipfile.is_zipfile(path):
            with open(path, "rb") as f:
                return f.read()
        with zipfile.ZipFile(path) as project:
            names = project.namelist()
            plates = [n for n in names if re.fullmatch(r"Metadata/plate_\d+\.gcode", n)]
            name = "Metadata/plate_%d.gcode" % plate
            if name in names:
                return project.read(name)
            if len(plates) == 1:
                return project.read(plates[0])
    sibling = os.path.splitext(path)[0] + ".gcode" if path else ""
    if sibling and sibling != path and os.path.isfile(sibling):
        with open(sibling, "rb") as f:
            return f.read()
    return None


def upload_name(project: str, plate: int) -> str:
    """The file name a print gets on the printer: "<project>_plate_<n>.gcode",
    with nothing in it a G-code parameter or a path would trip on."""
    base = re.sub(r"[^\w.-]+", "_", project or "").strip("._") or "print"
    return "%s_plate_%d.gcode" % (base, max(plate, 1))


@dataclass
class PrintJob:
    """What a print needs from OrcaSlicer's PrintParams, copied on its thread."""
    filename: str = ""
    plate: int = 1
    project: str = ""
    ams_mapping: str = ""
    ams_mapping2: str = ""
    bed_level: bool = False
    flow_calibrate: bool = False
    time_lapse: bool = False
    start: bool = True

    @classmethod
    def from_params(cls, params, start: bool = True) -> "PrintJob":
        return cls(filename=str(params.filename or ""), plate=int(params.plate_index or 0),
                   project=str(params.project_name or params.task_name or ""),
                   ams_mapping=str(params.ams_mapping or ""), ams_mapping2=str(params.ams_mapping2 or ""),
                   bed_level=bool(params.task_bed_leveling), flow_calibrate=bool(params.task_flow_cali),
                   time_lapse=bool(params.task_record_timelapse), start=start)


def prepare(text: str, job: PrintJob, snapshot, by_unit: bool = False) -> str:
    """The file as the printer gets it: the print dialog's trays for its
    filaments (laid out by AFC unit when `by_unit`), AFC's purges and spool
    flow K, and on a U1 its print switches. A printer without AFC gets the
    file as it was sliced, apart from the U1's switches."""
    settings = gcode.FileSettings.from_config(gcode.read_config_block(text))
    if settings.klipper and snapshot is not None and snapshot.present:
        shown = trays.layout(snapshot, by_unit)
        mapping = trays.mapping_from_print(job.ams_mapping, job.ams_mapping2, settings.filaments, shown)
        text = gcode.transform(text, gcode.plan_for(snapshot, settings.filaments, mapping, settings), settings).text
    if snapshot is not None and snapshot.u1_print.present:
        options = protocol.U1PrintOptions(True, job.bed_level, job.flow_calibrate, job.time_lapse)
        text = gcode.insert_after_header(text, protocol.u1_file_print_options_gcode(options))
    return text


class _Progress:
    """The latest print stage, set on the worker and shown from the job's thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._value = None

    def set(self, stage: int, code: int, message: str) -> None:
        with self._lock:
            self._value = (stage, code, message)

    def get(self):
        with self._lock:
            return self._value


# ------------------------------------------------------------------ the agent


@dataclass
class _Printer:
    """The printer OrcaSlicer connected the agent to."""
    dev_id: str
    client: Moonraker
    access_code: str
    model: str = ""
    model_file: str = ""
    model_id: str = ""
    name: str = ""
    version: str = ""
    connected: bool = False
    announced: bool = False
    failures: int = 0
    extruders: list = field(default_factory=lambda: ["extruder"])
    lights: list = field(default_factory=list)        # Klipper objects the lamp switches
    chamber: str = ""                                 # the Klipper object that reads the chamber's temperature
    snapshot: object = None                           # the last AFC snapshot, for the Device tab's controls
    readers: dict = field(default_factory=dict)       # RFID reader module -> {lane: slot}
    reading: set = field(default_factory=set)         # T numbers whose tag is being read
    by_unit: bool = False                             # OrcaSlicer syncs trays by the T number each reports: lay them out by AFC unit
    dry_filaments: dict = field(default_factory=dict) # unit -> the filament the drying popup started it for
    lane_presets: dict = field(default_factory=dict)  # lane -> (preset picked in the tray popup, spool_key())
    lane_k: dict = field(default_factory=dict)        # lane with no Spoolman spool -> (K set in the tray popup, spool_key())
    job: tuple = None                                 # (file, job_info(), when to ask again) of the print file
    camera: tuple = ("", "")                          # controls.camera(): the Device tab's camera mode and URL
    camera_due: float = 0.0                           # when to read the webcam list again
    drawn_tray: str = ""                              # the tray the Device tab draws in the toolhead
    release_until: float = None                       # when release_tray() stops naming a tray that left
    swap: object = field(default_factory=swaptimes.Tracker)  # AFC's measured change times
    stats_due: float = 0.0                            # when to read them again
    nozzles: list = field(default_factory=list)       # toolhead_nozzles() of Klipper's config
    nozzles_due: float = 0.0                          # when to read them again


def spool_key(lane) -> tuple:
    """What tells one spool from the next in a lane: a preset picked for the
    lane holds until the lane gets another spool."""
    return lane.spool_id, (lane.vendor or "").lower(), (lane.filament_name or "").lower()


def filament_setting_reply(request: dict, lane) -> dict:
    """The tray popup's request echoed back, as a Bambu printer reports an
    ams_filament_setting it took. OrcaSlicer reads every field, the temperatures
    as numbers; the lane's own stand in for ones the request lacks."""
    def temp(key):
        value = request.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else max(lane.nozzle_temp, 0)

    ams_id, slot_id = request.get("ams_id"), request.get("slot_id")
    tray_id = request.get("tray_id")
    reply = {"command": "ams_filament_setting", "result": "success",
             "ams_id": ams_id, "slot_id": slot_id, "tray_id": tray_id if isinstance(tray_id, int) else slot_id,
             "tray_info_idx": str(request.get("tray_info_idx") or ""), "tray_type": str(request.get("tray_type") or ""),
             "tray_color": str(request.get("tray_color") or ""),
             "nozzle_temp_min": temp("nozzle_temp_min"), "nozzle_temp_max": temp("nozzle_temp_max")}
    if isinstance(request.get("sequence_id"), str):
        reply["sequence_id"] = request["sequence_id"]
    return reply


_Base = getattr(getattr(orca, "printer_agent", None), "PrinterAgentBase", None)

if _Base is None:  # an OrcaSlicer without printer agents in plugins
    AFCAgent = None
else:
    class AFCAgent(_Base):
        def __init__(self, poll: float = POLL):
            super().__init__()
            self._lock = threading.Lock()
            self._fns = {}            # OrcaSlicer's callbacks by name
            self._printer = None      # _Printer, while connected
            self._selected = ""
            self._candidates = []     # presets.Candidate, read on the UI thread
            self._presets_due = 0.0
            self._match = {}          # (material, sub type, vendor, product) -> filament id
            self._ids = presets.FilamentIds()
            self._manager = AFCManager()
            self._worker = Worker("AFC printer agent", tick=self._poll, every=poll)
            self._gcode = Worker("AFC printer agent G-code")  # long commands, so pushes keep coming

        def get_name(self):
            return "AFC printer agent"

        def get_default_config(self):
            return {}

        def get_agent_info(self):
            from . import __version__
            return orca.printer_agent.AgentInfo(
                AGENT_ID, "AFC (Klipper)", __version__,
                "Klipper with AFC through Moonraker: AFC's lanes as trays, and prints on the lanes the print dialog picks")

        def on_unload(self):
            with self._lock:
                self._printer = None
            self._worker.stop()
            self._gcode.stop()

        # -------------------------------------------------------- OrcaSlicer's callbacks

        def _set(self, name, fn):
            with self._lock:
                self._fns[name] = fn
            return SUCCESS

        def set_server_callback(self, fn):
            return self._set("server", fn)

        def set_on_ssdp_msg_fn(self, fn):
            return self._set("ssdp", fn)

        def set_on_printer_connected_fn(self, fn):
            return self._set("printer_connected", fn)

        def set_on_subscribe_failure_fn(self, fn):
            return self._set("subscribe_failure", fn)

        def set_on_message_fn(self, fn):
            return self._set("message", fn)

        def set_on_user_message_fn(self, fn):
            return self._set("user_message", fn)

        def set_on_local_connect_fn(self, fn):
            return self._set("local_connect", fn)

        def set_on_local_message_fn(self, fn):
            return self._set("local_message", fn)

        def set_queue_on_main_fn(self, fn):
            return self._set("queue_on_main", fn)

        def _fn(self, name):
            with self._lock:
                return self._fns.get(name)

        def _on_main(self, job) -> None:
            """Run job on OrcaSlicer's UI thread. It must not raise there."""
            queue = self._fn("queue_on_main")
            if queue is None:
                return

            def safe():
                try:
                    job()
                except Exception:
                    traceback.print_exc()

            queue(safe)

        def _send(self, printer: _Printer, payload: dict) -> None:
            fn = self._fn("local_message") or self._fn("message")
            if fn is not None and self._current() is printer:
                fn(printer.dev_id, json.dumps(payload))

        def _current(self):
            with self._lock:
                return self._printer

        # -------------------------------------------------------- connecting (UI thread)

        def connect_printer(self, params):
            try:
                bundle = orca.host.preset_bundle()
                model, model_file = printer_model(bundle)
                name = preset_name(bundle, str(params.dev_id or ""))
            except Exception:  # OrcaSlicer is starting up or closing
                bundle, model, model_file, name = None, "", "", ""
            host = str(params.host or "")
            if "://" not in host:
                host = ("https://" if params.use_ssl or _https(bundle) else "http://") + host
            port = str(params.port or "")
            url = base_url(host + (":" + port if port and not re.search(r":\d+$", host) else ""))
            access_code = str(params.password or "") or NO_ACCESS_CODE
            key = "" if access_code == NO_ACCESS_CODE else access_code
            printer = _Printer(str(params.dev_id or ""), Moonraker(url, key), access_code, model, model_file, name=name)
            with self._lock:
                self._printer = printer
            if bundle is not None:
                self._read_presets(bundle)
            with self._lock:
                # Read again on the first poll: OrcaSlicer may connect before it has
                # marked which presets fit a printer it is switching to.
                self._presets_due = 0.0
            # OrcaSlicer lets a plugin start threads on its UI thread only, so both start here.
            self._gcode.submit(lambda: None)
            self._worker.submit(lambda: self._connect(printer))
            return SUCCESS

        def disconnect_printer(self):
            with self._lock:
                self._printer = None
            return SUCCESS

        def _read_presets(self, bundle=None) -> None:
            """OrcaSlicer's filament presets for this printer. UI thread."""
            try:
                candidates = presets.collect(bundle if bundle is not None else orca.host.preset_bundle())
            except Exception:  # starting up or closing
                return
            with self._lock:
                self._candidates, self._match = candidates, {}
                self._presets_due = time.monotonic() + PRESETS_EVERY

        # -------------------------------------------------------- the worker

        def _connect(self, printer: _Printer) -> None:
            if self._current() is not printer or printer.connected:
                return
            try:
                info = printer.client.get("/printer/info")
            except MoonrakerError:
                printer.failures += 1  # tried again on the next poll
                return
            info = info if isinstance(info, dict) else {}
            printer.version = str(info.get("software_version") or "")
            printer.name = printer.name or str(info.get("hostname") or "") or printer.model or "AFC printer"
            try:
                listed = printer.client.objects_list()
                objects = listed.get("objects") if isinstance(listed, dict) else listed
                printer.lights = controls.find_lights(objects if isinstance(objects, list) else [])
                printer.chamber = controls.find_chamber(objects if isinstance(objects, list) else [])
            except MoonrakerError:
                pass
            if not printer.model_id:
                printer.model_id = model_id(printer.model, printer.model_file)
            printer.connected, printer.failures = True, 0
            announce = None
            if not printer.announced:
                printer.announced = True
                # No model_id is announced as none: OrcaSlicer then skips its check that the
                # printer is the preset's model, which a model's name would always fail.
                announce = announcement(printer.dev_id, printer.name, printer.model_id)

            def connected():
                if self._current() is not printer:
                    return
                ssdp, local, done = self._fn("ssdp"), self._fn("local_connect"), self._fn("printer_connected")
                if announce and ssdp is not None:
                    ssdp(announce)
                if local is not None:
                    local(CONNECT_OK, printer.dev_id, "0")
                if done is not None:
                    done(printer.dev_id)

            self._on_main(connected)

        def _poll(self) -> None:
            printer = self._current()
            if printer is None:
                return
            if not printer.connected:
                self._connect(printer)
                return
            self._push(printer)
            with self._lock:
                due = time.monotonic() >= self._presets_due
                if due:
                    self._presets_due = time.monotonic() + PRESETS_EVERY
            if due:
                self._on_main(self._read_presets)

        def _push(self, printer: _Printer) -> None:
            if self._current() is not printer or not printer.connected:
                return
            # AFC first: it names the toolheads, whose temperatures come next.
            snapshot = self._manager.read(printer.client)
            if snapshot is not None and snapshot.extruders:
                printer.extruders = list(snapshot.extruders)
            try:
                objects = printer.client.objects_query(status_query(printer.extruders, printer.lights, printer.chamber))
            except MoonrakerError:
                printer.failures += 1
                if printer.failures >= LOST_AFTER:
                    printer.connected = False  # connected again once it answers

                    def lost():
                        local = self._fn("local_connect")
                        if local is not None and self._current() is printer:
                            local(CONNECT_LOST, printer.dev_id, "lost")

                    self._on_main(lost)
                return
            printer.failures = 0
            status = (objects or {}).get("status") if isinstance(objects, dict) else None
            status = status if isinstance(status, dict) else {}
            printer.readers = controls.reader_lanes(status)
            if snapshot is not None:
                self._kept_k(printer, snapshot)
                printer.snapshot = snapshot
            ids, extras = self._trays(printer, snapshot) if snapshot is not None else ({}, {})
            with self._lock:
                reading = set(printer.reading)
                dry_filaments = dict(printer.dry_filaments)
            job = self._job(printer, (status.get("print_stats") or {}).get("filename"))
            self._camera(printer)
            self._swap_times(printer)
            self._nozzles(printer)
            message = push_status(status, snapshot, ids, extras, reading, printer.lights, job,
                                  printer.by_unit, dry_filaments, printer.chamber, printer.swap.times(),
                                  printer.nozzles)
            release_tray(message["print"], printer, time.monotonic())
            self._send(printer, message)

        def _job(self, printer: _Printer, filename) -> dict:
            """job_info() of the file being printed, read once per file, and
            again every JOB_RETRY s while Moonraker has no estimate for it (its
            metadata comes a moment after an upload)."""
            if not isinstance(filename, str) or not filename:
                return {}
            known = printer.job
            if known is not None and known[0] == filename and ("estimated_time" in known[1] or time.monotonic() < known[2]):
                return known[1]
            try:
                metadata = printer.client.file_metadata(filename)
            except MoonrakerError:
                metadata = None
            info = job_info(metadata, filename, printer.client.url)
            # OrcaSlicer loads the thumbnail without the API key: one the printer refuses shows as a broken image.
            if "thumbnail_url" in info and not opens_without_key(info["thumbnail_url"]):
                del info["thumbnail_url"]
            printer.job = (filename, info, time.monotonic() + JOB_RETRY)
            return info

        def _camera(self, printer: _Printer) -> None:
            """The webcam the Device tab plays, read every CAMERA_EVERY s; a failed read keeps the last."""
            if time.monotonic() < printer.camera_due:
                return
            printer.camera_due = time.monotonic() + CAMERA_EVERY
            try:
                camera = controls.camera(printer.client.webcams(), printer.client.url)
            except MoonrakerError:
                return
            with self._lock:
                printer.camera = camera

        def _swap_times(self, printer: _Printer) -> None:
            """AFC's change times (swaptimes.py): AFC.log once a connection, its
            stats every STATS_EVERY s."""
            if time.monotonic() < printer.stats_due:
                return
            printer.stats_due = time.monotonic() + STATS_EVERY
            if not printer.swap.seeded:
                try:
                    printer.swap.seed(printer.client.log_tail("AFC.log", swaptimes.LOG_TAIL))
                except MoonrakerError:
                    printer.swap.seeded = True  # no log: only the changes seen while connected count
            try:
                printer.swap.update(printer.client.database_namespace("afc_stats"))
            except MoonrakerError:
                pass

        def _nozzles(self, printer: _Printer) -> None:
            """The toolheads' nozzle sizes, read every NOZZLES_EVERY s; a failed read keeps the last."""
            if time.monotonic() < printer.nozzles_due:
                return
            printer.nozzles_due = time.monotonic() + NOZZLES_EVERY
            try:
                answer = printer.client.objects_query({"configfile": ["settings"]})
            except MoonrakerError:
                return
            try:
                settings = answer["status"]["configfile"]["settings"]
            except (KeyError, TypeError):
                return
            printer.nozzles = toolhead_nozzles(settings) or printer.nozzles

        def _kept_k(self, printer: _Printer, snapshot) -> None:
            """Gives each lane with no Spoolman spool the K set for it in the tray
            popup, which sync then puts in its filament slot and a print applies,
            until the lane gets a spool or another filament."""
            with self._lock:
                for name, (k, key) in list(printer.lane_k.items()):
                    lane = snapshot.find_lane(name)
                    if lane is None or not lane.has_filament or lane.spool_id > 0 or spool_key(lane) != key:
                        del printer.lane_k[name]
                    else:
                        lane.flow_k = k

        def _trays(self, printer: _Printer, snapshot) -> tuple:
            """For each loaded tray: the filament id of the preset that fits its
            spool best, and the fields OrcaSlicer's sync makes the spool's own
            preset from, with the preset picked for the lane in the tray popup."""
            with self._lock:
                candidates, cache = self._candidates, self._match
                for name, (_, key) in list(printer.lane_presets.items()):
                    lane = snapshot.find_lane(name)
                    if lane is None or not lane.has_filament or spool_key(lane) != key:
                        del printer.lane_presets[name]
                picked = {name: preset for name, (preset, _) in printer.lane_presets.items()}
            ids, extras = {}, {}
            for tray, lane in trays.lanes_by_tray(snapshot).items():
                if not lane.has_filament:
                    continue
                key = (lane.material, lane.sub_type, lane.vendor, lane.filament_name)
                if key not in cache:
                    cache[key] = (presets.match(lane, candidates, self._ids), presets.best(lane, candidates))
                ids[tray], base = cache[key]
                extra = {}
                names = presets.spool_preset_names(lane)
                if names:
                    extra["spool"] = trays.spool_json(lane, names, base)
                if lane.name in picked:
                    extra["filament_preset"] = picked[lane.name]
                extras[tray] = extra
            return ids, extras

        # -------------------------------------------------------- requests

        def send_message(self, dev_id, json_str, qos, flag):
            return self._request(dev_id, json_str)

        def send_message_to_printer(self, dev_id, json_str, qos, flag):
            return self._request(dev_id, json_str)

        def _request(self, dev_id: str, json_str: str) -> int:
            try:
                data = json.loads(json_str)
            except ValueError:
                return ERR_INVALID_RESULT
            if not isinstance(data, dict):
                return ERR_INVALID_RESULT
            printer = self._current()
            if printer is None or (dev_id and dev_id != printer.dev_id):
                return FAILED

            def part(key):
                value = data.get(key)
                return value if isinstance(value, dict) else {}

            info, system, pushing, job = part("info"), part("system"), part("pushing"), part("print")
            if info.get("command") == "get_version":
                reply = {"info": {"command": "get_version", "sequence_id": info.get("sequence_id", ""),
                                  "result": "success",
                                  "module": [{"name": "ota", "sw_ver": printer.version or "1.0.0",
                                              "product_name": "Klipper"}]}}
                self._worker.submit(lambda: self._send(printer, reply))
            elif system.get("command") == "ledctrl":
                return self._lamp(printer, system)
            elif system.get("command") == "get_access_code":
                reply = {"system": {"command": "get_access_code", "sequence_id": system.get("sequence_id", ""),
                                    "access_code": printer.access_code}}
                self._worker.submit(lambda: self._send(printer, reply))
            elif pushing.get("command") == "pushall":
                if pushing.get("tray_tool") is True:
                    printer.by_unit = True
                self._worker.submit(lambda: self._push(printer))
            elif job.get("command"):
                return self._print_command(printer, job)
            return SUCCESS

        def _print_command(self, printer: _Printer, job: dict) -> int:
            command = job.get("command")
            if command == "gcode_line":
                script = job.get("param")
                if not isinstance(script, str):
                    return ERR_INVALID_RESULT
                reply = {"command": "gcode_line", "param": script}
                if isinstance(job.get("sequence_id"), str):
                    reply["sequence_id"] = job["sequence_id"]

                def run():
                    try:
                        printer.client.gcode(script, timeout=LANE_MOVE_TIMEOUT)
                        reply["result"] = "success"
                    except MoonrakerError:
                        reply["result"] = "failed"
                    self._send(printer, {"print": reply})

                self._gcode.submit(run)
            elif command in _PRINT_ENDPOINTS:
                self._gcode.submit(lambda: self._quietly(lambda: printer.client.post(_PRINT_ENDPOINTS[command], {},
                                                                                     timeout=LANE_MOVE_TIMEOUT)))
            elif command == "set_bed_temp" and _number(job.get("temp")) is not None:
                self._run(printer, "SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=%d" % job["temp"])
            elif command == "set_nozzle_temp" and _number(job.get("target_temp")) is not None:
                index = job.get("extruder_index")
                heater = "extruder%d" % index if isinstance(index, int) and index > 0 else "extruder"
                self._run(printer, "SET_HEATER_TEMPERATURE HEATER=%s TARGET=%d" % (heater, job["target_temp"]))
            elif command == "home":
                self._run(printer, "G28")
            elif command == "clear_agent_message":
                self._run(printer, "AFC_CLEAR_MESSAGE", push=True)
            elif command == "ams_filament_setting":
                return self._filament_setting(printer, job)
            elif command == "extrusion_cali_set":
                return self._tray_k(printer, job)
            elif command == "ams_filament_drying":
                return self._drying(printer, job)
            elif command == "ams_change_filament":
                try:
                    script = controls.change_filament_gcode(printer.snapshot, job, self._layout(printer)) if printer.snapshot else ""
                except AFCError:
                    script = ""
                if not script:
                    return FAILED
                self._run(printer, script, push=True)
            return SUCCESS

        def _run(self, printer: _Printer, script: str, push: bool = False) -> None:
            def run():
                self._quietly(lambda: printer.client.gcode(script, timeout=LANE_MOVE_TIMEOUT))
                if push:
                    self._worker.submit(lambda: self._push(printer))

            self._gcode.submit(run)

        # -------------------------------------------------------- the Device tab's controls

        def _filament_setting(self, printer: _Printer, job: dict) -> int:
            """The tray popup: the lane takes its color and material, and the
            preset picked for it until it gets another spool. A reset (no
            filament) drops the pick."""
            lane = controls.lane_of(printer.snapshot, self._layout(printer).tool(job.get("ams_id"), job.get("slot_id")))
            if lane is None:
                return FAILED
            reset = not job.get("tray_info_idx")
            preset = job.get("filament_preset")
            try:
                script = [] if reset else controls.filament_setting_gcode(printer.snapshot, lane, job)
            except AFCError:
                return ERR_INVALID_RESULT
            with self._lock:
                if reset:
                    printer.lane_presets.pop(lane.name, None)
                elif isinstance(preset, str) and preset:
                    printer.lane_presets[lane.name] = (preset, spool_key(lane))
            if not reset:
                # Echoed as a Bambu printer does: OrcaSlicer shows the tray's new filament from the
                # echo and holds it through the next three pushes, which would otherwise keep it.
                reply = filament_setting_reply(job, lane)
                self._worker.submit(lambda: self._send(printer, {"print": reply}))
            if script:
                self._run(printer, "\n".join(script), push=True)
            else:
                self._worker.submit(lambda: self._push(printer))
            return SUCCESS

        def _tray_k(self, printer: _Printer, job: dict) -> int:
            """The tray popup's Factor K: saved on the lane's Spoolman spool as
            its flow K, which filament sync puts in the slot and AFC autocal
            applies when the lane loads. A lane without a spool keeps it here
            (_kept_k()) for sync and prints, until OrcaSlicer closes."""
            tray, k = job.get("tray_id"), _number(job.get("k_value"))
            # The popup names the tray unit * 4 + slot, an AMS HT's too.
            tool = self._layout(printer).tool(tray // 4, tray % 4) if isinstance(tray, int) and tray >= 0 else -1
            lane = controls.lane_of(printer.snapshot, tool)
            if lane is None or k is None or not 0 < k < 2:
                return ERR_INVALID_RESULT
            # The popup shows K to four places: one confirmed unchanged keeps the spool's own.
            if lane.flow_k >= 0 and abs(k - lane.flow_k) < 0.00005:
                return SUCCESS
            if lane.spool_id <= 0:
                with self._lock:
                    printer.lane_k[lane.name] = (k, spool_key(lane))
                self._worker.submit(lambda: self._push(printer))
                return SUCCESS
            spoolman = protocol.spoolman_for_printer(printer.snapshot.spoolman_url, printer.client.url)

            def run():
                self._manager.save_flow_k(printer.client, lane.spool_id, k, spoolman)
                self._push(printer)

            self._worker.submit(run)
            return SUCCESS

        def _layout(self, printer: _Printer) -> trays.Layout:
            """The units and slots the Device tab shows AFC's T numbers in."""
            return trays.layout(printer.snapshot, printer.by_unit)

        def _drying(self, printer: _Printer, job: dict) -> int:
            """The drying popup of a unit with a dryer: starts it (mode 1, for
            `duration` hours) or stops it (mode 0). OrcaSlicer's popup allows an
            AMS 2 Pro's 65 C; a unit that dries cooler (an ACE's 55 C) dries at its most."""
            box = self._layout(printer).box(job.get("ams_id"))
            unit = printer.snapshot.find_unit(box.unit) if box is not None and box.unit and printer.snapshot else None
            if unit is None or unit.dryer is None:
                return ERR_CAP_NOT_AVAILABLE
            if job.get("mode") == 0:
                request = {"action": "stop"}
            elif not unit.dryer.online:
                return ERR_CAP_NOT_AVAILABLE  # ACE_DRY would only answer "ACE not connected"
            else:
                temp, hours = _number(job.get("temp")), _number(job.get("duration"))
                if temp is None or hours is None:
                    return ERR_INVALID_RESULT
                if unit.dryer.max_temp > 0:
                    temp = min(temp, unit.dryer.max_temp)
                request = {"action": "start", "temp": temp, "minutes": hours * 60, "rotate": job.get("rotate_tray") is True}
            try:
                script = protocol.build_dry_gcode(printer.snapshot, unit.name, request)
            except AFCError:
                return ERR_INVALID_RESULT
            filament = job.get("filament")
            with self._lock:
                printer.dry_filaments[unit.name] = filament if request["action"] == "start" and isinstance(filament, str) else ""
            self._run(printer, script, push=True)
            return SUCCESS

        def _lamp(self, printer: _Printer, system: dict) -> int:
            """The lamp switches every light find_lights() found. OrcaSlicer sends
            chamber_light2 alongside chamber_light; the one switch does both."""
            mode = system.get("led_mode")
            if system.get("led_node") != "chamber_light" or mode not in ("on", "off"):
                return SUCCESS
            if not printer.lights:
                return ERR_CAP_NOT_AVAILABLE
            self._run(printer, "\n".join(controls.light_gcode(printer.lights, mode == "on")), push=True)
            return SUCCESS

        def command_ams_refresh_rfid(self, dev_id, ams_id, slot_id, sequence_id, lan_mode):
            """A tray's refresh button: the lane's unit reads the spool's tag
            again (controls.rfid_scan_gcode()), the tray spinning meanwhile."""
            printer = self._current()
            if printer is None or not printer.connected:
                return FAILED
            # OrcaSlicer names the tray (ams_id -1) or the unit and slot.
            shown = self._layout(printer)
            tool = shown.tool_of_tray(slot_id) if isinstance(ams_id, int) and ams_id < 0 else shown.tool(ams_id, slot_id)
            lane = controls.lane_of(printer.snapshot, tool)
            script = controls.rfid_scan_gcode(lane, printer.readers) if lane is not None else ""
            if not script:
                return ERR_CAP_NOT_AVAILABLE
            with self._lock:
                printer.reading.add(tool)

            def run():
                try:
                    self._quietly(lambda: printer.client.gcode(script, timeout=LANE_MOVE_TIMEOUT))
                finally:
                    with self._lock:
                        printer.reading.discard(tool)
                    self._worker.submit(lambda: self._push(printer))

            self._worker.submit(lambda: self._push(printer))
            self._gcode.submit(run)
            return SUCCESS

        @staticmethod
        def _quietly(call) -> None:
            try:
                call()
            except MoonrakerError:
                pass  # the next push shows the printer as it is

        # -------------------------------------------------------- printing (a job thread)

        def start_send_gcode_to_sdcard(self, params, update_fn, cancel_fn, wait_fn):
            if params.project_name == "verify_job":
                # The print dialog's LAN check: the agent reaches the printer, or the dialog asks for its address.
                printer = self._current()
                return SUCCESS if printer is not None and printer.connected else FAILED
            return self._print(PrintJob.from_params(params, start=False), update_fn, cancel_fn)

        def start_local_print(self, params, update_fn, cancel_fn):
            return self._print(PrintJob.from_params(params), update_fn, cancel_fn)

        def start_local_print_with_record(self, params, update_fn, cancel_fn, wait_fn):
            return self._print(PrintJob.from_params(params), update_fn, cancel_fn)

        def start_print(self, params, update_fn, cancel_fn, wait_fn):
            return self._print(PrintJob.from_params(params), update_fn, cancel_fn)

        def start_sdcard_print(self, params, update_fn, cancel_fn):
            return FAILED  # printing a file already on the printer is not something this agent offers

        def _print(self, job: PrintJob, update_fn, cancel_fn) -> int:
            """Hand the print to the worker and wait here, on OrcaSlicer's job
            thread, showing its stages and passing on a cancel."""
            printer = self._current()
            if printer is None:
                return FAILED
            progress, canceled, done, result = _Progress(), threading.Event(), threading.Event(), {}

            def work():
                try:
                    result["code"] = self._send_print(printer, job, progress, canceled)
                except Exception:
                    traceback.print_exc()
                    result["code"] = FAILED
                finally:
                    done.set()

            try:
                self._worker.submit(work)
            except Exception:  # its thread is gone, and OrcaSlicer refuses a new one here
                traceback.print_exc()
                return FAILED
            shown = None
            while True:
                finished = done.wait(0.2)
                stage = progress.get()
                if stage is not None and stage != shown and update_fn is not None:
                    update_fn(*stage)
                    shown = stage
                if finished:
                    return result.get("code", FAILED)
                if cancel_fn is not None and not canceled.is_set() and cancel_fn():
                    canceled.set()

        def _send_print(self, printer: _Printer, job: PrintJob, progress: _Progress, canceled) -> int:
            progress.set(STAGE_CREATE, 0, "Preparing...")
            try:
                data = plate_gcode(job.filename, job.plate)
            except (OSError, zipfile.BadZipFile, KeyError):
                data = None
            if data is None:
                return ERR_FILE_NOT_EXIST
            # surrogateescape gives back the file byte for byte where nothing changed.
            snapshot = self._manager.read(printer.client)
            if snapshot is not None:
                self._kept_k(printer, snapshot)
            text = prepare(data.decode("utf-8", "surrogateescape"), job, snapshot, printer.by_unit)
            name = upload_name(job.project, job.plate)
            if canceled.is_set():
                return ERR_CANCELED
            progress.set(STAGE_UPLOAD, 0, "0%")

            def sent(done, total):
                if canceled.is_set():
                    return False
                percent = int(done * 100 / total) if total else 100
                progress.set(STAGE_UPLOAD, percent, "%d%%" % percent)
                return True

            try:
                printer.client.upload(name, text.encode("utf-8", "surrogateescape"), sent)
            except MoonrakerError:
                return ERR_CANCELED if canceled.is_set() else ERR_UPLOAD_FAILED
            if not job.start:
                return SUCCESS
            if canceled.is_set():
                return ERR_CANCELED
            progress.set(STAGE_SENDING, 0, "Starting print...")
            try:
                printer.client.post("/printer/print/start", {"filename": name}, timeout=30)
            except MoonrakerError:
                return ERR_START_FAILED
            self._worker.submit(lambda: self._push(printer))
            return SUCCESS

        # -------------------------------------------------------- the rest

        # OrcaSlicer asks for the camera on its UI thread at every Device tab update: these answer from the last read.
        def get_camera_stream_mode(self):
            printer = self._current()
            with self._lock:
                mode = printer.camera[0] if printer is not None else ""
            modes = orca.printer_agent.CameraStreamMode
            return {"http": modes.HTTP, "http_snapshot": modes.HTTP_SNAPSHOT}.get(mode, modes.None_)

        def get_camera_url(self):
            printer = self._current()
            with self._lock:
                return printer.camera[1] if printer is not None else ""

        def get_filament_sync_mode(self):
            # A push with the trays makes OrcaSlicer read them again (DevFilaSystem), no pull needed.
            return orca.printer_agent.FilamentSyncMode.Subscription

        def fetch_filament_info(self, dev_id, sync_mode):
            printer = self._current()
            if printer is None:
                return False
            self._read_presets()  # filaments made visible since the last read count now
            self._worker.submit(lambda: self._push(printer))
            return True

        def start_discovery(self, start, sending):
            if start:
                self._on_main(self._claim)
            return True

        def _claim(self) -> None:
            """Announce the printer preset's printer when OrcaSlicer selected
            none. UI thread.

            OrcaSlicer selects the preset's printer as it starts discovery, but
            a printer it met through another agent (Moonraker, Snapmaker) stays
            in that agent's device list, so the selection fails and filament
            sync does nothing. Announcing the printer moves it to this agent's
            list, where OrcaSlicer selects it then or on its next start. One
            the other agent kept without an access code (Moonraker with no API
            key) stays out of this agent's list all the same."""
            ssdp = self._fn("ssdp")
            if ssdp is None or self._current() is not None:
                return
            try:
                if orca.host.selected_printer() is not None:
                    return
                bundle = orca.host.preset_bundle()
                preset = bundle.current_printer_preset()
                host = _first(preset.config_value("print_host")) if preset is not None else ""
                port = _first(preset.config_value("printhost_port")) if host else ""
            except Exception:  # starting up or closing
                return
            if not host:
                return
            model, model_file = printer_model(bundle)
            dev_id = dev_id_from_address(host, port)
            ssdp(announcement(dev_id, str(preset.name or "") or model or dev_id, model_id(model, model_file)))

        def bind_detect(self, dev_ip, sec_link, detect):
            printer = self._current()
            known = printer is not None and dev_ip in (printer.dev_id, "")
            detect.dev_id = printer.dev_id if known else dev_ip
            detect.dev_name = (printer.name or printer.dev_id) if known else dev_ip
            detect.model_id = printer.model_id if known else ""
            detect.version = printer.version if known else ""
            detect.connect_type = "lan"
            detect.bind_state = "free"
            return SUCCESS

        def get_user_selected_machine(self):
            with self._lock:
                return self._selected

        def set_user_selected_machine(self, dev_id):
            with self._lock:
                self._selected = str(dev_id or "")
            return SUCCESS

        def check_cert(self):
            return SUCCESS

        def ping_bind(self, ping_code):
            return SUCCESS

        def bind(self, dev_ip, dev_id, dev_model, sec_link, timezone, improved, update_fn):
            return SUCCESS

        def unbind(self, dev_id):
            return SUCCESS
