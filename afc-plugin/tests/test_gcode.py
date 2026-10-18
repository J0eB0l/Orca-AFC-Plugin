"""The AFC rewrite of a sliced file: T numbers, lane-change purges, spool PA."""

import math
import unittest

import fake_orca  # noqa: F401  (puts afc-plugin/ on sys.path)
from orca_afc import gcode as g
from orca_afc.protocol import Lane, Snapshot

# Three filaments; the flush table holds one 3x3 block (one nozzle) or two (two nozzles).
FLUSH_ONE = [0, 100, 200,
             110, 0, 210,
             120, 130, 0]


def config_block(flavor="klipper", semm="0", flush=FLUSH_ONE, nozzles="0.4", adaptive="0,0,0",
                 diameters="1.75,1.75,2.85", extra=""):
    return ("; CONFIG_BLOCK_START\n"
            "; adaptive_pressure_advance = %s\n"
            "; filament_colour = #FF0000;#00FF00;#0000FF\n"
            "; filament_diameter = %s\n"
            "; flush_volumes_matrix = %s\n"
            "; gcode_flavor = %s\n"
            "; nozzle_diameter = %s\n"
            "; single_extruder_multi_material = %s\n"
            "%s"
            "; CONFIG_BLOCK_END\n") % (adaptive, diameters, ",".join(str(v) for v in flush), flavor, nozzles, semm, extra)


# "Purge in prime tower" with Multi-material per toolhead, sliced with filaments 0 and 1 on toolhead 0.
TOWER = ("; enable_prime_tower = 1\n; purge_in_prime_tower = 1\n; multi_extruder_multi_material = 1\n"
         "; wipe_tower_type = type2\n; filament_toolhead = 0,0,1\n")


def tower_change(tool):
    return "; CP TOOLCHANGE START\nG1 X10\nT%d\nG1 X20\n; CP TOOLCHANGE END\n" % tool


def area(d):
    return math.pi / 4 * d * d


def purge(text, line_no):
    line = text.splitlines()[line_no]
    if "PURGE_LENGTH=" not in line:
        return None
    return float(line.split("PURGE_LENGTH=")[1].split()[0])


class ConfigTest(unittest.TestCase):
    def test_reads_the_block(self):
        settings = g.FileSettings.from_config(g.read_config_block("G28\n" + config_block()))
        self.assertTrue(settings.klipper)
        self.assertFalse(settings.single_extruder_mm)
        self.assertTrue(settings.purges)
        self.assertEqual(settings.filaments, 3)
        self.assertEqual(settings.diameters, [1.75, 1.75, 2.85])
        self.assertEqual(settings.flush_volume(0, 0, 1), 100)
        self.assertEqual(settings.flush_volume(0, 2, 1), 130)
        self.assertEqual(settings.flush_volume(0, 1, 5), 0)
        self.assertFalse(settings.tower_flushes)

    def test_flush_multiplier_per_nozzle(self):
        settings = g.FileSettings.from_config(g.read_config_block(
            config_block(nozzles="0.4,0.4", extra="; flush_multiplier = 1.5,0.5\n")))
        self.assertEqual(settings.flush_volume(0, 0, 1), 150)
        self.assertEqual(settings.flush_volume(1, 0, 1), 50)

    def test_tower_flushes(self):
        settings = g.FileSettings.from_config(g.read_config_block(config_block(extra=TOWER)))
        self.assertTrue(settings.tower_flushes)
        self.assertEqual(settings.sliced_toolheads, [0, 0, 1])
        for off in ("; purge_in_prime_tower = 0\n", "; multi_extruder_multi_material = 0\n",
                    "; enable_prime_tower = 0\n", "; wipe_tower_type = type1\n"):
            # A later key in the block wins, as it would on reading the block.
            settings = g.FileSettings.from_config(g.read_config_block(config_block(extra=TOWER + off)))
            self.assertFalse(settings.tower_flushes, off)

    def test_one_block_per_nozzle(self):
        settings = g.FileSettings.from_config(g.read_config_block(
            config_block(flush=FLUSH_ONE + [v * 2 for v in FLUSH_ONE], nozzles="0.4,0.4")))
        self.assertEqual(settings.nozzles, 2)
        self.assertEqual(settings.flush_volume(0, 0, 1), 100)
        self.assertEqual(settings.flush_volume(1, 0, 1), 200)
        # A toolhead past the table's blocks uses the last one.
        self.assertEqual(settings.flush_volume(3, 0, 1), 200)

    def test_no_block(self):
        self.assertEqual(g.read_config_block("G28\nT0\n"), {})
        self.assertFalse(g.FileSettings.from_config({}).klipper)


