"""Which filament preset OrcaSlicer's filament sync picks for each AFC lane.

The sync matches a tray's filament id against the filament presets that fit
the selected printer, so the printer agent hands each lane the id of the
preset that best fits its spool. The scoring follows OrcaSlicer's own
Creality agent: a preset of the spool's material scores 20 when its name
holds the spool's product name, 10 when its vendor (or name) is the spool's
vendor, and loses 5 for each word of its name the spool never mentioned. A
spool that matches no preset by name or vendor gets the generic preset of its
material, as OrcaSlicer's Moonraker agent does. Support presets (breakaway,
PVA) count only for a spool that names support or when nothing else is of its
material.

The sync only picks a preset that is its own base: a system preset, or a
user preset that inherits nothing. A user preset that inherits stands for the
system preset it comes from and has that preset's filament id (an id still in
its own file is stale), so it only counts when that preset fits the printer.

OrcaSlicer's presets are read on its UI thread (collect()); a preset's
filament id is not one of its settings, so FilamentIds reads it on the
plugin's thread: from the preset's file, following "inherits" to the preset
that sets it, or, for a system preset OrcaSlicer 2.5 keeps packed
(system/<vendor>.opc), from that file.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from . import opc
from .protocol import Lane, material_key

# What OrcaSlicer's sync reads as "a filament it does not know": it then
# falls back to the generic preset of the tray's material.
UNKNOWN_FILAMENT_ID = "__unknown__"

# The vendor every other vendor's filament presets may inherit from.
LIBRARY = "OrcaFilamentLibrary"

# Words that make a spool support material, so support presets may fit it.
SUPPORT_WORDS = {"support", "breakaway"}


@dataclass
class Candidate:
    """A filament preset the sync can pick on this printer."""
    name: str
    type: str          # filament_type, upper case
    vendor: str        # filament_vendor
    system: bool
    file: str          # the file OrcaSlicer names for the preset the sync matches for it
    inherits: str = ""
    support: bool = False  # filament_is_support
    base: str = ""     # that preset's name: this one's, or the system preset it comes from


def _first(value) -> str:
    """A setting OrcaSlicer hands over as one value or a per-extruder list."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    return "" if value is None else str(value)


def _base(filaments, preset):
    """The preset the sync matches for `preset`, as PresetCollection::get_preset_base
    finds it: itself when it is a system preset or inherits nothing, else the
    one it inherits from, all the way up. None when a parent is missing."""
    seen = set()
    while preset is not None and not preset.is_system:
        parent = _first(preset.config_value("inherits"))
        if not parent:
            return preset
        if preset.name in seen:
            return None
        seen.add(preset.name)
        preset = filaments.find_preset(parent)
    return preset


