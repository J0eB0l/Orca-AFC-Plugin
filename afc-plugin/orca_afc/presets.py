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

import hashlib
import json
import os
import re
from dataclasses import dataclass

from . import bambu_ids, opc
from .protocol import Lane, material_key

# What OrcaSlicer's sync reads as "a filament it does not know": it then
# falls back to the generic preset of the tray's material.
UNKNOWN_FILAMENT_ID = "__unknown__"

# The vendor every other vendor's filament presets may inherit from.
LIBRARY = "OrcaFilamentLibrary"

# Words that make a spool support material, so support presets may fit it.
SUPPORT_WORDS = {"support", "breakaway"}

# Words a preset name holds for the plain product of its vendor and material.
PLAIN_WORDS = {"basic", "generic"}


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
    base_system: bool = False  # whether that preset is a system preset
    visible: bool = True  # ticked in the filament list; a hidden one is picked only by its filament id
    synced: bool = False  # a user preset the filament sync made for a spool, which each sync refreshes


def _first(value) -> str:
    """A setting OrcaSlicer hands over as one value or a per-extruder list.
    A list of strings comes serialized: values joined by ";", each one with a
    space, quote or ";" in it quoted C-style (["Bambu Lab"] is '"Bambu Lab"')."""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else ""
    text = "" if value is None else str(value)
    if not text.startswith('"'):
        return text.split(";", 1)[0]
    out, i = [], 1
    while i < len(text) and text[i] != '"':
        if text[i] == "\\" and i + 1 < len(text):
            i += 1
            out.append({"n": "\n", "r": "\r"}.get(text[i], text[i]))
        else:
            out.append(text[i])
        i += 1
    return "".join(out)


def works_out_ids(preset) -> bool:
    """Whether a printer preset has "Match and save filament IDs" ticked
    (afc_filament_ids): the plugin then works out a filament id for each of
    the printer's spools from its vendor and name and saves it to Spoolman and
    AFC. No preset, or an OrcaSlicer without the setting, counts as off."""
    return preset is not None and _first(preset.config_value("afc_filament_ids")).lower() in ("true", "1")


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




# How the notes of a preset the filament sync makes for a spool start (SpoolPreset.cpp SPOOL_PRESET_NOTE).
SPOOL_PRESET_NOTE = "Made by filament sync from the spool's data."


def collect(bundle) -> list:
    """The filament presets compatible with the selected printer that the
    sync can pick: the visible ones, and the hidden system ones, which
    OrcaSlicer's own sync picks by filament id too and then ticks. Runs on
    OrcaSlicer's UI thread."""
    out = []
    filaments = bundle.filaments
    for index in range(filaments.size()):
        preset = filaments.preset(index)
        if preset is None or preset.is_default:
            continue
        base = _base(filaments, preset)
        if (not preset.is_compatible or not (preset.is_visible or preset.is_system)
                or base is None or not base.is_compatible):
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
            base_system=bool(base.is_system),
            visible=bool(preset.is_visible),
            synced=not preset.is_system and _first(preset.config_value("filament_notes")).startswith(SPOOL_PRESET_NOTE),
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


def shown(candidates: list) -> list:
    """The presets ticked in the filament list, the only ones picked by name."""
    return [c for c in candidates if c.visible]


