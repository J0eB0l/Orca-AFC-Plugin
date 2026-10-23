"""AFC's changes to a sliced G-code file, on its way to a printer that runs AFC.

The file OrcaSlicer writes changes filament with `T<n>`, where n is the
project's filament number. After a filament sync that is also AFC's T number,
since the sync puts each lane in the slot of its T number. This module
rewrites the file for what AFC does with it:

- **T numbers.** When the print dialog maps filament n to another lane, every
  `T<n>` becomes that lane's T number, and so do the tool numbers AFC reads in
  the start G-code: the `T` of `M104` / `M109` (not one with an `A` parameter,
  a Snapmaker extruder number) and `TOOL=`, `INITIAL_TOOL=` and `T<n>_TEMP=`
  of `PRINT_START` / `START_PRINT`. A renumbered file says which T number
  prints each filament in a `; AFC filament tools = 4,0` line before its
  settings block, so a file already on the printer can be renumbered again
  for other lanes. A T number two filaments shared stays the first one's.
- **Lane-change purge.** With "single extruder multi-material" off, a change
  between two lanes that feed one toolhead gets `PURGE_LENGTH=<mm>` on its `T`
  line for AFC's poop: the flushing volume from the filament that toolhead
  held, out of the file's flush volume table times its flush multiplier, over
  the filament's cross-section. The first lane a toolhead loads, and a switch
  to a toolhead that already holds its filament, get none, so AFC purges its
  own default length if it loads a lane there. With "Purge in prime tower" and
  "Multi-material per toolhead" on, the AFC build of OrcaSlicer flushes the
  lane changes of the prime tower's tool changes on the tower, from the
  toolheads the trays reported at filament sync (`filament_toolhead`); such a
  change gets no PURGE_LENGTH when it is still the same lane change after the
  print dialog's mapping. Every other lane change still goes to the poop.
- **Spool pressure advance.** A lane whose Spoolman spool has a saved flow K
  (`flow_k`, written by AFC autocal) prints with it: a
  `SET_PRESSURE_ADVANCE ADVANCE=<k>` follows its `T` line, and the file's own
  PA lines while it prints take that K. Filaments with adaptive pressure
  advance keep theirs.
- **Snapmaker U1 top cover.** With "Support air filtration" on in the printer
  settings and the U1 reporting its top cover on (`purifier.power_detected`),
  a U1 file sets the top cover's filter and exhaust fan right after
  `PRINT_START`, the way Snapmaker's OrcaSlicer does from its U1 start G-code:
  strong cooling when a filament the plate prints softens at 50 °C or below,
  light cooling at 70 °C or below, else keep warm. Keep warm aims for 45 °C, or
  with "Support controlling chamber temperature" on, for the highest chamber
  temperature those filaments ask for. A plate with ABS, ASA or a nylon (PA,
  PPA) always runs the filter: keep warm runs it anyway, and with a cooling
  mode (PLA or PETG on the same plate) its fan (`circulation_fan`) is turned on
  beside it, while the purifier keeps running the exhaust. A plate of only PLA
  and PETG runs no filter. A file that already sets the mode is left as it is.
- **No M141 or M191 on a U1.** The U1 answers both with "Unknown command", so a
  U1 file loses the chamber lines OrcaSlicer writes from the filaments'
  chamber temperature, with the cavity fan blip around M191. The U1 holds its
  chamber with the top cover's mode above and waits for it with its own
  `WAIT_CHAMBER_TEMP`, which Snapmaker's U1 start G-code runs once the bed is hot.

Only Klipper files are changed. Apart from that rewrite, the agent adds
moonraker-timelapse's frames to a file when the print dialog's Timelapse switch
is on (add_timelapse_frames()). Nothing here talks to the network or to
OrcaSlicer, so all of it is unit-tested.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .protocol import Snapshot

CONFIG_START = "; CONFIG_BLOCK_START"
CONFIG_END = "; CONFIG_BLOCK_END"
TOWER_CHANGE_START = "; CP TOOLCHANGE START"
TOWER_CHANGE_END = "; CP TOOLCHANGE END"

_T_LINE = re.compile(r"^([ \t]*)T(\d{1,3})(?=[ \t;\r\n]|$)(.*)$", re.S)
_PURGE = re.compile(r"[ \t]*PURGE_LENGTH=[^ \t;\r\n]*", re.I)
_TEMP_LINE = re.compile(r"^[ \t]*M10[49](?=[ \t;\r\n]|$)", re.I)
_TEMP_TOOL = re.compile(r"(?<=[ \t])T(\d+)", re.I)
_A_PARAM = re.compile(r"[ \t]A\d", re.I)
_START_LINE = re.compile(r"^[ \t]*(PRINT_START|START_PRINT)(?=[ \t;\r\n]|$)", re.I)
_START_TOOL = re.compile(r"(?<=[ \t])(TOOL|INITIAL_TOOL)=(\d+)", re.I)
_START_TEMP = re.compile(r"(?<=[ \t])T(\d+)_TEMP=", re.I)
_PA_LINE = re.compile(r"^[ \t]*SET_PRESSURE_ADVANCE(?=[ \t;\r\n]|$)", re.I)
_PA_VALUE = re.compile(r"(?<=[ \t])ADVANCE=[^ \t;\r\n]*", re.I)
PA_NOTE = "; AFC spool flow K"
TOOLS_NOTE = "; AFC filament tools = "
_TOOLS_LINE = re.compile(r"^; AFC filament tools = ([0-9, ]*)\r?$", re.M)
TIMELAPSE_FRAME = "TIMELAPSE_TAKE_FRAME"
_TAKES_FRAMES = re.compile(r"^[ \t]*TIMELAPSE_TAKE_FRAME(?=[ \t;\r\n]|$)", re.I | re.M)
_LAYER_CHANGE = re.compile(r"^;LAYER_CHANGE(\r?\n)", re.M)
_PURIFIER_LINE = re.compile(r"^[ \t]*SET_PURIFIER_MODE(?=[ \t;\r\n]|$)", re.I | re.M)
_FILAMENT_USED = re.compile(r"^; filament used \[mm\] = (.*)$", re.M)
_ANY_T_LINE = re.compile(r"^[ \t]*T(\d{1,3})(?=[ \t;\r\n]|$)", re.M)
_CHAMBER_LINE = re.compile(r"^[ \t]*M1([49])1(?=[ \t;\r\n]|$)", re.I)
# The cavity fan OrcaSlicer runs around M191 when the printer has an auxiliary fan.
_AUX_FAN_ON = re.compile(r"^[ \t]*M106 P2 S255[ \t]*\r?\n$")
_AUX_FAN_OFF = re.compile(r"^[ \t]*M106 P2 S0[ \t]*\r?\n?$")
TOP_COVER_NOTE = "; U1 top cover"
KEEP_WARM_TEMP = 45
# Fumes the top cover's filter should take: ABS, ASA and nylons, their blends and fills.
_FILTER_TYPES = re.compile(r"^(ABS|ASA|PC-ABS|PA|PPA)", re.I)
# The purifier's inner fan in the U1's config; cooling modes leave it off.
FILTER_FAN_LINE = "SET_FAN_SPEED FAN=circulation_fan SPEED=0.6"
# The modes of Snapmaker's U1 start G-code, by the name its comments give them.
TOP_COVER_MODES = {
    "keep warm": "SET_PURIFIER_MODE MODE=3 DESIRE_TEMP={temp} FAN_SPEED=0.6 DELAY_OFF=600",
    "light cooling": "SET_PURIFIER_MODE MODE=1 DESIRE_TEMP=45 ALARM_TEMP=51 FAN_SPEED=0.6 DELAY_OFF=0",
    "strong cooling": "SET_PURIFIER_MODE MODE=1 DESIRE_TEMP=42 ALARM_TEMP=48 FAN_SPEED=0.6 DELAY_OFF=0",
}


# ------------------------------------------------------------------ the file's settings


def read_config_block(text: str) -> dict:
    """The `; key = value` lines of the file's CONFIG_BLOCK, as strings."""
    end = text.rfind(CONFIG_END)
    start = text.rfind(CONFIG_START, 0, end if end >= 0 else len(text))
    if start < 0:
        return {}
    block = text[start + len(CONFIG_START):end if end > start else len(text)]
    out = {}
    for line in block.splitlines():
        if not line.startswith("; ") or " = " not in line:
            continue
        key, _, value = line[2:].partition(" = ")
        out[key.strip()] = value.strip()
    return out


