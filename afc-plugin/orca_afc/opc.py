"""OrcaSlicer's packed vendor profiles (system/<vendor>.opc).

OrcaSlicer 2.5 installs a vendor's system presets as one binary cache file
instead of the JSON files their paths still name, and its plugin API does not
hand out a preset's filament id, so the ids are read from these files. The
layout follows PresetCacheFormat.cpp (cache version 2): a 20-byte header, then
a cereal binary body of the vendor's stamps, an option dictionary, the vendor
profiles and the process, filament and machine presets in source form. A file
of another version, or one that does not parse to its end, reads as None.
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass

MAGIC = 0x4F52435A  # "ORCZ"
VERSION = 2

# ConfigOptionType (Config.hpp): the bytes each option value takes on the wire.
_VECTOR = 0x4000
_FIXED = {1: 8, 2: 4, 4: 8, 5: 9, 6: 16, 7: 24, 8: 1}  # float, int, percent, float or percent, point, point3, bool
_ENUM = 9
_STRING = 3
_POINTS_GROUPS = 10 | _VECTOR
_INTS_GROUPS = 11 | _VECTOR


@dataclass
class Entry:
    """A preset as its vendor's JSON states it, nothing inherited."""
    name: str
    sub_path: str
    inherits: str
    instantiation: str
    filament_id: str


@dataclass
class Vendor:
    name: str
    models: dict      # printer model name -> model_id
    filaments: list   # Entry
    machines: list    # Entry


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        if n < 0 or self.pos + n > len(self.data):
            raise ValueError("past the end")
        out = self.data[self.pos:self.pos + n]
        self.pos += n
        return out

    def skip(self, n: int) -> None:
        self.take(n)

    def u16(self) -> int:
        return struct.unpack("<H", self.take(2))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def string(self) -> str:
        return self.take(self.u64()).decode("utf-8", "replace")

    def strings(self) -> list:
        return [self.string() for _ in range(self.u64())]


def _skip_value(r: _Reader, kind: int) -> None:
    if kind in _FIXED:
        r.skip(_FIXED[kind])
    elif kind & _VECTOR and kind & ~_VECTOR in _FIXED:
        r.skip(r.u64() * _FIXED[kind & ~_VECTOR])
    elif kind == _STRING:
        r.skip(r.u64())
    elif kind == _STRING | _VECTOR:
        r.strings()
    elif kind in (_POINTS_GROUPS, _INTS_GROUPS):
        size = 16 if kind == _POINTS_GROUPS else 4
        for _ in range(r.u64()):
            r.skip(r.u64() * size)
    else:
        raise ValueError("unknown option type %d" % kind)


def _skip_config(r: _Reader, types: list) -> None:
    for _ in range(r.u32()):
        kind = types[r.u16()]
        if kind & ~_VECTOR == _ENUM:
            for _ in range(r.u32()):
                if r.u16() == 0:  # a value the writer could not name travels as an int
                    r.skip(4)
        else:
            _skip_value(r, kind)


def _entries(r: _Reader, types: list) -> list:
    out = []
    for _ in range(r.u32()):
        name, sub_path = r.string(), r.string()
        _skip_config(r, types)
        inherits = r.string()
        r.strings()   # includes
        r.string()    # description
        instantiation = r.string()
        r.string()    # setting_id
        filament_id = r.string()
        r.strings()   # renamed_from
        out.append(Entry(name, sub_path, inherits, instantiation, filament_id))
    return out


def _models(r: _Reader) -> dict:
    out = {}
    for _ in range(r.u64()):  # vendor profiles by id
        r.string()
        for _ in range(5):    # name, id, config version, update and changelog URLs
            r.string()
        for _ in range(r.u64()):
            r.string()                      # id
            name, model_id = r.string(), r.string()
            r.skip(1)                       # technology (an unsigned char)
            r.string()                      # family
            r.strings()                     # variants
            r.strings()                     # default materials
            r.strings()                     # unsupported bed types
            for _ in range(9):              # bed model and texture settings, hotend model
                r.string()
            out[name] = model_id
        r.strings()  # default filaments
        r.strings()  # default SLA materials
    return out


def read(path: str):
    """The vendor in an .opc file, or None when it is not one this reads."""
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return None
    if len(data) < 20:
        return None
    magic, _, size, crc = struct.unpack("<IIQI", data[:20])
    body = data[20:]
    if magic != MAGIC or size != len(body) or zlib.crc32(body) != crc:
        return None
    r = _Reader(body)
    try:
        if r.u32() != VERSION:
            return None
        name = r.string()
        r.string()  # vendor version
        r.strings()  # option names
        count = r.u64()
        types = list(struct.unpack("<%dH" % count, r.take(2 * count)))
        r.strings()  # enum value names
        models = _models(r)
        _entries(r, types)  # process presets
        filaments = _entries(r, types)
        machines = _entries(r, types)
        r.u64()  # parse errors
    except (ValueError, IndexError, struct.error):
        return None
    if r.pos != len(body):
        return None
    return Vendor(name, models, filaments, machines)