class PurgeTest(unittest.TestCase):
    def run_file(self, body, plan, **config):
        text = body + config_block(**config)
        return g.transform(text, plan).text

    def test_lane_change_on_one_toolhead(self):
        plan = g.Plan(toolheads={0: 0, 1: 0, 2: 1})
        out = self.run_file("T0\nG1 X1\nT1\nG1 X2\nT2\nG1 X3\nT0\n", plan)
        self.assertIsNone(purge(out, 0))           # the first lane toolhead 0 loads
        self.assertAlmostEqual(purge(out, 2), round(100 / area(1.75), 1))
        self.assertIsNone(purge(out, 4))           # the first lane toolhead 1 loads
        self.assertAlmostEqual(purge(out, 6), round(110 / area(1.75), 1))  # back from T1 on toolhead 0

    def test_toolhead_switch_holding_its_filament(self):
        plan = g.Plan(toolheads={0: 0, 2: 1})
        out = self.run_file("T0\nT2\nT0\nT2\n", plan)
        self.assertNotIn("PURGE_LENGTH", out)

    def test_uses_the_new_filaments_diameter(self):
        out = self.run_file("T0\nT2\n", g.Plan(toolheads={0: 0, 2: 0}))
        self.assertAlmostEqual(purge(out, 1), round(200 / area(2.85), 1))

    def test_replaces_a_purge_the_file_had(self):
        out = self.run_file("T0\nT1 PURGE_LENGTH=99 ; change\n", g.Plan(toolheads={0: 0, 1: 0}))
        self.assertEqual(out.splitlines()[1], "T1 PURGE_LENGTH=%s ; change" % g._format(100 / area(1.75), 1))

    def test_unknown_toolhead_keeps_the_line(self):
        out = self.run_file("T0\nT1 PURGE_LENGTH=99\n", g.Plan(toolheads={0: 0}))
        self.assertEqual(out.splitlines()[1], "T1 PURGE_LENGTH=99")

    def test_tower_flushed_lane_change(self):
        out = self.run_file("T0\n" + tower_change(1) + tower_change(2) + tower_change(0), g.Plan(toolheads={0: 0, 1: 0, 2: 1}),
                            extra=TOWER)
        self.assertNotIn("PURGE_LENGTH", out)  # 0 -> 1 on toolhead 0 was flushed on the tower, as was 1 -> 0

    def test_tower_lane_change_the_mapping_moved(self):
        # Sliced with filament 2 on toolhead 1, the print dialog put it on toolhead 0's lanes.
        out = self.run_file("T0\n" + tower_change(2), g.Plan(toolheads={0: 0, 2: 0}), extra=TOWER)
        self.assertAlmostEqual(purge(out, 3), round(200 / area(2.85), 1))

    def test_tower_switch_the_mapping_made_a_lane_change(self):
        # Sliced on two toolheads, the tower only primed; the mapping put both on toolhead 0.
        out = self.run_file("T0\n" + tower_change(1), g.Plan(toolheads={0: 0, 1: 0}),
                            extra=TOWER.replace("filament_toolhead = 0,0,1", "filament_toolhead = 0,1,1"))
        self.assertAlmostEqual(purge(out, 3), round(100 / area(1.75), 1))

    def test_lane_change_off_the_tower_still_purges(self):
        out = self.run_file("T0\nT1\n", g.Plan(toolheads={0: 0, 1: 0}), extra=TOWER)
        self.assertAlmostEqual(purge(out, 1), round(100 / area(1.75), 1))

    def test_tower_flushes_with_single_extruder_mm(self):
        out = self.run_file("T0\nT1\n", g.Plan(toolheads={0: 0, 1: 0}), semm="1")
        self.assertNotIn("PURGE_LENGTH", out)

    def test_other_flavors_unchanged(self):
        text = "T0\nT1\n" + config_block(flavor="marlin2")
        self.assertEqual(g.transform(text, g.Plan(tools={0: 3}, toolheads={0: 0, 1: 0})).text, text)

    def test_other_lines_unchanged(self):
        body = "TIMELAPSE_TAKE_FRAME\nT0\n; T1 in a comment\nT12345\nTURN_OFF_HEATERS\n"
        out = self.run_file(body, g.Plan(toolheads={0: 0, 1: 0}))
        self.assertTrue(out.startswith(body))

    def test_keeps_windows_line_ends(self):
        out = g.transform("T0\r\nT1\r\n" + config_block(), g.Plan(toolheads={0: 0, 1: 0})).text
        self.assertTrue(out.startswith("T0\r\nT1 PURGE_LENGTH="))
        self.assertTrue(out.splitlines(keepends=True)[1].endswith("\r\n"))