def _floats(value: str) -> list:
    out = []
    for item in (value or "").split(","):
        try:
            out.append(float(item))
        except ValueError:
            out.append(0.0)
    return out if value else []


def _bools(value: str) -> list:
    return [item.strip() in ("1", "true") for item in value.split(",")] if value else []


def _ints(value: str) -> list:
    out = []
    for item in (value or "").split(","):
        try:
            out.append(int(item))
        except ValueError:
            out.append(-1)
    return out if value else []


def _on(config: dict, key: str) -> bool:
    return config.get(key, "0") in ("1", "true")


@dataclass
class FileSettings:
    """What the transform needs from the file's own settings."""
    klipper: bool = False
    single_extruder_mm: bool = False   # "single extruder multi-material": the tower flushes
    diameters: list = field(default_factory=list)  # filament diameter per filament, mm
    flush: list = field(default_factory=list)      # flush_volumes_matrix, mm³
    multipliers: list = field(default_factory=list)  # flush_multiplier per nozzle
    filaments: int = 0
    nozzles: int = 1
    adaptive_pa: list = field(default_factory=list)  # adaptive pressure advance per filament
    tower_flushes: bool = False  # the prime tower flushes the lane changes it plans ("Purge in prime tower")
    sliced_toolheads: list = field(default_factory=list)  # filament_toolhead as sliced, -1 = not known
    u1: bool = False  # a Snapmaker U1
    top_cover: bool = False  # a Snapmaker U1 with "Support air filtration" on
    softening: list = field(default_factory=list)  # temperature_vitrification per filament, °C
    types: list = field(default_factory=list)  # filament_type per filament
    chamber_control: bool = False  # "Support controlling chamber temperature"
    chamber_on: list = field(default_factory=list)  # activate_chamber_temp_control per filament
    chamber_temps: list = field(default_factory=list)  # chamber_temperature per filament, °C
    afc: bool | None = None  # the printer's AFC box (afc_enabled); None when the file does not say

    @classmethod
    def from_config(cls, config: dict) -> "FileSettings":
        diameters = _floats(config.get("filament_diameter", ""))
        colors = [c for c in config.get("filament_colour", "").split(";") if c] if config.get("filament_colour") else []
        single_extruder_mm = _on(config, "single_extruder_multi_material")
        u1 = config.get("printer_model", "").startswith("Snapmaker U1")
        return cls(
            klipper=config.get("gcode_flavor", "") == "klipper",
            single_extruder_mm=single_extruder_mm,
            diameters=diameters,
            flush=_floats(config.get("flush_volumes_matrix", "")),
            multipliers=_floats(config.get("flush_multiplier", "")),
            filaments=max(len(diameters), len(colors)),
            nozzles=max(1, len(_floats(config.get("nozzle_diameter", "")))),
            adaptive_pa=_bools(config.get("adaptive_pressure_advance", "")),
            # As Print::_make_wipe_tower plans it: a Type 2 tower, SEMM off, Multi-material per toolhead.
            tower_flushes=(_on(config, "enable_prime_tower") and _on(config, "purge_in_prime_tower")
                           and _on(config, "multi_extruder_multi_material") and not single_extruder_mm
                           and config.get("wipe_tower_type", "type2") != "type1"),
            sliced_toolheads=_ints(config.get("filament_toolhead", "")),
            u1=u1,
            top_cover=u1 and _on(config, "support_air_filtration"),
            softening=_floats(config.get("temperature_vitrification", "")),
            types=[t.strip().strip('"') for t in config.get("filament_type", "").split(";")],
            chamber_control=_on(config, "support_chamber_temp_control"),
            chamber_on=_bools(config.get("activate_chamber_temp_control", "")),
            chamber_temps=_ints(config.get("chamber_temperature", "")),
            afc=_on(config, "afc_enabled") if "afc_enabled" in config else None,
        )

    @property
    def purges(self) -> bool:
        """Lane changes are AFC's to purge: Klipper, with the tower not flushing."""
        return self.klipper and not self.single_extruder_mm

    def flush_volume(self, toolhead: int, before: int, after: int) -> float:
        """mm³ to flush going from filament `before` to `after` on a toolhead,
        times that toolhead's flush multiplier. The table holds one n×n block
        per nozzle; a toolhead past the last block uses the last one."""
        n = self.filaments
        if n <= 0 or before >= n or after >= n or not self.flush:
            return 0.0
        blocks = len(self.flush) // (n * n)
        if blocks <= 0:
            return 0.0
        block = min(max(toolhead, 0), blocks - 1)
        head = min(max(toolhead, 0), self.nozzles - 1)
        multiplier = self.multipliers[head] if head < len(self.multipliers) else (
            self.multipliers[0] if self.multipliers else 1.0)
        volume = self.flush[block * n * n + before * n + after] * multiplier
        return volume if math.isfinite(volume) and volume > 0 else 0.0

    def sliced_toolhead(self, filament: int) -> int:
        return self.sliced_toolheads[filament] if 0 <= filament < len(self.sliced_toolheads) else -1

    def area(self, filament: int) -> float:
        d = self.diameters[filament] if filament < len(self.diameters) else (self.diameters[-1] if self.diameters else 1.75)
        return math.pi / 4 * d * d

    def adaptive(self, filament: int) -> bool:
        return filament < len(self.adaptive_pa) and self.adaptive_pa[filament]


