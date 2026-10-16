"""AFC's changes to a sliced G-code file, on its way to a printer that runs AFC.

The file OrcaSlicer writes changes filament with `T<n>`, where n is the
project's filament number. After a filament sync that is also AFC's T number,
since the sync puts each lane in the slot of its T number. This module
rewrites the file for what AFC does with it:

- **T numbers.** When the print dialog maps filament n to another lane, every
  `T<n>` becomes that lane's T number, and so do the tool numbers AFC reads in
  the start G-code: the `T` of `M104` / `M109` (not one with an `A` parameter,
  a Snapmaker extruder number) and `TOOL=`, `INITIAL_TOOL=` and `T<n>_TEMP=`
  of `PRINT_START` / `START_PRINT`.
- **Lane-change purge.** With "single extruder multi-material" off, the prime
  tower only primes, so a change between two lanes that feed one toolhead gets
  `PURGE_LENGTH=<mm>` on its `T` line for AFC's poop: the flushing volume from
  the filament that toolhead held, out of the file's flush volume table, over
  the filament's cross-section. The first lane a toolhead loads, and a switch
  to a toolhead that already holds its filament, get none, so AFC purges its
  own default length if it loads a lane there.
- **Spool pressure advance.** A lane whose Spoolman spool has a saved flow K
  (`flow_k`, written by AFC autocal) prints with it: a
  `SET_PRESSURE_ADVANCE ADVANCE=<k>` follows its `T` line, and the file's own
  PA lines while it prints take that K. Filaments with adaptive pressure
  advance keep theirs.

Only Klipper files are changed. Nothing here talks to the network or to
OrcaSlicer, so all of it is unit-tested.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

from .protocol import Snapshot

CONFIG_START = "; CONFIG_BLOCK_START"
CONFIG_END = "; CONFIG_BLOCK_END"

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


@dataclass
class FileSettings:
    """What the transform needs from the file's own settings."""
    klipper: bool = False
    single_extruder_mm: bool = False   # "single extruder multi-material": the tower flushes
    diameters: list = field(default_factory=list)  # filament diameter per filament, mm
    flush: list = field(default_factory=list)      # flush_volumes_matrix, mm³, flush multiplier applied
    filaments: int = 0
    nozzles: int = 1
    adaptive_pa: list = field(default_factory=list)  # adaptive pressure advance per filament

    @classmethod
    def from_config(cls, config: dict) -> "FileSettings":
        diameters = _floats(config.get("filament_diameter", ""))
        colors = [c for c in config.get("filament_colour", "").split(";") if c] if config.get("filament_colour") else []
        return cls(
            klipper=config.get("gcode_flavor", "") == "klipper",
            single_extruder_mm=config.get("single_extruder_multi_material", "0") in ("1", "true"),
            diameters=diameters,
            flush=_floats(config.get("flush_volumes_matrix", "")),
            filaments=max(len(diameters), len(colors)),
            nozzles=max(1, len(_floats(config.get("nozzle_diameter", "")))),
            adaptive_pa=_bools(config.get("adaptive_pressure_advance", "")),
        )

    @property
    def purges(self) -> bool:
        """Lane changes are AFC's to purge: Klipper, with the tower not flushing."""
        return self.klipper and not self.single_extruder_mm

    def flush_volume(self, toolhead: int, before: int, after: int) -> float:
        """mm³ to flush going from filament `before` to `after` on a toolhead.
        The table holds one n×n block per nozzle; a toolhead past the last
        block uses the last one."""
        n = self.filaments
        if n <= 0 or before >= n or after >= n or not self.flush:
            return 0.0
        blocks = len(self.flush) // (n * n)
        if blocks <= 0:
            return 0.0
        block = min(max(toolhead, 0), blocks - 1)
        volume = self.flush[block * n * n + before * n + after]
        return volume if math.isfinite(volume) and volume > 0 else 0.0

    def area(self, filament: int) -> float:
        d = self.diameters[filament] if filament < len(self.diameters) else (self.diameters[-1] if self.diameters else 1.75)
        return math.pi / 4 * d * d

    def adaptive(self, filament: int) -> bool:
        return filament < len(self.adaptive_pa) and self.adaptive_pa[filament]