class RenumberTest(unittest.TestCase):
    def test_tool_numbers_follow_the_mapping(self):
        body = ("M104 T0 S210\nM109 S220 T1 ; wait\nM104 A1 T1 S200\n"
                "PRINT_START TOOL=0 INITIAL_TOOL=0 T0_TEMP=210 T1_TEMP=220 BED=60\n"
                "T0\nT1\nT0\n")
        plan = g.Plan(tools={0: 4, 1: 2}, toolheads={0: 0, 1: 0})
        lines = g.transform(body + config_block(), plan).text.splitlines()
        self.assertEqual(lines[0], "M104 T4 S210")
        self.assertEqual(lines[1], "M109 S220 T2 ; wait")
        self.assertEqual(lines[2], "M104 A1 T1 S200")  # a Snapmaker extruder number stays
        self.assertEqual(lines[3], "PRINT_START TOOL=4 INITIAL_TOOL=4 T4_TEMP=210 T2_TEMP=220 BED=60")
        self.assertEqual(lines[4], "T4")
        # The purge is the file's filament 0 -> 1, whatever T numbers they print as.
        self.assertEqual(lines[5], "T2 PURGE_LENGTH=%s" % g._format(100 / area(1.75), 1))
        self.assertTrue(lines[6].startswith("T4 PURGE_LENGTH="))

    def test_unmapped_filaments_keep_their_number(self):
        lines = g.transform("T0\nT2\n" + config_block(), g.Plan(tools={1: 5})).text.splitlines()
        self.assertEqual(lines[:2], ["T0", "T2"])


class PressureAdvanceTest(unittest.TestCase):
    def test_spool_k_after_its_tool_change(self):
        body = ("SET_PRESSURE_ADVANCE ADVANCE=0.05\nT0\nSET_PRESSURE_ADVANCE ADVANCE=0.04; Override pressure advance value\n"
                "T1\nSET_PRESSURE_ADVANCE ADVANCE=0.04; Override pressure advance value\n"
                "SET_PRESSURE_ADVANCE ADVANCE=0.03 EXTRUDER=extruder1\n")
        result = g.transform(body + config_block(), g.Plan(flow_k={1: 0.0223}))
        lines = result.text.splitlines()
        self.assertEqual(lines[0], "SET_PRESSURE_ADVANCE ADVANCE=0.05")       # before any tool change
        self.assertEqual(lines[2], "SET_PRESSURE_ADVANCE ADVANCE=0.04; Override pressure advance value")  # T0 has no K
        self.assertEqual(lines[3], "T1")
        self.assertEqual(lines[4], "SET_PRESSURE_ADVANCE ADVANCE=0.0223 ; AFC spool flow K")
        self.assertEqual(lines[5], "SET_PRESSURE_ADVANCE ADVANCE=0.0223; Override pressure advance value")
        self.assertEqual(lines[6], "SET_PRESSURE_ADVANCE ADVANCE=0.03 EXTRUDER=extruder1")  # another extruder's
        self.assertEqual(result.pressure_advance, 1)

    def test_a_second_pass_keeps_one_spool_k(self):
        # The agent rewrites files the AFC G-code step may already have rewritten.
        text = "T1\nG1 X1 E1\n" + config_block()
        once = g.transform(text, g.Plan(flow_k={1: 0.0223})).text
        twice = g.transform(once, g.Plan(flow_k={1: 0.025})).text
        self.assertEqual(twice.splitlines()[:3], ["T1", "SET_PRESSURE_ADVANCE ADVANCE=0.025 ; AFC spool flow K",
                                                  "G1 X1 E1"])

    def test_adaptive_pressure_advance_stays(self):
        body = "T1\nSET_PRESSURE_ADVANCE ADVANCE=0.04\n"
        out = g.transform(body + config_block(adaptive="0,1,0"), g.Plan(flow_k={1: 0.02})).text
        self.assertTrue(out.startswith(body))