def filaments_used(text: str) -> list:
    """The filaments a file prints: those with a length in its `filament used
    [mm]` line, else those it changes to."""
    match = _FILAMENT_USED.search(text)
    if match:
        return [i for i, mm in enumerate(_floats(match.group(1))) if mm > 0]
    return sorted({int(m.group(1)) for m in _ANY_T_LINE.finditer(text)})


def printed_tools(text: str) -> dict:
    """Filament -> the T number a file prints it as, from the line a
    renumbering left in it. Empty for a file that prints each filament as its
    own number."""
    match = _TOOLS_LINE.search(text)
    tools = [item.strip() for item in match.group(1).split(",")] if match else []
    return {filament: int(tool) for filament, tool in enumerate(tools) if tool.isdigit()}


def top_cover_mode(settings: FileSettings, used: list) -> str:
    """The U1 top cover mode for a plate printing the filaments `used`, as
    Snapmaker's OrcaSlicer picks it from their softening temperature."""
    softest = min((settings.softening[f] for f in used
                   if f < len(settings.softening) and settings.softening[f] > 0), default=None)
    if softest is not None and softest <= 50:
        return "strong cooling"
    if softest is not None and softest <= 70:
        return "light cooling"
    return "keep warm"


def filter_filament(settings: FileSettings, used: list) -> str:
    """The type of the first filament `used` whose fumes want the filter, or ""."""
    return next((settings.types[f] for f in used
                 if f < len(settings.types) and _FILTER_TYPES.match(settings.types[f])), "")


