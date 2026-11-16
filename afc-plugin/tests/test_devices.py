"""The BridgeBox rows of the Update page: the firmware each part runs, the
newest on GitHub, and the steps of an update (devices.py)."""

import hashlib
import io
import json
import struct
import unittest
import urllib.error

import fake_orca  # noqa: F401  (puts afc-plugin/ on sys.path)
from orca_afc import devices
from orca_afc.protocol import Snapshot, Unit

BRIDGE = devices.BRIDGE_IMAGES["ESP32S3"]
PORT = "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit-if00"


def app_image(version="AFC-1.61", project="bridgebox_display", chip=9, size=70000):
    """An ESP32 app image whose description holds `version` and `project`."""
    image = bytearray(size)
    image[0] = 0xE9
    struct.pack_into("<H", image, 12, chip)
    struct.pack_into("<I", image, 32, 0xABCD5432)
    image[48:48 + len(version)] = version.encode()
    image[80:80 + len(project)] = project.encode()
    return bytes(image)


def bridge_image(ver=294, chip=9):
    """A signed bridge image and its manifest."""
    image = bytearray(4096)
    image[0] = 0xE9
    struct.pack_into("<H", image, 12, chip)
    image = bytes(image)
    manifest = {"format": "BBFW1", "ver": ver, "len": len(image), "sha512": hashlib.sha512(image).hexdigest(),
                "sig": "00", "pub": "00"}
    return image, json.dumps(manifest).encode()


class Web:
    """urlopen for GitHub and a display: files by URL, served with Range, and
    what a POST was sent."""

    def __init__(self, files=None, answer=b"ok"):
        self.files = dict(files or {})
        self.answer = answer
        self.asked = []
        self.sent = {}

    def __call__(self, request, timeout, context=None):
        self.asked.append((request.full_url, request.get_header("Range")))
        if request.get_method() == "POST":
            self.sent[request.full_url] = (b"".join(request.data), request.get_header("Content-length"))
            if isinstance(self.answer, Exception):
                raise self.answer
            return io.BytesIO(self.answer)
        data = self.files.get(request.full_url)
        if data is None:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
        span = request.get_header("Range")
        if span:
            first, last = (int(n) for n in span[len("bytes="):].split("-"))
            data = data[first:last + 1]
        return io.BytesIO(data)


def snapshot(*units):
    return Snapshot(present=True, units=[Unit(name, kind, lanes=["lane%d" % i]) for i, (name, kind) in
                                         enumerate(units, 1)])


def firmware(**changes):
    """AFC's AMS updater: an AMS 2 Pro with a newer image out, and an AMS HT
    up to date."""
    status = {"updater": True, "running": False, "waiting": "", "result": {"unit": "", "ok": False, "message": ""},
              "units": {"Bambu_AMS_1": {"version": "00.00.06.49", "model": "AMS 2 Pro",
                                        "choices": [{"version": "00.00.07.10", "file": "", "newest": True},
                                                    {"version": "00.00.06.49", "file": "", "newest": False}]},
                        "Bambu_HT_1": {"version": "00.00.01.10", "model": "AMS HT",
                                       "choices": [{"version": "00.00.01.10", "file": "", "newest": True}]}}}
    status.update(changes)
    return status


def unit_status(**changes):
    status = {"ams_model": "ams2", "fw_version": "00.00.06.49", "bridge_fw": "AFC-2.93", "bridge_port": PORT,
              "bridge_wifi": False, "bridge_chip": "ESP32S3", "bridge_connected": True}
    status.update(changes)
    return status


def printer_status(**units):
    status = {devices.FIRMWARE: firmware(), "AFC_BambuAMS Bambu_AMS_1": unit_status(),
              "AFC_BambuAMS Bambu_HT_1": unit_status(ams_model="ht", fw_version="00.00.01.10")}
    for name, value in units.items():
        status["AFC_BambuAMS " + name] = value
    return status


NEWEST = {"bridge": {"ESP32S3": "AFC-2.94"}, "display": "AFC-1.61"}
DISPLAY = [{"key": "a0b1c2d3e4f5", "url": "http://192.168.1.50", "version": "AFC-1.60"}]


