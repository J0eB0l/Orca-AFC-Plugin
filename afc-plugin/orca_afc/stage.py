"""What the printer is doing right now, for the Device tab's stage line under
the print's progress bar, as a Bambu printer reports it.

A Bambu printer sends a stage number (stg_cur) that OrcaSlicer turns into its
own words, e.g. "Heatbed preheating". Klipper has no such number, so it is
read off the printer: AFC's own state for a tool change, Klipper's heaters,
homed axes, the probe lines a bed mesh prints, and a macro's M117 message.
Each stage goes out as the nearest Bambu number, which any OrcaSlicer shows,
and in AFC's own words with its numbers (stg_text), which the AFC build of
OrcaSlicer shows instead."""

# Bambu's stage numbers (DeviceManager.cpp get_stage_string()).
PRINTING = 0
BED_LEVELING = 1
BED_HEATING = 2
CHANGING_FILAMENT = 4
RUNOUT_PAUSE = 6
NOZZLE_HEATING = 7
HOMING = 13
USER_PAUSE = 16
UNLOADING = 22
LOADING = 24

#: Degrees below its target a heater still reads as heating up.
BED_BELOW = 2.0
NOZZLE_BELOW = 3.0

#: The AFC fields the stage reads (status_query() asks for them).
AFC_FIELDS = ["current_state", "current_lane", "next_lane", "current_toolchange",
              "number_of_toolchanges"]


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def toolhead_name(extruder: str, toolheads: int) -> str:
    """'toolhead 2' for extruder1 on a printer with more than one toolhead,
    'the nozzle' on a printer with one."""
    if toolheads < 2:
        return "the nozzle"
    digits = "".join(c for c in extruder if c.isdigit())
    return "toolhead %d" % ((int(digits) if digits else 0) + 1)


def _lane_name(snapshot, name: str) -> str:
    """'lane8 (T4)', or the bare lane name when it has no T number."""
    lane = snapshot.find_lane(name) if snapshot is not None and name else None
    tools = [t for t in (lane.maps if lane else []) if t and t.upper() != "NONE"]
    return "%s (%s)" % (name, tools[0]) if tools else name


class ProbeWatch:
    """Whether the printer probed its bed since the last look, from the probe
    lines (Klipper's "probe at x, y is z") in Moonraker's G-code store: a bed
    mesh, a Z tilt or a screws check prints one every second or two."""

    def __init__(self) -> None:
        self.seen = None  # newest store time already looked at, None before the first look

    def update(self, entries) -> bool:
        """True when a probe line came in since the last call. The first call
        only notes where the store is, so old lines never count."""
        entries = [e for e in entries or []
                   if isinstance(e, dict) and _number(e.get("time")) is not None]
        newest = max((_number(e["time"]) for e in entries), default=None)
        if self.seen is None:  # an empty store: every line to come is new
            self.seen = newest if newest is not None else 0.0
            return False
        last = self.seen
        if newest is None or newest <= last:
            return False
        self.seen = newest
        fresh = [e for e in entries if _number(e["time"]) > last]
        probing = False
        for entry in fresh:
            text = str(entry.get("message") or "").lower()
            if "probe at" in text:
                probing = True
            elif "leveling complete" in text:
                probing = False
        return probing


def _heating(objects: dict, extruder: str, toolheads: int):
    """(code, text) of a heater the print waits on: the bed, then the active
    toolhead's nozzle. None when both are at temperature."""
    bed = objects.get("heater_bed") if isinstance(objects.get("heater_bed"), dict) else {}
    temp, target = _number(bed.get("temperature")), _number(bed.get("target"))
    if temp is not None and target and temp < target - BED_BELOW:
        return BED_HEATING, "Heating the bed to %d °C (now %d °C)" % (round(target), round(temp))
    nozzle = objects.get(extruder) if isinstance(objects.get(extruder), dict) else {}
    temp, target = _number(nozzle.get("temperature")), _number(nozzle.get("target"))
    if temp is not None and target and temp < target - NOZZLE_BELOW:
        return NOZZLE_HEATING, "Heating %s to %d °C (now %d °C)" % (
            toolhead_name(extruder, toolheads), round(target), round(temp))
    return None