def top_cover_gcode(settings: FileSettings, used: list) -> list:
    """The U1 top cover lines for a plate printing the filaments `used`."""
    mode = top_cover_mode(settings, used)
    target = 0
    if settings.chamber_control:
        target = max((settings.chamber_temps[f] for f in used
                      if f < len(settings.chamber_on) and settings.chamber_on[f]
                      and f < len(settings.chamber_temps)), default=0)
    line = TOP_COVER_MODES[mode].format(temp=target if target > 0 else KEEP_WARM_TEMP)
    lines = ["%s %s: %s" % (line, TOP_COVER_NOTE, mode)]
    fumes = filter_filament(settings, used)
    if fumes and mode != "keep warm":
        lines.append("%s %s: filter for %s" % (FILTER_FAN_LINE, TOP_COVER_NOTE, fumes))
    return lines


# ------------------------------------------------------------------ what to print


@dataclass
class Plan:
    """Per file filament (its T number in the file): the T number to print,
    the toolhead it feeds and the spool's flow K. A filament missing from
    `tools` keeps its number; one missing from `toolheads` gets no purge."""
    tools: dict = field(default_factory=dict)
    toolheads: dict = field(default_factory=dict)
    flow_k: dict = field(default_factory=dict)
    top_cover: bool = False  # the printer reports a U1 top cover on


def plan_for(snapshot: Snapshot | None, filaments: int, mapping: dict | None = None,
             settings: FileSettings | None = None) -> Plan:
    """The plan for a file with `filaments` filaments printed on the AFC in
    `snapshot`. `mapping` is the print dialog's filament -> AFC T number;
    without it each filament prints as its own T number. Without AFC data, a
    file sliced for one nozzle still knows every lane feeds toolhead 0."""
    plan = Plan(top_cover=snapshot is not None and snapshot.top_cover)
    for filament in range(filaments):
        tool = mapping.get(filament, filament) if mapping else filament
        if tool != filament:
            plan.tools[filament] = tool
        lane = snapshot.lane_for_tool(tool) if snapshot is not None and snapshot.present else None
        if lane is not None and lane.extruder_index >= 0:
            plan.toolheads[filament] = lane.extruder_index
        elif (snapshot is None or not snapshot.present) and settings is not None and settings.nozzles == 1:
            plan.toolheads[filament] = 0
        if lane is not None and lane.flow_k >= 0:
            plan.flow_k[filament] = lane.flow_k
    return plan