# ------------------------------------------------------------------ what to print


@dataclass
class Plan:
    """Per file filament (its T number in the file): the T number to print,
    the toolhead it feeds and the spool's flow K. A filament missing from
    `tools` keeps its number; one missing from `toolheads` gets no purge."""
    tools: dict = field(default_factory=dict)
    toolheads: dict = field(default_factory=dict)
    flow_k: dict = field(default_factory=dict)


def plan_for(snapshot: Snapshot | None, filaments: int, mapping: dict | None = None,
             settings: FileSettings | None = None) -> Plan:
    """The plan for a file with `filaments` filaments printed on the AFC in
    `snapshot`. `mapping` is the print dialog's filament -> AFC T number;
    without it each filament prints as its own T number. Without AFC data, a
    file sliced for one nozzle still knows every lane feeds toolhead 0."""
    plan = Plan()
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

    def summary(self) -> str:
        return "%d tool changes, %d lane-change purges, %d spool PA settings" % (
            self.tool_changes, self.purges, self.pressure_advance)


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

    lines = text.splitlines(keepends=True)
    loaded = {}           # toolhead -> filament it holds
    active = -1           # filament printing now
    in_config = False
    out = []
    for line in lines:
        first = line.lstrip(" \t")[:1]
        if first == ";":
            if line.startswith(CONFIG_START):
                in_config = True
            elif line.startswith(CONFIG_END):
                in_config = False
            out.append(line)
            continue
        if in_config or not first:
            out.append(line)
            continue

        if first in "Tt":
            match = _T_LINE.match(line)
            if match:
                indent, number, rest = match.group(1), int(match.group(2)), match.group(3)
                result.tool_changes += 1
                active = number
                purge = 0.0
                if settings.purges and number in plan.toolheads:
                    head = plan.toolheads[number]
                    before = loaded.get(head, -1)
                    loaded[head] = number
                    if before >= 0 and before != number:
                        area = settings.area(number)
                        volume = settings.flush_volume(head, before, number)
                        purge = volume / area if area > 0 else 0.0
                    # What the file had there is replaced, so the line carries only this purge.
                    rest = _PURGE.sub("", rest)
                newline = "\n" if line.endswith("\n") else ""
                rest = rest[:-1] if newline and rest.endswith("\n") else rest
                purge_text = ""
                if purge >= 0.05:
                    purge_text = " PURGE_LENGTH=" + _format(purge, 1)
                    result.purges += 1
                line = "%sT%d%s%s%s" % (indent, plan.tools.get(number, number), purge_text, rest, newline)
                out.append(line)
                if number in plan.flow_k and not settings.adaptive(number):
                    out.append("SET_PRESSURE_ADVANCE ADVANCE=%s %s\n" % (_format(plan.flow_k[number], 4), PA_NOTE))
                    result.pressure_advance += 1
                continue

        if plan.tools and (first in "Mm") and _TEMP_LINE.match(line):
            out.append(_renumber_temp_line(line, plan.tools))
            continue
        if plan.tools and first in "PpSs" and _START_LINE.match(line):
            out.append(_renumber_start_line(line, plan.tools))
            continue
        if first in "Ss" and PA_NOTE in line and _PA_LINE.match(line):
            continue  # one this step wrote on an earlier pass; the T line above gets a new one
        if first in "Ss" and active in plan.flow_k and not settings.adaptive(active) and _PA_LINE.match(line):
            body = line.split(";", 1)[0]
            if "EXTRUDER=" not in body.upper():
                line = _PA_VALUE.sub("ADVANCE=" + _format(plan.flow_k[active], 4), line, count=1)
        out.append(line)

    result.text = "".join(out)
    return result


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
