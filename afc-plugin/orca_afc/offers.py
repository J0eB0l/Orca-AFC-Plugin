"""Settings an AFC printer is better off with, offered once a session per
preset in a plater notification, for one click to apply:

- AFC's own settings: AFC unloads and loads each lane itself, cutting or
  forming the tip, and its PRINT_START loads the first tool, so ramming,
  cooling moves and toolchange retraction go off and PRINT_START gets
  TOOL={initial_tool} (afcproject.dev, slicer setup).
- Preheating on a toolchanger: ooze prevention heats the next tool ahead
  of each tool change.
- Klipper's motion limits: the printer preset's machine limits that differ
  from Klipper's [printer] section, which the time estimate should use.

A click applies the change through OrcaSlicer's preset API, as an unsaved
edit of the printer (or process) preset, the same as editing it by hand.
A printer whose macros handle these itself keeps its settings by not
clicking; the printer preset's "afc_settings_check" turns the offers off.

The offers are worked out on OrcaSlicer's UI thread, where presets are
read; the API call runs on a plugin thread, as the API blocks its caller
while OrcaSlicer's UI settles.
"""

from __future__ import annotations

import re
import threading

import orca

AFC_OFF = ("enable_filament_ramming", "high_current_on_filament_swap")
AFC_ZERO = ("cooling_tube_retraction", "cooling_tube_length", "parking_pos_retraction", "extra_loading_move")
AFC_ZERO_EACH = ("retract_length_toolchange", "retract_restart_extra_toolchange")  # per extruder

# Klipper's [printer] limit -> the machine limits it sets. Klipper has no
# stealth mode, so every value of an option takes Klipper's. The toolheads'
# offsets stay out: Klipper applies them itself on a tool change.
LIMITS = {
    "max_velocity": ("machine_max_speed_x", "machine_max_speed_y"),
    "max_z_velocity": ("machine_max_speed_z",),
    "max_accel": ("machine_max_acceleration_x", "machine_max_acceleration_y",
                  "machine_max_acceleration_extruding", "machine_max_acceleration_travel"),
    "max_z_accel": ("machine_max_acceleration_z",),
    "square_corner_velocity": ("machine_max_jerk_x", "machine_max_jerk_y"),
}

START_LINE = re.compile(r"^[ \t]*(?:PRINT_START|START_PRINT)\b[^;\r\n]*", re.IGNORECASE | re.MULTILINE)
NAMES_TOOL = re.compile(r"\b(?:INITIAL_)?TOOL=", re.IGNORECASE)

AFC_TEXT = ("Some printer settings differ from what AFC recommends: AFC unloads and loads each lane itself, "
            "cutting or forming the tip, and its PRINT_START loads the first tool.")
PREHEAT_TEXT = ("OrcaSlicer can keep idle tools a little cooler and heat the next tool ahead of each tool change, "
                "so it is hot when it is picked up. Leave this off when the printer's macros handle tool temperatures.")
LIMITS_TEXT = ("This printer's maximum speeds and accelerations differ from the ones in its Klipper config, "
               "which the time estimate should use.")


def unescape(text: str) -> str:
    """A string setting's value as OrcaSlicer serializes it (C-style escapes)."""
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            i += 1
            c = {"n": "\n", "r": "\r"}.get(text[i], text[i])
        out.append(c)
        i += 1
    return "".join(out)


def escape(text: str) -> str:
    """unescape() undone, as OrcaSlicer reads a string setting back."""
    return text.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n")


def _numbers(value) -> list:
    """The values of a serialized number setting ("0.8,0.8"); None for a nil one."""
    out = []
    for part in str(value or "").split(","):
        try:
            out.append(float(part))
        except ValueError:
            out.append(None)
    return out


def _number_text(value: float) -> str:
    return ("%.6f" % value).rstrip("0").rstrip(".")


def afc_changes(get) -> dict:
    """The printer settings that differ from AFC's recommendations, as
    {key: serialized value}. get(key) is the printer preset's serialized
    value, None for a setting it lacks."""
    changes = {}
    for key in AFC_OFF:
        if get(key) == "1":
            changes[key] = "0"
    for key in AFC_ZERO:
        value = get(key)
        if value is not None and any(v for v in _numbers(value)):
            changes[key] = "0"
    for key in AFC_ZERO_EACH:
        value = get(key)
        if value is not None and any(v for v in _numbers(value)):
            changes[key] = ",".join("0" for _ in _numbers(value))
    start = get("machine_start_gcode")
    if start is not None:
        text = unescape(start)
        line = START_LINE.search(text)
        if line and not NAMES_TOOL.search(line.group(0)):
            end = line.start() + len(line.group(0).rstrip(" \t"))
            changes["machine_start_gcode"] = escape(text[:end] + " TOOL={initial_tool}" + text[end:])
    return changes