def rank(lane: Lane, candidates: list, hidden: bool = False) -> list:
    """Presets of the lane's material its spool names, best first, of the
    ticked ones unless `hidden`. Empty when the spool's vendor and product
    match no preset."""
    vendor = (lane.vendor or "").strip().lower()
    product = (lane.filament_name or "").strip().lower()
    words = spool_words(lane)

    scored = []
    for c in of_material(lane, candidates if hidden else shown(candidates)):
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

    return sorted(of_material(lane, shown(candidates)), key=key)


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
            packed = self._packed(path, candidate.base or candidate.name)
            if packed is not None:
                self._ids[path] = packed
            else:
                self._ids[path] = self._read(path) if os.path.isfile(path) else ""
        return self._ids[path]

    def _packed(self, path: str, name: str):
        """A system preset's id from its vendor's .opc, or None when OrcaSlicer
        reads the preset from its JSON file. The path OrcaSlicer gives a
        packed preset is system/<vendor>/<sub path>, which is gone or is left
        over from an older install, with that version's ids."""
        root = _profiles_root(path)
        if not root or not name:
            return None
        vendor = os.path.relpath(path, root).split(os.sep)[0]
        if name not in (self._pack(root, vendor) or {}):
            return None
        return self._packed_id(root, vendor, name, set())

    def _packed_id(self, root: str, vendor: str, name: str, seen: set) -> str:
        """As OrcaSlicer resolves it: the preset's own id, else its parent's,
        looked up in its own vendor first, then in the filament library."""
        entries = self._pack(root, vendor) or {}
        entry = entries.get(name)
        if entry is None or (vendor, name) in seen:
            return ""
        seen.add((vendor, name))
        if entry.filament_id or not entry.inherits:
            return entry.filament_id
        parent_vendor = vendor if entry.inherits in entries else LIBRARY
        return self._packed_id(root, parent_vendor, entry.inherits, seen)

    def _pack(self, root: str, vendor: str):
        """The vendor's filament presets by name when OrcaSlicer reads the
        vendor from its .opc, else None: as PresetBundle::read_vendor does, the
        .opc counts when its stamp is at least the version of the vendor's
        .json beside it, or there is no .json."""
        path = os.path.join(root, vendor + ".opc")
        if path not in self._packs:
            packed = opc.read(path)
            if packed is not None and not _covers(packed.version, os.path.join(root, vendor + ".json")):
                packed = None
            self._packs[path] = {e.name: e for e in packed.filaments} if packed is not None else None
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


def _covers(stamp: str, profile: str) -> bool:
    """Whether an .opc made from vendor version `stamp` is at least the
    vendor .json `profile` (PresetCacheFormat.cpp cache_covers_version)."""
    if not os.path.exists(profile):
        return True
    data = _load(profile)
    on_disk = _version(data.get("version")) if isinstance(data, dict) else None
    cached = _version(stamp)
    return on_disk is not None and cached is not None and cached >= on_disk


def _version(text) -> tuple:
    """A profile version's numbers: "02.03.02.60" -> (2, 3, 2, 60); None when it has none."""
    found = re.match(r"\s*(\d+(?:\.\d+)*)", str(text or ""))
    return tuple(int(n) for n in found.group(1).split(".")) if found else None