def rows(status=None, found=DISPLAY, newest=NEWEST, jobs=None, state="standby", snap=None):
    snap = snap or snapshot(("Bambu_AMS_1", "BambuAMS"), ("Bambu_HT_1", "BambuAMS"), ("Turtle_1", "Box_Turtle"))
    return devices.rows(printer_status() if status is None else status, snap, found, newest, jobs or {}, state)


class VersionTest(unittest.TestCase):
    def test_newer(self):
        self.assertTrue(devices.newer("AFC-2.93", "AFC-2.94"))
        self.assertTrue(devices.newer("00.00.06.49", "00.00.07.10"))
        self.assertFalse(devices.newer("AFC-2.93", "AFC-2.90"))
        self.assertFalse(devices.newer("AFC-2.93", "AFC-2.93"))
        self.assertFalse(devices.newer("", "AFC-2.94"))
        self.assertFalse(devices.newer("AFC-2.93", ""))

    def test_a_manifest_version_as_the_bridge_reports_it(self):
        self.assertEqual(devices.bridge_version(290), "AFC-2.90")
        self.assertEqual(devices.bridge_version(305), "AFC-3.05")

    def test_an_app_images_version(self):
        self.assertEqual(devices.app_version(app_image("AFC-1.61")), "AFC-1.61")
        self.assertEqual(devices.app_version(app_image(project="bridgebox")), "")  # another app
        self.assertEqual(devices.app_version(app_image(chip=5)), "")  # an ESP32-C3 image
        self.assertEqual(devices.app_version(b"\0" * 512), "")


class NewestTest(unittest.TestCase):
    def test_bridge_and_display(self):
        image, manifest = bridge_image(294)
        web = Web({BRIDGE + ".manifest.json": manifest, devices.DISPLAY_IMAGE: app_image("AFC-1.61")})
        self.assertEqual(devices.read_newest(web), {"bridge": {"ESP32S3": "AFC-2.94"}, "display": "AFC-1.61"})
        # Only the head of the display image is read.
        self.assertIn((devices.DISPLAY_IMAGE, "bytes=0-511"), web.asked)

    def test_what_github_does_not_answer_is_left_out(self):
        self.assertEqual(devices.read_newest(Web()), {"bridge": {}, "display": ""})
        web = Web({BRIDGE + ".manifest.json": b"not json", devices.DISPLAY_IMAGE: b"\0" * 512})
        self.assertEqual(devices.read_newest(web), {"bridge": {}, "display": ""})


class QueryTest(unittest.TestCase):
    def test_the_updater_and_each_bambu_unit(self):
        query = devices.query(snapshot(("Bambu_AMS_1", "BambuAMS"), ("Turtle_1", "Box_Turtle")))
        self.assertEqual(sorted(query), ["AFC_BambuAMS Bambu_AMS_1", devices.FIRMWARE])
        self.assertIn("bridge_chip", query["AFC_BambuAMS Bambu_AMS_1"])

    def test_nothing_without_a_bambu_unit(self):
        self.assertEqual(devices.query(snapshot(("Turtle_1", "Box_Turtle"))), {})
        self.assertEqual(devices.query(None), {})


class DisplaysTest(unittest.TestCase):
    def test_a_displays_address_and_version(self):
        items = {"a0b1c2d3e4f5": {"url": "http://192.168.1.50", "panel": "AFC-1.60 on ota_0",
                                  "version": "AFC-1.60", "seq": 3}}
        self.assertEqual(devices.displays({"value": items}, {}, 100.0), DISPLAY)
        # The version can come from the panel line alone.
        del items["a0b1c2d3e4f5"]["version"]
        self.assertEqual(devices.displays(items, {}, 100.0), DISPLAY)

    def test_a_display_that_stopped_writing_its_record_is_gone(self):
        seen = {}
        items = {"a0b1c2d3e4f5": {"url": "http://192.168.1.50", "version": "AFC-1.60", "seq": 3}}
        self.assertEqual(len(devices.displays(items, seen, 100.0)), 1)
        self.assertEqual(len(devices.displays(items, seen, 100.0 + devices.DISPLAY_STALE)), 1)
        self.assertEqual(devices.displays(items, seen, 101.0 + devices.DISPLAY_STALE), [])
        items["a0b1c2d3e4f5"]["seq"] = 4
        self.assertEqual(len(devices.displays(items, seen, 102.0 + devices.DISPLAY_STALE)), 1)

    def test_only_a_plain_http_address(self):
        self.assertEqual(devices.display_url("http://192.168.1.50:8080/"), "http://192.168.1.50:8080")
        for url in ("https://192.168.1.50", "http://192.168.1.50/ota", "http://user@192.168.1.50",
                    "http://192.168.1.50?x=1", "file:///etc/passwd", "", None, 5):
            self.assertEqual(devices.display_url(url), "", url)
        self.assertEqual(devices.displays({"x": {"url": "ftp://x"}, "y": "nothing"}, {}, 1.0), [])