# ------------------------------------------------------------------ the rewrite


@dataclass
class Result:
    text: str = ""
    tool_changes: int = 0
    purges: int = 0
    pressure_advance: int = 0
    top_cover: str = ""  # the U1 top cover mode this rewrite set
    chamber_lines: int = 0  # M141 and M191 lines dropped from a U1 file

    def summary(self) -> str:
        text = "%d tool changes, %d lane-change purges, %d spool PA settings" % (
            self.tool_changes, self.purges, self.pressure_advance)
        if self.top_cover:
            text += ", U1 top cover: " + self.top_cover
        if self.chamber_lines:
            text += ", %d M141/M191 dropped (the U1 has neither)" % self.chamber_lines
        return text


def _format(value: float, digits: int) -> str:
    text = ("%." + str(digits) + "f") % value
    return text.rstrip("0").rstrip(".") if "." in text else text


def _renumber_start_line(line: str, tools: dict) -> str:
    line = _START_TOOL.sub(lambda m: "%s=%d" % (m.group(1), tools.get(int(m.group(2)), int(m.group(2)))), line)
    return _START_TEMP.sub(lambda m: "T%d_TEMP=" % tools.get(int(m.group(1)), int(m.group(1))), line)


def _renumber_temp_line(line: str, tools: dict) -> str:
    body = line.split(";", 1)[0]
    if _A_PARAM.search(body):
        return line  # a Snapmaker extruder number, not an AFC tool
    head, sep, comment = line.partition(";")
    return _TEMP_TOOL.sub(lambda m: "T%d" % tools.get(int(m.group(1)), int(m.group(1))), head) + sep + comment