def _load(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------------ the spool's own preset
#
# OrcaSlicer's filament sync looks for a preset named like the spool (and makes
# one, "<name> @<printer>", copied from the best fitting preset, when there is
# none), as the Snapmaker build's AFCProtocol::spool_preset_name does.


def clean_preset_part(text: str) -> str:
    """Single spaces, no ends, and "-" for what a preset file name or its " @" cannot hold."""
    out = re.sub(r"[\x00- ]+", " ", text or "").strip()
    return re.sub(r'[<>:"/\\|?*@]', "-", out)


def _is_unknown(text: str) -> bool:
    return not text or text in ("NONE", "None", "none")


def filament_display_name(vendor: str, material: str, sub_type: str) -> str:
    if sub_type == "Support":
        return "%s Support For %s" % (vendor, material)
    return "%s %s%s" % (vendor, material, "" if _is_unknown(sub_type) else " " + sub_type)


def _without_vendor(name: str, vendor: str) -> str:
    """"Polymaker PolyLite PLA" without its "Polymaker "."""
    low, vendor = name.lower(), vendor.lower()
    if low == vendor:
        return ""
    if low.startswith(vendor + " "):
        return clean_preset_part(name[len(vendor) + 1:])
    return name


def picked_spool_info(preset_name: str, candidates: list) -> tuple:
    """(vendor, filament name) for AFC's SET_FILAMENT_INFO from the preset picked
    in the tray popup: "Polymaker PolyLite PLA @System" -> ("Polymaker",
    "PolyLite PLA"). The vendor is the preset's filament_vendor, none for
    "Generic"; the name is the preset's without its "@..." and without the
    vendor, or the vendor's first word ("Bambu Lab" off "Bambu PLA Basic"), as
    Spoolman names a filament. The vendor is None when the preset is not one
    of `candidates` (unknown, so AFC's is kept); both are None for no preset."""
    base = clean_preset_part((preset_name or "").split("@", 1)[0])
    if not base:
        return None, None
    vendor = next((c.vendor for c in candidates if c.name == preset_name), None)
    if vendor is None:
        return None, base
    vendor = re.sub(r"[\x00- ]+", " ", vendor).strip()
    if _is_unknown(vendor) or vendor.lower() == "generic":
        return "", base
    name = base
    for prefix in (vendor, vendor.split(" ", 1)[0]):
        name = _without_vendor(name, prefix)
        if name != base:
            break
    return vendor, name or base


def _names_material(name: str, material: str) -> bool:
    """A word of `name` starts with the material: "PLA" in "PolyLite PLA" or "Tough PLA-CF"."""
    material = material.lower()
    return any(word.startswith(material) for word in name.lower().split(" "))


def _spool_parts(lane: Lane) -> tuple:
    """(vendor, filament name without the vendor, material), cleaned; no vendor
    for none or "Generic"."""
    vendor = clean_preset_part(lane.vendor)
    if _is_unknown(vendor) or vendor.lower() == "generic":
        vendor = ""
    material = "" if _is_unknown(lane.material) else clean_preset_part(lane.material)
    filament = clean_preset_part(lane.filament_name)
    filament = _without_vendor(filament, vendor) if vendor and not _is_unknown(filament) else ""
    return vendor, filament, material


def spool_preset_name(lane: Lane) -> str:
    """The name of the spool's own preset: "Polymaker PolyLite PLA", "" without a vendor."""
    vendor, filament, material = _spool_parts(lane)
    if not vendor:
        return ""
    if not filament:
        if not material:
            return ""
        return clean_preset_part(filament_display_name(vendor, material, clean_preset_part(lane.sub_type)))
    if material and not _names_material(filament, material):
        return "%s %s %s" % (vendor, material, filament)
    return "%s %s" % (vendor, filament)


def afc_lane_preset_name(lane: Lane) -> str:
    """The preset for a spool AFC knows only the material and temperatures of:
    "AFC PLA 210C bed 60C"."""
    if _is_unknown(lane.material) or (lane.nozzle_temp <= 0 and lane.bed_temp <= 0):
        return ""
    name = "AFC " + lane.material + ("" if _is_unknown(lane.sub_type) else " " + lane.sub_type)
    if lane.nozzle_temp > 0:
        name += " %dC" % lane.nozzle_temp
    if lane.bed_temp > 0:
        name += " bed %dC" % lane.bed_temp
    return clean_preset_part(name)


def spool_preset_names(lane: Lane) -> list:
    """Names the spool's own preset may have, best first: its full name, then
    the filament name alone; for a spool with no vendor, AFC's lane preset."""
    name = spool_preset_name(lane)
    if not name:
        afc = afc_lane_preset_name(lane)
        return [afc] if afc else []
    out = [name]
    _, filament, material = _spool_parts(lane)
    if filament and filament.lower() not in (material.lower(), name.lower()):
        out.append(filament)
    return out


def lane_id(lane: Lane) -> str:
    """The OrcaSlicer filament id the lane's spool names: its Spoolman
    filament's, else the one its Bambu tag's id translates to."""
    return lane.filament_id or bambu_ids.to_orca(lane.bambu_id)


def by_id(lane: Lane, candidates: list, ids: FilamentIds) -> list:
    """The presets of the lane's material that carry the filament id its
    spool names (lane_id()), ticked and system presets first. Empty without
    one."""
    wanted = lane_id(lane)
    if not wanted:
        return []
    ids.learn(candidates)
    found = [c for c in of_material(lane, candidates) if ids.of(c) == wanted]
    return sorted(found, key=lambda c: (not c.visible, not c.system, c.name))


def by_product(lane: Lane, candidates: list, ids: FilamentIds) -> list:
    """For a spool that names no filament id, the presets carrying the id
    product_id() would save for it, ticked and system presets first, so a
    filament Spoolman names by its color ("Matte Scarlet Red") syncs as its
    product before Spoolman holds the id."""
    if lane_id(lane):
        return []
    wanted = product_id(lane, candidates, ids)
    if not wanted:
        return []
    found = [c for c in of_material(lane, candidates) if ids.of(c) == wanted]
    return sorted(found, key=lambda c: (not c.visible, not c.system, c.name))


def best(lane: Lane, candidates: list, ids: FilamentIds | None = None, work_out: bool = True) -> str:
    """The name of the preset that fits the lane's spool best: the one a
    spool's own preset is copied from. "" when none is of its material.
    `work_out` off leaves out the preset product_id() works out for a spool
    that names no id (by_product())."""
    found = ((by_id(lane, candidates, ids) or (by_product(lane, candidates, ids) if work_out else [])) if ids else []) \
        or rank(lane, candidates) or generic(lane, candidates)
    return found[0].name if found else ""


def match(lane: Lane, candidates: list, ids: FilamentIds, work_out: bool = True) -> str:
    """The filament id the sync should pick for a loaded lane: the id AFC
    holds for it when a preset carries it, else, with `work_out` (the
    printer's "Match and save filament IDs"), the one product_id() reads from
    its vendor and name, else the best scored preset's, else the generic
    one's, else UNKNOWN_FILAMENT_ID so the sync still falls back on the
    material."""
    ids.learn(candidates)
    found = by_id(lane, candidates, ids) + (by_product(lane, candidates, ids) if work_out else [])
    for candidate in found + rank(lane, candidates) + generic(lane, candidates):
        filament_id = ids.of(candidate)
        if filament_id:
            return filament_id
    return UNKNOWN_FILAMENT_ID


def product_words(candidate: Candidate, lane: Lane) -> set:
    """The words of a preset's name that name its product, which a spool must
    name too: "Bambu PLA Matte" asks for "matte", "Polymaker PolyLite PLA"
    for "polylite", and "Bambu PLA Basic" for nothing, as "basic" is what a
    spool named only by its color is."""
    skip = set(name_words(candidate.vendor)) | set(name_words(lane.vendor or "")) | PLAIN_WORDS
    for kind in [candidate.type] + lane_types(lane):
        skip.update(name_words(kind))
    return set(name_words(candidate.name)) - skip


def _of_vendor(candidate: Candidate, vendor: str) -> bool:
    """Whether a preset is the spool vendor's: by its vendor, or the whole
    words of the vendor's name in its own ("Bambu" for "Bambu PLA Basic")."""
    return candidate.vendor.lower() == vendor or set(name_words(vendor)) <= set(name_words(candidate.name))


def product_miss(lane: Lane, candidates: list, ids: FilamentIds) -> str:
    """Why product_id() found no id for the lane's spool, for the Filament
    IDs sheet: Spoolman names no brand, OrcaSlicer has no preset of its brand
    and material for this printer, those presets carry no id, or its name
    names none of their products."""
    vendor = (lane.vendor or "").strip().lower()
    if not vendor or vendor == "generic":
        return "No brand in Spoolman"
    ids.learn(candidates)
    of_vendor = [c for c in of_material(lane, candidates) if c.base_system and _of_vendor(c, vendor)]
    if not of_vendor:
        return "OrcaSlicer has no %s %s preset for this printer" % (lane.vendor, lane.material)
    names = ", ".join(c.name.split(" @")[0] for c in of_vendor[:2])
    if not any(ids.of(c) for c in of_vendor):
        return "OrcaSlicer's %s presets have no filament ID" % names
    return "The name matches none of OrcaSlicer's %s %s presets (%s)" % (lane.vendor, lane.material, names)


def product_id(lane: Lane, candidates: list, ids: FilamentIds) -> str:
    """The id to save on the lane's Spoolman filament: that of a preset that
    is, or comes from, a system preset of the spool's vendor whose product
    words (product_words()) the spool's name all holds, and no product word
    of another preset of its material, the one naming the most of them, the
    best scored on a tie. "Matte Scarlet Red" gets "Bambu
    PLA Matte", "Jade White" "Bambu PLA Basic". "" when none does, as a
    generic or another product's id would keep OrcaSlicer on that preset for
    good. A Bambu tag's id that a preset carries comes first."""
    translated = "" if lane.filament_id else bambu_ids.to_orca(lane.bambu_id)
    if translated and by_id(lane, candidates, ids):
        return translated
    vendor = (lane.vendor or "").strip().lower()
    if not vendor or vendor == "generic" or not (lane.filament_name or "").strip():
        return ""
    ids.learn(candidates)
    named = set(name_words(lane.filament_name))
    # Product words any system preset of the material has: one the spool
    # names rules out a preset without it ("ePLA-Silk Bronze" is not "eSUN
    # PLA+"). Not a user preset's, as one named for a spool ("Bambu Lab Jade
    # White PLA #3") would make its colors product words.
    known = set().union(*(product_words(c, lane) for c in of_material(lane, candidates) if c.system))
    found, most = "", -1
    for candidate in rank(lane, candidates, hidden=True):
        if not candidate.base_system:
            continue
        if not _of_vendor(candidate, vendor):
            continue
        wanted = product_words(candidate, lane)
        if len(wanted) <= most or not wanted <= named or (named & known) - wanted:
            continue
        filament_id = ids.of(candidate)
        if filament_id:
            found, most = filament_id, len(wanted)
    return found


def user_filament_id(name: str) -> str:
    """The filament id OrcaSlicer gives a filament the user makes ("Create
    filament"): "P" and the first 7 hex digits of the MD5 of its name, so
    one name gets one id on every machine. OrcaSlicer's filament sync gives
    the same to the preset it makes for a spool that is no system filament."""
    return "P" + hashlib.md5(name.strip().encode("utf-8")).hexdigest()[:7]


def own_id(lane: Lane, candidates: list, ids: FilamentIds) -> str:
    """The id of the preset the sync gives the lane's spool when no system
    filament is its product (product_id() finds none). The sync takes the
    one named like the spool, the user's before a system one: a system
    preset's id; "" for one inheriting another preset, which carries that
    product's id; the user's own filament's id. A preset the sync made, or
    none, carries user_filament_id() of its name, which "" stands for when
    another filament already holds it (the sync salts that one's). "" for a
    spool with no vendor to name a preset."""
    if not spool_preset_name(lane).strip():
        return ""
    ids.learn(candidates)
    names = [n.strip() for n in spool_preset_names(lane)]
    name = names[0]
    for wanted in names:
        found = [c for c in candidates if _base_key(c.name) == wanted.lower()]
        found.sort(key=lambda c: c.system)
        if found:
            c = found[0]
            if c.system:
                return ids.of(c)
            if c.inherits:
                return ""
            if not c.synced:
                return ids.of(c)
            name = c.name.split("@", 1)[0].strip()
            break
    key = name.lower()
    filament_id = user_filament_id(name)
    if any(ids.of(c) == filament_id and _base_key(c.name) != key for c in candidates):
        return ""
    return filament_id


def _base_key(name: str) -> str:
    """A preset's name without its "@..." part, lower case, as the sync compares them."""
    return name.split("@", 1)[0].strip().lower()