class RowsTest(unittest.TestCase):
    def test_each_bridgebox_with_its_units_then_the_displays(self):
        found = rows()
        self.assertEqual([(r["id"], r["kind"], r["icon"], r["title"]) for r in found],
                         [("bridge:" + PORT, "bridge", "bridgebox", "BridgeBox"),
                          ("ams:Bambu_AMS_1", "ams", "ams", "Bambu_AMS_1"),
                          ("ams:Bambu_HT_1", "ams", "ams_ht", "Bambu_HT_1"),
                          ("display:a0b1c2d3e4f5", "display", "display", "BridgeBox display")])
        bridge, ams, ht, display = found
        self.assertEqual((bridge["link"], bridge["units"], bridge["version"], bridge["latest"]),
                         ("USB", "Bambu_AMS_1, Bambu_HT_1", "AFC-2.93", "AFC-2.94"))
        self.assertTrue(bridge["newer"] and bridge["updatable"] and bridge["ready"])
        self.assertEqual((ams["model"], ams["version"], ams["latest"]), ("AMS 2 Pro", "00.00.06.49", "00.00.07.10"))
        self.assertTrue(ams["newer"] and ams["ready"])
        # Up to date: it can be updated, but there is nothing to update to.
        self.assertTrue(ht["updatable"])
        self.assertFalse(ht["newer"] or ht["ready"])
        self.assertEqual((display["address"], display["version"], display["latest"]),
                         ("192.168.1.50", "AFC-1.60", "AFC-1.61"))
        self.assertTrue(display["ready"])
        self.assertTrue(all(r["state"] == "" and r["progress"] == -1 for r in found))

    def test_a_bridge_that_does_not_say_its_chip_gets_no_newest(self):
        status = printer_status(Bambu_AMS_1=unit_status(bridge_chip=None),
                                Bambu_HT_1=unit_status(bridge_chip=None, ams_model="ht"))
        bridge = rows(status)[0]
        self.assertEqual(bridge["latest"], "")
        self.assertFalse(bridge["updatable"] or bridge["ready"])

    def test_two_bridgeboxes(self):
        status = printer_status(Bambu_HT_1=unit_status(ams_model="ht", bridge_port="tcp:192.168.1.20:8899",
                                                       bridge_wifi=True))
        found = rows(status, found=[])
        self.assertEqual([r["title"] for r in found], ["BridgeBox 1", "Bambu_AMS_1", "BridgeBox 2", "Bambu_HT_1"])
        self.assertEqual(found[2]["link"], "WiFi")

    def test_a_bridge_whose_firmware_has_wifi_on_a_serial_device(self):
        # It may talk over WiFi with its USB device still as its port, so the page does not say which.
        status = printer_status(Bambu_AMS_1=unit_status(bridge_wifi=True),
                                Bambu_HT_1=unit_status(bridge_wifi=True, ams_model="ht"))
        self.assertEqual(rows(status, found=[])[0]["link"], "")

    def test_the_link_afc_reports_wins(self):
        # With connection: wifi the port stays the USB device while the bridge talks over WiFi.
        for link, shown in (("wifi", "WiFi"), ("usb", "USB")):
            status = printer_status(Bambu_AMS_1=unit_status(bridge_wifi=True, bridge_link=link),
                                    Bambu_HT_1=unit_status(bridge_wifi=True, bridge_link=link, ams_model="ht"))
            self.assertEqual(rows(status, found=[])[0]["link"], shown)

    def test_no_link_while_the_bridge_port_is_closed(self):
        status = printer_status(Bambu_AMS_1=unit_status(bridge_link=None),
                                Bambu_HT_1=unit_status(bridge_link=None, ams_model="ht"))
        self.assertEqual(rows(status, found=[])[0]["link"], "USB")  # its firmware has no WiFi

    def test_a_bridgebox_not_connected(self):
        status = printer_status(Bambu_AMS_1=unit_status(bridge_connected=False),
                                Bambu_HT_1=unit_status(bridge_connected=False, ams_model="ht"))
        bridge, ams = rows(status, found=[])[:2]
        self.assertFalse(bridge["updatable"])
        self.assertEqual(bridge["message"], "Not connected to the printer")
        self.assertFalse(ams["ready"])  # the AMS update goes over the bridge link

    def test_nothing_updates_while_printing(self):
        self.assertFalse(any(r["ready"] for r in rows(state="printing")))
        self.assertFalse(any(r["ready"] for r in rows(state="paused")))
        self.assertTrue(any(r["ready"] for r in rows(state="complete")))

    def test_nothing_else_updates_while_an_update_runs(self):
        status = printer_status()
        status[devices.FIRMWARE] = firmware(running=True, result={"unit": "Bambu_HT_1", "ok": False,
                                                                  "message": "Updating Bambu_HT_1."})
        found = rows(status)
        self.assertFalse(any(r["ready"] for r in found))
        self.assertEqual((found[2]["state"], found[2]["message"]), ("running", "Updating Bambu_HT_1."))
        found = rows(jobs={"display:a0b1c2d3e4f5": {"state": "running", "message": "Sending it", "progress": 40}})
        self.assertFalse(any(r["ready"] for r in found))
        self.assertEqual((found[3]["state"], found[3]["message"], found[3]["progress"]), ("running", "Sending it", 40))

    def test_how_the_last_ams_update_ended(self):
        status = printer_status()
        status[devices.FIRMWARE] = firmware(result={"unit": "Bambu_AMS_1", "ok": True,
                                                    "message": "Bambu_AMS_1 now runs 00.00.07.10."})
        ams = rows(status)[1]
        self.assertEqual((ams["state"], ams["message"]), ("done", "Bambu_AMS_1 now runs 00.00.07.10."))
        status[devices.FIRMWARE]["result"]["ok"] = False
        self.assertEqual(rows(status)[1]["state"], "failed")

    def test_an_update_that_stopped_is_finished(self):
        status = printer_status()
        status[devices.FIRMWARE] = firmware(waiting="Bambu_HT_1")
        ht = rows(status)[2]
        self.assertTrue(ht["resume"] and ht["ready"])
        self.assertEqual(ht["state"], "failed")
        self.assertEqual(devices.ams_gcode(ht), "AFC_BAMBU_FIRMWARE UNIT=Bambu_HT_1 RESUME=1")

    def test_without_the_ams_updater(self):
        status = printer_status()
        status[devices.FIRMWARE] = firmware(updater=False)
        ams = rows(status)[1]
        self.assertFalse(ams["updatable"])
        self.assertEqual(ams["message"], "AFC's AMS updater is not set up on this printer")

    def test_an_ams_lite_shows_without_an_update(self):
        snap = snapshot(("Bambu_Lite_1", "BambuAMS"))
        status = {devices.FIRMWARE: firmware(units={}),
                  "AFC_BambuAMS Bambu_Lite_1": unit_status(ams_model="lite", fw_version="00.01.02.03")}
        bridge, lite = rows(status, found=[], snap=snap)
        self.assertEqual((lite["icon"], lite["model"], lite["version"]), ("ams_lite", "AMS Lite", "00.01.02.03"))
        self.assertFalse(lite["updatable"])

    def test_a_pool_spare_section_is_left_out(self):
        # A spare section holds no unit until one is claimed, so the AMS updater does not list it.
        snap = snapshot(("Bambu_AMS_1", "BambuAMS"), ("Bambu_Spare_1", "BambuAMS"))
        status = printer_status(Bambu_Spare_1=unit_status())
        self.assertEqual([r["title"] for r in rows(status, found=[], snap=snap)], ["BridgeBox", "Bambu_AMS_1"])

    def test_the_rows_as_sent(self):
        sent = devices.public(rows())
        self.assertTrue(all("do" not in row for row in sent))
        self.assertEqual(json.loads(json.dumps(sent)), sent)

    def test_the_ams_update_gcode(self):
        ams = rows()[1]
        self.assertEqual(devices.ams_gcode(ams), "AFC_BAMBU_FIRMWARE UNIT=Bambu_AMS_1 VERSION=00.00.07.10 CONFIRM=1")
        status = printer_status()
        status[devices.FIRMWARE]["units"]["Bambu_AMS_1"]["choices"][0]["file"] = "n3f_rev5_00.00.07.10.bin"
        self.assertEqual(devices.ams_gcode(rows(status)[1]),
                         "AFC_BAMBU_FIRMWARE UNIT=Bambu_AMS_1 VERSION=00.00.07.10 FILE=n3f_rev5_00.00.07.10.bin "
                         "CONFIRM=1")


