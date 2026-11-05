import unittest

import fake_orca  # noqa: F401

from orca_afc import keepouts

AFC = {"poop": True, "kick": True, "wipe": True}
POOP = {"purge_loc_xy": [345, 345]}
KICK = {"kick_start_loc": [285, 345, 5], "kick_direction": "right", "kick_move_dist": 60}
BRUSH = {"brush_loc": [100, 20, 0.9], "brush_width": 30, "brush_depth": 10}


def status(**macros):
    return {"gcode_macro _AFC_%s_VARS" % name.upper().replace("__", "_"): vars for name, vars in macros.items()}


class BedKeepoutsTest(unittest.TestCase):
    def test_macro_objects(self):
        names = ["gcode_macro _AFC_POOP_VARS", "gcode_macro _AFC_KICK_VARS_extruder1", "gcode_macro _AFC_PARK_VARS",
                 "gcode_macro _CTC_PURGE_VARS", "extruder"]
        self.assertEqual(keepouts.macro_objects(names), names[:2])

    def test_kick_path_holds_the_purge_spot(self):
        # Printer 1: the purge spot sits inside the kick path, both clipped to the axes.
        self.assertEqual(keepouts.bed_keepouts(status(poop=POOP, kick=KICK), AFC, [353.8, 351]),
                         "270x330, 353.8x330, 353.8x351, 270x351")

    def test_brush_and_purge(self):
        out = keepouts.bed_keepouts(status(poop={"purge_loc_xy": [20, 300]}, brush=BRUSH), AFC, [350, 350])
        self.assertEqual(out.split(" | "), ["80x10, 120x10, 120x30, 80x30", "5x285, 35x285, 35x315, 5x315"])

    def test_off_or_unset_spots_are_left_out(self):
        unset = status(poop={"purge_loc_xy": [-99, -99]}, kick={"kick_start_loc": [-99, -99, 5]},
                       brush={"brush_loc": [-1, -1, -1]})
        self.assertEqual(keepouts.bed_keepouts(unset, AFC, [271, 335]), "")
        self.assertEqual(keepouts.bed_keepouts(status(poop=POOP, brush=BRUSH), {"poop": False, "wipe": False}), "")
        self.assertEqual(keepouts.bed_keepouts(status(kick=dict(KICK, kick_direction="")), AFC), "")

    def test_spot_off_the_bed(self):
        # Printer 1's brush sits in front of the bed.
        brush = {"brush_loc": [232.85, -7.5, 0.9], "brush_width": 30, "brush_depth": 2}
        self.assertEqual(keepouts.bed_keepouts(status(brush=brush), AFC, [353.8, 351]), "")

    def test_toolhead_override(self):
        macros = status(poop=POOP)
        macros["gcode_macro _AFC_POOP_VARS_extruder1"] = {"purge_loc_xy": [20, 20]}
        out = keepouts.bed_keepouts(macros, AFC, [350, 350])
        self.assertEqual(out.split(" | "), ["330x330, 350x330, 350x350, 330x350", "5x5, 35x5, 35x35, 5x35"])


if __name__ == "__main__":
    unittest.main()