def limit_changes(get, limits: dict) -> dict:
    """The machine limits that differ from Klipper's (agent.klipper_limits()),
    as {key: serialized value}, every value of each taking Klipper's."""
    changes = {}
    for klipper, keys in LIMITS.items():
        limit = limits.get(klipper) if isinstance(limits, dict) else None
        if limit is None:
            continue
        for key in keys:
            values = _numbers(get(key)) if get(key) is not None else []
            if values and any(v is None or abs(v - limit) >= 0.001 for v in values):
                changes[key] = ",".join(_number_text(limit) for _ in values)
    return changes


def wants_preheat(printer_get, process_get) -> bool:
    """Whether a toolchanger's process preset has ooze prevention off."""
    return (len(_numbers(printer_get("nozzle_diameter"))) > 1 and printer_get("single_extruder_multi_material") != "1"
            and process_get("ooze_prevention") == "0")


def _getter(preset):
    def get(key):
        value = preset.config_value(key) if preset is not None else None
        return None if value is None else str(value)
    return get


class Offers:
    """Each offer once a session per preset. check() and check_limits() run
    on OrcaSlicer's UI thread; `run` hands the preset edit to a plugin
    thread."""

    def __init__(self, run):
        self._run = run
        self._lock = threading.Lock()
        self._shown = set()  # (offer, preset name)

    def _once(self, offer: str, name: str) -> bool:
        with self._lock:
            if (offer, name) in self._shown:
                return False
            self._shown.add((offer, name))
            return True

    @staticmethod
    def _wanted(printer) -> bool:
        """An AFC printer with the offers on. A printer setting OrcaSlicer
        lacks (an OrcaSlicer without AFC's own settings) counts as on, as the
        AFC agent serving the printer says it has AFC."""
        get = _getter(printer)
        return get("afc_enabled") != "0" and get("afc_settings_check") != "0"

    def check(self, bundle) -> None:
        """Offer AFC's settings, and preheating on a toolchanger, for the
        printer preset being edited."""
        printer = bundle.current_printer_preset()
        if printer is None or not self._wanted(printer):
            return
        name = str(printer.name)
        if afc_changes(_getter(printer)) and self._once("afc", name):
            self._notify(AFC_TEXT, "Use AFC's settings", "printer",
                         lambda b: afc_changes(_getter(b.current_printer_preset())))
        process = bundle.current_process_preset()
        if process is not None and wants_preheat(_getter(printer), _getter(process)) and self._once("preheat", name):
            self._notify(PREHEAT_TEXT, "Turn on preheating", "process",
                         lambda b: {"ooze_prevention": "1"} if _getter(b.current_process_preset())("ooze_prevention") == "0" else {})

    def check_limits(self, bundle, limits: dict) -> None:
        """Offer Klipper's motion limits for the printer preset being edited."""
        printer = bundle.current_printer_preset()
        if not limits or printer is None or not self._wanted(printer):
            return
        if limit_changes(_getter(printer), limits) and self._once("limits", str(printer.name)):
            limits = dict(limits)
            self._notify(LIMITS_TEXT, "Use Klipper's limits", "printer",
                         lambda b: limit_changes(_getter(b.current_printer_preset()), limits))

    def _notify(self, text: str, link: str, category: str, changes) -> None:
        def clicked():
            # UI thread: read what to change from the preset as it is now.
            try:
                bundle = orca.host.preset_bundle()
                preset = bundle.current_printer_preset() if category == "printer" else bundle.current_process_preset()
                name, wanted = str(preset.name), changes(bundle)
            except Exception:  # closing
                return True
            if wanted:
                self._run(lambda: apply(category, name, wanted))
            return True

        ui = orca.host.ui
        ui.push_notification(ui.NotificationLevel.ImportantNotificationLevel, text, link, clicked)


def apply(category: str, name: str, changes: dict) -> None:
    """Put the changes on the preset being edited, unless another one is by
    now. Plugin thread."""
    try:
        transaction = getattr(orca.host.preset, category).begin_update()
        if str(transaction.target_name) != name:
            return
        for key, value in changes.items():
            transaction.set(key, value)
        transaction.commit()
    except Exception as error:  # closing, or the user switched presets
        print("AFC: could not apply the %s settings: %s" % (category, error))