class BridgeFilesTest(unittest.TestCase):
    def test_the_image_and_its_manifest(self):
        image, manifest = bridge_image(294)
        got = devices.bridge_files("ESP32S3", Web({BRIDGE: image, BRIDGE + ".manifest.json": manifest}))
        self.assertEqual(got, (image, manifest, "AFC-2.94"))

    def test_an_image_that_does_not_match_its_manifest(self):
        image, manifest = bridge_image(294)
        with self.assertRaisesRegex(ValueError, "does not match its manifest"):
            devices.bridge_files("ESP32S3", Web({BRIDGE: image[:-1] + b"x", BRIDGE + ".manifest.json": manifest}))

    def test_an_image_for_another_chip(self):
        image, manifest = bridge_image(294, chip=5)
        with self.assertRaisesRegex(ValueError, "not a BridgeBox image"):
            devices.bridge_files("ESP32S3", Web({BRIDGE: image, BRIDGE + ".manifest.json": manifest}))

    def test_not_a_manifest(self):
        image, _ = bridge_image(294)
        with self.assertRaisesRegex(ValueError, "not a BridgeBox manifest"):
            devices.bridge_files("ESP32S3", Web({BRIDGE: image, BRIDGE + ".manifest.json": b"[]"}))

    def test_github_not_answering(self):
        with self.assertRaises(OSError):
            devices.bridge_files("ESP32S3", Web())