def snapshot():
    def lane(name, tool, extruder, k=-1.0):
        return Lane(name=name, maps=["T%d" % tool], extruder_index=extruder, flow_k=k)
    return Snapshot(present=True, lanes=[lane("lane1", 0, 0, 0.03), lane("lane2", 1, 0), lane("lane5", 4, 1, 0.05)])


class PlanTest(unittest.TestCase):
    def test_from_afc(self):
        plan = g.plan_for(snapshot(), 3)
        self.assertEqual(plan.tools, {})
        self.assertEqual(plan.toolheads, {0: 0, 1: 0})  # nothing on T2
        self.assertEqual(plan.flow_k, {0: 0.03})

    def test_mapped_in_the_print_dialog(self):
        plan = g.plan_for(snapshot(), 3, {0: 4, 1: 1, 2: 0})
        self.assertEqual(plan.tools, {0: 4, 2: 0})
        self.assertEqual(plan.toolheads, {0: 1, 1: 0, 2: 0})
        self.assertEqual(plan.flow_k, {0: 0.05, 2: 0.03})

    def test_without_afc(self):
        one_nozzle = g.FileSettings(nozzles=1)
        self.assertEqual(g.plan_for(None, 2, None, one_nozzle).toolheads, {0: 0, 1: 0})
        self.assertEqual(g.plan_for(None, 2, None, g.FileSettings(nozzles=2)).toolheads, {})
        self.assertEqual(g.plan_for(Snapshot(), 2, None, one_nozzle).toolheads, {0: 0, 1: 0})



class HeaderTest(unittest.TestCase):
    def test_after_the_header_and_thumbnails(self):
        text = "; HEADER_BLOCK_START\n; x\n; HEADER_BLOCK_END\n\n; THUMBNAIL_BLOCK_START\n; THUMBNAIL_BLOCK_END\nG28\n"
        out = g.insert_after_header(text, "M117 hi")
        self.assertEqual(out.splitlines()[6:8], ["M117 hi", "G28"])

    def test_windows_newlines_and_comment_only_files(self):
        self.assertEqual(g.insert_after_header("; a\r\nG28\r\n", "M1"), "; a\r\nM1\r\nG28\r\n")
        self.assertEqual(g.insert_after_header("; only", "M1"), "; only\nM1\n")


# A U1 with "Support air filtration" on; filaments 0-2 soften at 45, 70 and 110 °C.
U1 = ("; printer_model = Snapmaker U1\n; support_air_filtration = 1\n"
      "; temperature_vitrification = 45,70,110\n")
STRONG = "SET_PURIFIER_MODE MODE=1 DESIRE_TEMP=42 ALARM_TEMP=48 FAN_SPEED=0.6 DELAY_OFF=0"
LIGHT = "SET_PURIFIER_MODE MODE=1 DESIRE_TEMP=45 ALARM_TEMP=51 FAN_SPEED=0.6 DELAY_OFF=0"
WARM = "SET_PURIFIER_MODE MODE=3 DESIRE_TEMP=45 FAN_SPEED=0.6 DELAY_OFF=600"


def u1_file(used, extra=U1, start="PRINT_START TOOL=0\n"):
    lengths = ", ".join("%.2f" % (100.0 if i in used else 0.0) for i in range(3))
    return (start + "G28 X Y\nT%d\nG1 X10 E1\n" % used[0]
            + "; filament used [mm] = %s\n" % lengths + config_block(extra=extra))