def collect(bundle) -> list:
    """The visible filament presets compatible with the selected printer that
    the sync can pick. Runs on OrcaSlicer's UI thread."""
    out = []
    filaments = bundle.filaments
    for index in range(filaments.size()):
        preset = filaments.preset(index)
        if preset is None or not preset.is_visible or not preset.is_compatible or preset.is_default:
            continue
        base = _base(filaments, preset)
        if base is None or not base.is_compatible:
            continue
        out.append(Candidate(
            name=str(preset.name),
            type=_first(preset.config_value("filament_type")).upper(),
            vendor=_first(preset.config_value("filament_vendor")),
            system=bool(preset.is_system),
            file=str(base.file or ""),
            inherits=_first(preset.config_value("inherits")),
            support=_first(preset.config_value("filament_is_support")).split(",")[0].strip().lower() in ("1", "true"),
            base=str(base.name),
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


def spool_words(lane: Lane) -> set:
    """The words a preset's name may hold without losing score for them."""
    words = {"generic"}
    for text in (lane.vendor, lane.filament_name, lane.material, lane.sub_type):
        words.update(name_words(text or ""))
    return words


def of_material(lane: Lane, candidates: list) -> list:
    """The presets of the lane's most specific material that has any,
    support presets left out unless the spool names support or nothing else
    is of that material."""
    for kind in lane_types(lane):
        of_type = [c for c in candidates if c.type == kind]
        if of_type:
            plain = [c for c in of_type if not c.support]
            return of_type if not plain or spool_words(lane) & SUPPORT_WORDS else plain
    return []


def rank(lane: Lane, candidates: list) -> list:
    """Presets of the lane's material its spool names, best first. Empty
    when the spool's vendor and product match no preset."""
    vendor = (lane.vendor or "").strip().lower()
    product = (lane.filament_name or "").strip().lower()
    words = spool_words(lane)

    scored = []
    for c in of_material(lane, candidates):
        name = c.name.lower()
        score = 0
        if product and product in name:
            score += 20
        if vendor and (c.vendor.lower() == vendor or vendor in name):
            score += 10
        if score == 0:
            continue
        score -= 5 * sum(1 for word in name_words(c.name) if word not in words)
        scored.append((-score, not c.system, c.name, c))
    scored.sort(key=lambda item: item[:3])
    return [item[3] for item in scored]


def generic(lane: Lane, candidates: list) -> list:
    """The presets to fall back on for the lane's material: OrcaSlicer's
    "Generic <material>" first, then other system presets, then the user's,
    the plainest first in each ("Generic PLA" before "Generic PLA Silk")."""
    words = spool_words(lane)

    def key(c):
        extra = sum(1 for word in name_words(c.name) if word not in words)
        return not c.name.lower().startswith("generic "), not c.system, extra, c.name

    return sorted(of_material(lane, candidates), key=key)


class FilamentIds:
    """Reads presets' filament ids from their files and keeps them. Runs on
    the plugin's thread."""

    def __init__(self):
        self._ids = {}       # preset file -> filament id ("" when it has none)
        self._names = {}     # file name -> path, under the profile folders indexed so far
        self._roots = set()  # profile folders indexed
        self._system = ""    # OrcaSlicer's system profile folder, once a system preset showed it
        self._packs = {}     # .opc file -> {preset name: opc.Entry}

    def learn(self, candidates: list) -> None:
        """Note where the system presets live: a user preset's parent is one."""
        if not self._system:
            self._system = next((r for r in (_profiles_root(c.file) for c in candidates if c.system) if r), "")

    def of(self, candidate: Candidate) -> str:
        path = candidate.file
        if path not in self._ids:
            if os.path.isfile(path):
                self._ids[path] = self._read(path)
            else:
                self._ids[path] = self._packed(path, candidate.base or candidate.name)
        return self._ids[path]

    def _packed(self, path: str, name: str) -> str:
        """A system preset's id from its vendor's .opc: the path OrcaSlicer
        gives it is system/<vendor>/<sub path>, which no longer exists."""
        root = _profiles_root(path)
        if not root or not name:
            return ""
        vendor = os.path.relpath(path, root).split(os.sep)[0]
        return self._packed_id(root, vendor, name, set())

    def _packed_id(self, root: str, vendor: str, name: str, seen: set) -> str:
        """As OrcaSlicer resolves it: the preset's own id, else its parent's,
        looked up in its own vendor first, then in the filament library."""
        entries = self._pack(root, vendor)
        entry = entries.get(name)
        if entry is None or (vendor, name) in seen:
            return ""
        seen.add((vendor, name))
        if entry.filament_id or not entry.inherits:
            return entry.filament_id
        parent_vendor = vendor if entry.inherits in entries else LIBRARY
        return self._packed_id(root, parent_vendor, entry.inherits, seen)

    def _pack(self, root: str, vendor: str) -> dict:
        path = os.path.join(root, vendor + ".opc")
        if path not in self._packs:
            packed = opc.read(path)
            self._packs[path] = {e.name: e for e in packed.filaments} if packed is not None else {}
        return self._packs[path]

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

