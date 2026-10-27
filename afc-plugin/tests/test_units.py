"""Each AFC unit's lane change time, for the suggested spool layout."""

import unittest

import fake_orca  # noqa: F401
from orca_afc import units
from orca_afc.protocol import Dryer, Snapshot, Unit


class UnitsTest(unittest.TestCase):
    def test_default_order(self):
        kinds = [units.kind(unit) for unit in (
            Unit(type="Box_Turtle"), Unit(type="AFC_EMU"), Unit(type="BambuAMS", dryer=Dryer(kind="bambu")),
            Unit(type="ACE2"), Unit(type="BambuAMS"), Unit(type="OpenAMS"), Unit(type="Night_Owl"), Unit(type="AFC_ACE"))]
        self.assertEqual(kinds, list(units.DEFAULT_ORDER))
        seconds = [units.default_seconds(Unit(type=t)) for t in ("Box_Turtle", "EMU", "ACE2", "OpenAMS", "ACE")]
        self.assertEqual(seconds, sorted(seconds))

    def test_measured_from_the_log(self):
        snapshot = Snapshot(units=[Unit(name="Turtle_1", type="Box_Turtle", lanes=["lane0", "lane1"]),
                                   Unit(name="Ace_1", type="ACE", lanes=["lane4"]), Unit(name="Empty_1", type="EMU")])
        lanes = {"lane0": {"load": [30, 32], "unload": [40, 41, 39]}, "lane1": {"load": [31], "unload": []},
                 "lane4": {"load": [20, 21], "unload": [20, 20, 20]}}
        self.assertEqual(units.unit_times(snapshot, lanes), [
            {"name": "Turtle_1", "type": "Box_Turtle", "seconds": 71.0, "measured": True},
            {"name": "Ace_1", "type": "ACE", "seconds": units.default_seconds(Unit(type="ACE")), "measured": False}])


if __name__ == "__main__":
    unittest.main()
