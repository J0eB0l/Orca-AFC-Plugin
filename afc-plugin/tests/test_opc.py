"""Reading OrcaSlicer's packed vendor profiles (system/<vendor>.opc)."""

import os
import struct
import tempfile
import unittest
import zlib

import fake_orca  # noqa: F401  (orca_afc imports orca)
from orca_afc import opc, presets


def s(text):
    data = text.encode()
    return struct.pack("<Q", len(data)) + data


def ss(items):
    return struct.pack("<Q", len(items)) + b"".join(s(i) for i in items)


def q(n):
    return struct.pack("<Q", n)


# One option of every type the cache writes, as PresetCacheFormat.cpp lays it out.
KEYS = ["f", "fs", "i", "is", "str", "strs", "p", "ps", "fp", "fps", "pt", "pts", "pt3", "b", "bs", "e", "es",
        "pg", "ig"]
TYPES = [1, 0x4001, 2, 0x4002, 3, 0x4003, 4, 0x4004, 5, 0x4005, 6, 0x4006, 7, 8, 0x4008, 9, 0x4009, 0x400A, 0x400B]
VALUES = [
    struct.pack("<d", 1.5), q(2) + struct.pack("<2d", 1, 2),
    struct.pack("<i", 3), q(3) + struct.pack("<3i", 1, 2, 3),
    s("text"), ss(["a", "bc"]),
    struct.pack("<d", 50), q(1) + struct.pack("<d", 50),
    struct.pack("<d?", 0.4, True), q(2) + struct.pack("<d?d?", 1, False, 2, True),
    struct.pack("<2d", 1, 2), q(2) + struct.pack("<4d", 0, 0, 1, 1),
    struct.pack("<3d", 1, 2, 3),
    b"\x01", q(3) + b"\x01\x00\x01",
    struct.pack("<IH", 1, 1),                              # a named enum value
    struct.pack("<IHHi", 2, 1, 0, -1),                     # a named one and a nil
    q(2) + q(1) + struct.pack("<2d", 1, 2) + q(0),
    q(1) + q(2) + struct.pack("<2i", 4, 5),
]
CONFIG = struct.pack("<I", len(VALUES)) + b"".join(struct.pack("<H", i) + v for i, v in enumerate(VALUES))


def entry(name, sub_path, inherits="", filament_id="", config=struct.pack("<I", 0)):
    return (s(name) + s(sub_path) + config + s(inherits) + ss(["include"]) + s("about it") + s("true") +
            s("setting") + s(filament_id) + ss(["old name"]))


def entries(items):
    return struct.pack("<I", len(items)) + b"".join(items)


def model(name, model_id):
    return (s("id") + s(name) + s(model_id) + b"\x00" + s("family") + ss(["0.4"]) + ss(["PLA"]) + ss([]) +
            b"".join(s("") for _ in range(9)))


def body(vendor, models=(), filaments=(), machines=(), version=2):
    profile = s(vendor) + s(vendor) + s("1.0.0") + s("") + s("") + q(len(models)) + b"".join(models) + ss([]) + ss([])
    return (struct.pack("<I", version) + s(vendor) + s("1.0.0") + ss(KEYS) + q(len(TYPES)) +
            struct.pack("<%dH" % len(TYPES), *TYPES) + ss(["", "on"]) + q(1) + s(vendor) + profile +
            entries([entry("0.20mm", "process/0.20mm.json", config=CONFIG)]) + entries(list(filaments)) +
            entries(list(machines)) + q(0))


def write(path, data, crc=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(struct.pack("<IIQI", opc.MAGIC, 2, len(data), zlib.crc32(data) if crc is None else crc) + data)
    return path


class ReadTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = folder.name

    def test_reads_every_option_type(self):
        path = write(os.path.join(self.root, "Custom.opc"), body(
            "Custom", models=[model("Generic ToolChanger Printer", "my_toolchanger_01")],
            filaments=[entry("Generic PLA @MyToolChanger", "filament/Generic PLA @MyToolChanger.json",
                             inherits="Generic PLA @System", config=CONFIG),
                       entry("Mine", "filament/Mine.json", filament_id="P123")],
            machines=[entry("Generic ToolChanger Printer 0.4 nozzle", "machine/MyToolChanger 0.4.json",
                            config=CONFIG)]))
        vendor = opc.read(path)
        self.assertEqual(vendor.name, "Custom")
        self.assertEqual(vendor.models, {"Generic ToolChanger Printer": "my_toolchanger_01"})
        self.assertEqual([(e.name, e.inherits, e.filament_id, e.instantiation) for e in vendor.filaments],
                         [("Generic PLA @MyToolChanger", "Generic PLA @System", "", "true"),
                          ("Mine", "", "P123", "true")])
        self.assertEqual([e.sub_path for e in vendor.machines], ["machine/MyToolChanger 0.4.json"])

    def test_refuses_what_it_cannot_read(self):
        data = body("Custom")
        self.assertIsNone(opc.read(write(os.path.join(self.root, "crc.opc"), data, crc=1)))
        self.assertIsNone(opc.read(write(os.path.join(self.root, "version.opc"), body("Custom", version=3))))
        self.assertIsNone(opc.read(write(os.path.join(self.root, "long.opc"), data + b"\x00")))
        self.assertIsNone(opc.read(write(os.path.join(self.root, "short.opc"), data[:-1])))
        self.assertIsNone(opc.read(os.path.join(self.root, "gone.opc")))


class PackedIdTest(unittest.TestCase):
    def test_ids_of_packed_system_presets(self):
        with tempfile.TemporaryDirectory() as root:
            system = os.path.join(root, "system")
            write(os.path.join(system, "OrcaFilamentLibrary.opc"), body("OrcaFilamentLibrary", filaments=[
                entry("Generic PLA @System", "filament/Generic PLA @System.json", inherits="fdm_filament_pla",
                      filament_id="OFDSrzZ8")]))
            write(os.path.join(system, "Custom.opc"), body("Custom", filaments=[
                entry("Generic PLA @MyToolChanger", "filament/Generic PLA @MyToolChanger.json",
                      inherits="Generic PLA @System"),
                entry("Loop", "filament/Loop.json", inherits="Loop")]))
            ids = presets.FilamentIds()

            def of(name, vendor="Custom"):
                path = os.path.join(system, vendor, "filament", name + ".json")  # as OrcaSlicer names it; not there
                return ids.of(presets.Candidate(name=name, type="PLA", vendor="", system=True, file=path, base=name))

            self.assertEqual(of("Generic PLA @MyToolChanger"), "OFDSrzZ8")
            self.assertEqual(of("Generic PLA @System", "OrcaFilamentLibrary"), "OFDSrzZ8")
            self.assertEqual(of("Loop"), "")
            self.assertEqual(of("Not there"), "")


if __name__ == "__main__":
    unittest.main()
