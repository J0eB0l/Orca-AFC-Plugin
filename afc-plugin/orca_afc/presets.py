"""Which filament preset OrcaSlicer's filament sync picks for each AFC lane.

The sync matches a tray's filament id against the filament presets that fit
the selected printer, so the printer agent hands each lane the id of the
preset that best fits its spool. The scoring follows OrcaSlicer's own
Creality agent: a preset of the spool's material scores 20 when its name
holds the spool's product name, 10 when its vendor (or name) is the spool's
vendor, and loses 5 for each word of its name the spool never mentioned. A
spool that matches no preset by name or vendor gets the generic preset of its
material, as OrcaSlicer's Moonraker agent does.

OrcaSlicer's presets are read on its UI thread (collect()); a preset's
filament id is not one of its settings, so FilamentIds reads it from the
preset's file on the plugin's thread, following "inherits" to the preset that
sets it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from .protocol import Lane, material_key

# What OrcaSlicer's sync reads as "a filament it does not know": it then
# falls back to the generic preset of the tray's material.
UNKNOWN_FILAMENT_ID = "__unknown__"


@dataclass
class Candidate:
    """A filament preset the sync can pick on this printer."""
    name: str
    type: str          # filament_type, upper case
    vendor: str        # filament_vendor
    system: bool
    file: str          # the preset's JSON file
    inherits: str = ""


def _first(value) -> str:
    """A setting OrcaSlicer hands over as one value or a per-extruder list."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return "" if value is None else str(value)


def collect(bundle) -> list:
    """The visible filament presets compatible with the selected printer.
    Runs on OrcaSlicer's UI thread."""
    out = []
    filaments = bundle.filaments
    for index in range(filaments.size()):
        preset = filaments.preset(index)
        if preset is None or not preset.is_visible or not preset.is_compatible or preset.is_default:
            continue
        out.append(Candidate(
            name=str(preset.name),
            type=_first(preset.config_value("filament_type")).upper(),
            vendor=_first(preset.config_value("filament_vendor")),
            system=bool(preset.is_system),
            file=str(preset.file or ""),
            inherits=_first(preset.config_value("inherits")),
        ))
    return out


def name_words(name: str) -> list:
    """Lower-case words of a preset name without its "@..." suffix:
    "Generic PLA Matte @U1 0.4 nozzle" -> ["generic", "pla", "matte"]."""
    return re.findall(r"[a-z0-9]+", name.split("@", 1)[0].lower())


def lane_types(lane: Lane) -> list:
    """Material names to look for, most specific first: "PA" with sub type
    "CF" tries "PA-CF" before "PA"; "PLA+" falls back to "PLA"."""
    material = (lane.material or "").strip().upper()
    if not material:
        return []
    types = []
    if lane.sub_type:
        types.append("%s-%s" % (material, lane.sub_type.strip().upper()))
    types.append(material)
    key = material_key(material)
    if key and key not in types:
        types.append(key)
    return types


def rank(lane: Lane, candidates: list) -> list:
    """Presets of the lane's material its spool names, best first. Empty
    when the spool's vendor and product match no preset."""
    types = lane_types(lane)
    of_type = []
    for kind in types:
        of_type = [c for c in candidates if c.type == kind]
        if of_type:
            break
    vendor = (lane.vendor or "").strip().lower()
    product = (lane.filament_name or "").strip().lower()
    spool_words = {"generic"}
    for text in (lane.vendor, lane.filament_name, lane.material, lane.sub_type):
        spool_words.update(name_words(text or ""))

    scored = []
    for c in of_type:
        name = c.name.lower()
        score = 0
        if product and product in name:
            score += 20
        if vendor and (c.vendor.lower() == vendor or vendor in name):
            score += 10
        if score == 0:
            continue
        score -= 5 * sum(1 for word in name_words(c.name) if word not in spool_words)
        scored.append((-score, not c.system, c.name, c))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored]


def generic(lane: Lane, candidates: list) -> list:
    """The presets to fall back on for the lane's material: OrcaSlicer's
    "Generic <material>" first, then other system presets, then the user's."""
    for kind in lane_types(lane):
        of_type = [c for c in candidates if c.type == kind]
        if of_type:
            return sorted(of_type, key=lambda c: (not c.name.lower().startswith("generic "), not c.system, c.name))
    return []


class FilamentIds:
    """Reads presets' filament ids from their files and keeps them. Runs on
    the plugin's thread."""

    def __init__(self):
        self._ids = {}       # preset file -> filament id ("" when it has none)
        self._names = {}     # file name -> path, under the profile folders indexed so far
        self._roots = set()  # profile folders indexed
        self._system = ""    # OrcaSlicer's system profile folder, once a system preset showed it

    def learn(self, candidates: list) -> None:
        """Note where the system presets live: a user preset's parent is one."""
        if not self._system:
            self._system = next((r for r in (_profiles_root(c.file) for c in candidates if c.system) if r), "")

    def of(self, candidate: Candidate) -> str:
        if candidate.file not in self._ids:
            self._ids[candidate.file] = self._read(candidate.file)
        return self._ids[candidate.file]

    def _read(self, path: str) -> str:
        seen = set()
        while path and path not in seen:
            seen.add(path)
            data = _load(path)
            if not isinstance(data, dict):
                return ""
            if data.get("filament_id"):
                return str(data["filament_id"])
            parent = _first(data.get("inherits"))
            if not parent:
                return ""
            path = self._find(parent + ".json", path)
        return ""

    def _find(self, file_name: str, near: str) -> str:
        """A preset file by name: next to `near`, else anywhere under the
        profile folder `near` sits in (system presets inherit across vendors,
        e.g. Snapmaker's from OrcaFilamentLibrary)."""
        here = os.path.join(os.path.dirname(near), file_name)
        if os.path.isfile(here):
            return here
        root = _profiles_root(near) or self._system
        if root and root not in self._roots:
            self._roots.add(root)
            for folder, _, files in os.walk(root):
                for name in files:
                    if name.endswith(".json"):
                        self._names.setdefault(name, os.path.join(folder, name))
        return self._names.get(file_name, "")


def _profiles_root(path: str) -> str:
    """The "system" folder that holds every vendor's profiles, for a preset
    file under it; a user preset's parent is found through its own file."""
    parts = os.path.normpath(path).split(os.sep)
    for i in range(len(parts) - 1, 0, -1):
        if parts[i].lower() == "system":
            return os.sep.join(parts[:i + 1]) or os.sep
    return ""


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def match(lane: Lane, candidates: list, ids: FilamentIds) -> str:
    """The filament id the sync should pick for a loaded lane: the best
    scored preset's, else the generic one's, else UNKNOWN_FILAMENT_ID so the
    sync still falls back on the material."""
    ids.learn(candidates)
    for candidate in rank(lane, candidates) + generic(lane, candidates):
        filament_id = ids.of(candidate)
        if filament_id:
            return filament_id
    return UNKNOWN_FILAMENT_ID

