"""Bambu's filament ids (GFA00 for Bambu PLA Basic) in OrcaSlicer's own.

A Bambu spool's tag names its filament by Bambu's catalog id, which AFC's RFID
reading saves as the Spoolman filament's article number. OrcaSlicer's presets
carry their own ids instead (docs/HLSD/filament_id.md), and OrcaSlicer ships the
map between the two as resources/printers/bambu_filament_ids.json. Without the
file every id stays untranslated.
"""

from __future__ import annotations

import json
import os
import re

BAMBU_ID = re.compile(r"GF[A-Z0-9]{2,6}")

_map = {}   # Bambu id -> OrcaSlicer id


def load(resources_dir: str) -> int:
    """Reads OrcaSlicer's map from its resources folder. Returns the number of
    ids it holds, 0 when the file is missing or unreadable."""
    try:
        with open(os.path.join(resources_dir, "printers", "bambu_filament_ids.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        return 0
    rows = data.get("filaments") if isinstance(data, dict) else None
    set_map({ours: row.get("bambu_id") for ours, row in rows.items() if isinstance(row, dict)}
            if isinstance(rows, dict) else {})
    return len(_map)


def set_map(ours_to_bambu: dict) -> None:
    """The map as the file has it: OrcaSlicer id -> Bambu id."""
    global _map
    _map = {bambu: ours for ours, bambu in ours_to_bambu.items()
            if isinstance(ours, str) and isinstance(bambu, str) and bambu}


def to_orca(bambu_id: str) -> str:
    """OrcaSlicer's id for a Bambu filament id, "" for one it has no row for."""
    return _map.get(bambu_id, "") if isinstance(bambu_id, str) else ""


def is_bambu(article: str) -> bool:
    return isinstance(article, str) and BAMBU_ID.fullmatch(article) is not None