class TopCoverTest(unittest.TestCase):
    def run_on(self, text):
        return g.transform(text, g.Plan(), g.FileSettings.from_config(g.read_config_block(text)))

    def test_by_the_softest_filament_printed(self):
        for used, line, mode in (([0], STRONG, "strong cooling"), ([1], LIGHT, "light cooling"),
                                 ([2], WARM, "keep warm"), ([2, 0], STRONG, "strong cooling")):
            result = self.run_on(u1_file(used))
            self.assertEqual(result.text.splitlines()[:2],
                             ["PRINT_START TOOL=0", line + " ; U1 top cover: " + mode], used)
            self.assertEqual(result.top_cover, mode)

    def test_filaments_the_plate_does_not_print_do_not_count(self):
        # Plate 3 on printer 3: one ASA and six unused PLA slots.
        self.assertEqual(self.run_on(u1_file([2])).top_cover, "keep warm")

    def test_tool_changes_without_a_length_line(self):
        text = u1_file([1]).replace("; filament used [mm] = 0.00, 100.00, 0.00\n", "")
        self.assertEqual(self.run_on(text).top_cover, "light cooling")

    def test_off_without_air_filtration(self):
        text = u1_file([0], extra=U1.replace("support_air_filtration = 1", "support_air_filtration = 0"))
        result = self.run_on(text)
        self.assertEqual(result.text, text)
        self.assertEqual(result.top_cover, "")

    def test_only_on_a_u1(self):
        text = u1_file([0], extra=U1.replace("Snapmaker U1", "Voron 2.4 300"))
        self.assertEqual(self.run_on(text).text, text)

    def test_start_gcode_that_sets_it_wins(self):
        text = u1_file([0], start="PRINT_START TOOL=0\nSET_PURIFIER_MODE MODE=3 DESIRE_TEMP=50\n")
        result = self.run_on(text)
        self.assertEqual(result.text, text)
        self.assertNotIn("top cover", result.summary())

    def test_a_second_pass_keeps_one_line(self):
        once = self.run_on(u1_file([0])).text
        self.assertEqual(self.run_on(once).text, once)

    def test_without_print_start_before_the_first_command(self):
        text = "; HEADER_BLOCK_START\n; HEADER_BLOCK_END\n" + u1_file([0], start="")
        self.assertEqual(self.run_on(text).text.splitlines()[2:4],
                         [STRONG + " ; U1 top cover: strong cooling", "G28 X Y"])

    def test_keeps_windows_line_ends(self):
        text = u1_file([0]).replace("\n", "\r\n")
        self.assertIn("PRINT_START TOOL=0\r\n" + STRONG + " ; U1 top cover: strong cooling\r\nG28",
                      self.run_on(text).text)

    def test_summary(self):
        self.assertTrue(self.run_on(u1_file([0])).summary().endswith(", U1 top cover: strong cooling"))

    def test_keep_warm_aims_for_the_chamber_temperature(self):
        chamber = ("; support_chamber_temp_control = 1\n; activate_chamber_temp_control = 0,1,1\n"
                   "; chamber_temperature = 0,40,55\n")
        cover = self.run_on(u1_file([2], extra=U1 + chamber)).text.splitlines()[1]
        self.assertEqual(cover, WARM.replace("DESIRE_TEMP=45", "DESIRE_TEMP=55") + " ; U1 top cover: keep warm")

    def test_chamber_temperature_needs_the_printer_setting(self):
        chamber = "; activate_chamber_temp_control = 0,0,1\n; chamber_temperature = 0,0,55\n"
        cover = self.run_on(u1_file([2], extra=U1 + chamber)).text.splitlines()[1]
        self.assertEqual(cover, WARM + " ; U1 top cover: keep warm")

    def test_chamber_temperature_of_a_filament_not_printed(self):
        chamber = ("; support_chamber_temp_control = 1\n; activate_chamber_temp_control = 1,1,0\n"
                   "; chamber_temperature = 60,60,0\n")
        cover = self.run_on(u1_file([2], extra=U1 + chamber)).text.splitlines()[1]
        self.assertEqual(cover, WARM + " ; U1 top cover: keep warm")


if __name__ == "__main__":
    unittest.main()