def transform(text: str, plan: Plan, settings: FileSettings | None = None) -> Result:
    """The file with the plan applied. Files that are not Klipper's come back
    unchanged."""
    settings = settings or FileSettings.from_config(read_config_block(text))
    result = Result(text=text)
    if not settings.klipper:
        return result

    cover = []
    if settings.top_cover and not plan.top_cover:
        result.top_cover = "none, the printer does not report it on"
    elif settings.top_cover and not _PURIFIER_LINE.search(text):
        used = filaments_used(text)
        cover = top_cover_gcode(settings, used)
        result.top_cover = top_cover_mode(settings, used) + (" with the filter on" if len(cover) > 1 else "")

    # The file's T numbers may be an earlier renumbering's: each names a filament.
    filament_of = {}
    for filament, tool in printed_tools(text).items():
        filament_of.setdefault(tool, filament)
    numbers = set(filament_of) | set(range(settings.filaments)) | set(plan.tools)
    renumber = {}         # the file's T number -> the one to print
    for number in numbers:
        filament = filament_of.get(number, number)
        if plan.tools.get(filament, filament) != number:
            renumber[number] = plan.tools.get(filament, filament)
    tools = [plan.tools.get(f, f) for f in range(settings.filaments)]
    record = TOOLS_NOTE + ",".join(str(t) for t in tools) if any(t != f for f, t in enumerate(tools)) else ""

    lines = text.splitlines(keepends=True)
    loaded = {}           # toolhead -> filament it holds
    sliced = {}           # the same, by the toolheads the file was sliced with
    active = -1           # filament printing now
    in_config = False
    in_tower_change = False
    fan_off_next = False  # the cavity fan line after a dropped M191 goes too
    out = []
    for line in lines:
        first = line.lstrip(" \t")[:1]
        if fan_off_next:
            fan_off_next = False
            if _AUX_FAN_OFF.match(line):
                continue
        if first == ";":
            if line.startswith(TOOLS_NOTE):
                continue  # an earlier renumbering's; this one writes its own
            if line.startswith(CONFIG_START):
                in_config = True
                if record:
                    out.append(record + ("\r\n" if line.endswith("\r\n") else "\n"))
                    record = ""
            elif line.startswith(CONFIG_END):
                in_config = False
            elif line.startswith(TOWER_CHANGE_START):
                in_tower_change = True
            elif line.startswith(TOWER_CHANGE_END):
                in_tower_change = False
            out.append(line)
            continue
        if in_config or not first:
            out.append(line)
            continue

        if first in "Tt":
            match = _T_LINE.match(line)
            if match:
                indent, number, rest = match.group(1), int(match.group(2)), match.group(3)
                filament = filament_of.get(number, number)
                result.tool_changes += 1
                active = filament
                purge = 0.0
                sliced_before = -1
                if settings.tower_flushes and settings.sliced_toolhead(filament) >= 0:
                    sliced_before = sliced.get(settings.sliced_toolhead(filament), -1)
                    sliced[settings.sliced_toolhead(filament)] = filament
                if settings.purges and filament in plan.toolheads:
                    head = plan.toolheads[filament]
                    before = loaded.get(head, -1)
                    loaded[head] = filament
                    # The tower flushed this lane change as sliced, and the mapping kept it.
                    flushed = in_tower_change and before == sliced_before
                    if before >= 0 and before != filament and not flushed:
                        area = settings.area(filament)
                        volume = settings.flush_volume(head, before, filament)
                        purge = volume / area if area > 0 else 0.0
                    # What the file had there is replaced, so the line carries only this purge.
                    rest = _PURGE.sub("", rest)
                newline = "\n" if line.endswith("\n") else ""
                rest = rest[:-1] if newline and rest.endswith("\n") else rest
                purge_text = ""
                if purge >= 0.05:
                    purge_text = " PURGE_LENGTH=" + _format(purge, 1)
                    result.purges += 1
                line = "%sT%d%s%s%s" % (indent, renumber.get(number, number), purge_text, rest, newline)
                out.append(line)
                if filament in plan.flow_k and not settings.adaptive(filament):
                    out.append("SET_PRESSURE_ADVANCE ADVANCE=%s %s\n" % (_format(plan.flow_k[filament], 4), PA_NOTE))
                    result.pressure_advance += 1
                continue

        if settings.u1 and first in "Mm":
            match = _CHAMBER_LINE.match(line)
            if match:
                result.chamber_lines += 1
                if match.group(1) == "9":
                    if out and _AUX_FAN_ON.match(out[-1]):
                        out.pop()
                    fan_off_next = True
                continue
        if renumber and (first in "Mm") and _TEMP_LINE.match(line):
            out.append(_renumber_temp_line(line, renumber))
            continue
        if first in "PpSs" and _START_LINE.match(line):
            out.append(_renumber_start_line(line, renumber) if renumber else line)
            if cover:
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                out.append(("" if line.endswith("\n") else newline) + newline.join(cover) + newline)
                cover = []
            continue
        if first in "Ss" and PA_NOTE in line and _PA_LINE.match(line):
            continue  # one this step wrote on an earlier pass; the T line above gets a new one
        if first in "Ss" and active in plan.flow_k and not settings.adaptive(active) and _PA_LINE.match(line):
            body = line.split(";", 1)[0]
            if "EXTRUDER=" not in body.upper():
                line = _PA_VALUE.sub("ADVANCE=" + _format(plan.flow_k[active], 4), line, count=1)
        out.append(line)

    result.text = "".join(out)
    if cover:  # no PRINT_START: before the file's first command
        result.text = insert_after_header(result.text, ("\r\n" if "\r\n" in text else "\n").join(cover))
    return result


def add_timelapse_frames(text: str) -> str:
    """moonraker-timelapse's TIMELAPSE_TAKE_FRAME before each `;LAYER_CHANGE`,
    for a file that takes no frames itself (the printer preset's Time lapse
    G-code). A file that does is left as it is."""
    if _TAKES_FRAMES.search(text):
        return text
    return _LAYER_CHANGE.sub(lambda m: TIMELAPSE_FRAME + m.group(1) + m.group(0), text)


def insert_after_header(text: str, line: str) -> str:
    """`line` after the comments and blank lines a file opens with (its header
    block and thumbnails), before its first command."""
    pos = 0
    while pos < len(text):
        end = text.find("\n", pos)
        end = len(text) if end < 0 else end + 1
        stripped = text[pos:end].strip()
        if stripped and not stripped.startswith(";"):
            break
        pos = end
    newline = "\r\n" if "\r\n" in text[:pos or len(text)] else "\n"
    head = text[:pos]
    if head and not head.endswith("\n"):
        head += newline
    return head + line + newline + text[pos:]