def _afc_stage(afc: dict, snapshot, objects: dict, extruder: str, toolheads: int):
    """(code, text) while AFC changes, loads or unloads a lane, None otherwise."""
    state = str(afc.get("current_state") or "")
    nxt = str(afc.get("next_lane") or "")
    # AFC lets go of current_lane before a change's load ends (its purge and wipe): the lane is the next one.
    lane = str(afc.get("current_lane") or (nxt if state == "Loading" else ""))
    found = snapshot.find_lane(lane) if snapshot is not None and lane else None
    # The lane's own toolhead: an unload can run on a toolhead that is not the active one.
    extruder = getattr(found, "extruder", "") or extruder
    head = toolhead_name(extruder, toolheads)
    change, changes = _number(afc.get("current_toolchange")), _number(afc.get("number_of_toolchanges"))
    prefix = "Change %d of %d: " % (change, changes) if change and changes else ""
    if state == "Loading":
        heat = _heating(objects, extruder, toolheads)
        if heat is not None and heat[0] == NOZZLE_HEATING:
            return LOADING, prefix + heat[1][0].lower() + heat[1][1:] + " for " + _lane_name(snapshot, lane)
        return LOADING, prefix + "loading %s into %s" % (_lane_name(snapshot, lane) or "filament", head)
    if state == "Unloading":
        return UNLOADING, prefix + "unloading %s from %s" % (_lane_name(snapshot, lane) or "filament", head)
    words = {"ToolSwap": "switching toolheads", "ToolDock": "docking the toolhead",
             "ToolPickup": "picking up the toolhead", "Ejecting": "ejecting " + _lane_name(snapshot, lane),
             "Moving": "moving " + _lane_name(snapshot, lane), "Restoring": "returning to the print"}
    if state in words:
        text = words[state]
        if state in ("ToolSwap", "ToolPickup") and nxt:
            text += " for " + _lane_name(snapshot, nxt)
        return CHANGING_FILAMENT, prefix + text
    return None


def _capitalized(text: str) -> str:
    return text[:1].upper() + text[1:]


def describe(objects: dict, snapshot=None, toolheads: int = 1, probing: bool = False):
    """(stage number, text) of a print in progress or paused, from Klipper's
    objects (agent.status_query()); (PRINTING, "") when nothing more is going
    on than printing, and when no print is."""
    objects = objects if isinstance(objects, dict) else {}

    def obj(name):
        value = objects.get(name)
        return value if isinstance(value, dict) else {}

    state = str(obj("print_stats").get("state") or "").lower()
    if state == "paused":
        # AFC's error says why it paused; a pause with none is the user's (or a macro's) own.
        message = snapshot.message if snapshot is not None and snapshot.message_type == "error" else ""
        if message:
            words = message.lower()
            code = RUNOUT_PAUSE if "runout" in words or "ran out" in words else USER_PAUSE
            return code, "Paused: " + message.splitlines()[0].strip()
        return USER_PAUSE, "Paused"
    if state != "printing":
        return PRINTING, ""
    extruder = str(obj("toolhead").get("extruder") or "extruder")
    found = _afc_stage(obj("AFC"), snapshot, objects, extruder, toolheads)
    if found is not None:
        code, text = found
        return code, _capitalized(text)
    homed = str(obj("toolhead").get("homed_axes") or "").lower()
    if not all(axis in homed for axis in "xyz"):
        return HOMING, "Homing"
    if probing:
        return BED_LEVELING, "Auto bed leveling"
    heat = _heating(objects, extruder, toolheads)
    if heat is not None:
        return heat
    message = obj("display_status").get("message")
    if isinstance(message, str) and message.strip():
        return PRINTING, message.strip()
    return PRINTING, ""