class DisplayTest(unittest.TestCase):
    URL = "http://192.168.1.50"

    def test_the_display_image(self):
        image = app_image("AFC-1.61")
        self.assertEqual(devices.display_image(Web({devices.DISPLAY_IMAGE: image})), (image, "AFC-1.61"))
        with self.assertRaisesRegex(ValueError, "not a BridgeBox display image"):
            devices.display_image(Web({devices.DISPLAY_IMAGE: app_image(project="other")}))

    def test_contact(self):
        web = Web({self.URL + "/state": json.dumps({"panel": "AFC-1.60 on ota_0", "units": 2}).encode()})
        self.assertEqual(devices.contact(self.URL, web), "AFC-1.60")
        with self.assertRaises(ValueError):
            devices.contact(self.URL, Web({self.URL + "/state": b"{}"}))
        with self.assertRaises(OSError):
            devices.contact(self.URL, Web())

    def test_sending_the_image(self):
        image = app_image("AFC-1.61", size=40000)
        web, seen = Web(), []
        devices.send_display(self.URL, image, seen.append, web)
        self.assertEqual(web.sent[self.URL + "/ota"], (image, str(len(image))))
        self.assertEqual(seen[0], 0)
        self.assertEqual(seen[-1], 100)
        self.assertEqual(seen, sorted(seen))

    def test_a_display_that_refuses_it(self):
        refused = urllib.error.HTTPError(self.URL + "/ota", 500, "Error", {}, io.BytesIO(b"Image failed validation."))
        with self.assertRaisesRegex(ValueError, "Image failed validation"):
            devices.send_display(self.URL, app_image(), opener=Web(answer=refused))
        with self.assertRaisesRegex(ValueError, "did not confirm"):
            devices.send_display(self.URL, app_image(), opener=Web(answer=b""))


if __name__ == "__main__":
    unittest.main()
