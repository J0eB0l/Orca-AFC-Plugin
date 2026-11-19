"""The BridgeBox on the Device tab's Update page in the AFC build of
OrcaSlicer: each BridgeBox AFC's Bambu AMS units are on, those units, and the
BridgeBox displays that left their address with the printer, each with its
firmware, the newest one out, and an Update that writes the newest.

- A BridgeBox runs the firmware its units report (bridge_fw). The newest is
  the signed image for its chip (bridge_chip) on the BridgeBox branch. Update
  puts the image and its manifest in the printer's config folder and has
  AFC_BAMBU_FLASH send them to the bridge, which writes them and restarts.
- An AMS unit's firmware and the images it can take come from AFC's AMS
  updater (AFC_BambuAMS_firmware). Update runs AFC_BAMBU_FIRMWARE with
  CONFIRM=1, which writes the newest while Klipper keeps running, or finishes
  an update that stopped.
- A display leaves its address and version in Moonraker's database
  (DISPLAY_NAMESPACE, one item per display, keyed by its MAC address). The
  newest is the display image on the BridgeBox branch, whose app header holds
  its version. Update sends that image to the display's own update server.
- A USB BoxTurtle reports the firmware its board runs (turtle.firmware).
  The newest is the image on the BoxTurtle firmware's branch, whose manifest
  names its version and protocol. Update puts the image and its manifest in
  the printer's config folder and has AFC_TURTLE_FLASH write them over USB.

Nothing updates while the printer prints, nor while another update runs."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import struct
import urllib.error
import urllib.parse
import urllib.request

FIRMWARE = "AFC_BambuAMS_firmware"  # AFC's AMS updater, in Klipper
FIRMWARE_FIELDS = ["updater", "running", "waiting", "result", "units"]
UNIT_FIELDS = ["ams_model", "fw_version", "bridge_fw", "bridge_port", "bridge_wifi", "bridge_link", "bridge_chip",
               "bridge_connected"]
TURTLE_TYPE = "BOXTURTLEUSB"  # a USB BoxTurtle's unit type, as bambu_units() compares types
TURTLE_FIELDS = ["turtle"]
DISPLAY_NAMESPACE = "bridgebox_display"
EVERY = 30.0             # seconds between reads while no update runs; each push while one does
DISPLAY_STALE = 1800.0   # seconds a display's record may stay as it was: it rewrites it every 10 minutes
NEWEST_EVERY = 6 * 3600.0  # seconds between reads of the newest firmware on GitHub
NEWEST_RETRY = 600.0     # ... and before a read that found nothing is tried again
FIRMWARES = "https://raw.githubusercontent.com/J0eB0l/AFC-Klipper-Add-On/BridgeBox/firmwares"
BRIDGE_IMAGES = {"ESP32S3": FIRMWARES + "/BridgeBox/signed_s3.bin",  # chip -> its signed image
                 "ESP32C3": FIRMWARES + "/BridgeBox/signed_c3.bin"}
DISPLAY_IMAGE = FIRMWARES + "/BridgeBox_Display/bridgebox-update-app-0x10000.bin"
# The BoxTurtle firmware's own branch, until it joins the BridgeBox branch.
TURTLE_IMAGE = ("https://raw.githubusercontent.com/J0eB0l/AFC-Klipper-Add-On/BridgeBox-turtle/firmwares"
                "/BoxTurtleUSB/boxturtle.bin")
BRIDGE_FOLDER = "bridgebox"  # in the printer's config folder, beside printer.cfg
TURTLE_FOLDER = "boxturtle"  # ... where AFC_TURTLE_FLASH looks first
FLASH_TIMEOUT = 600.0    # seconds AFC_BAMBU_FLASH may take: the whole image goes over the bridge link
TURTLE_TIMEOUT = 300.0   # ... and AFC_TURTLE_FLASH: bootloader, write, check, restart
CONTACT_TIMEOUT = 4.0    # seconds to wait for a display that is asked for its state
SEND_TIMEOUT = 120.0     # ... and for each part of the image it is sent

_ESP_MAGIC = 0xE9
_APP_MAGIC = 0xABCD5432
_CHIP_IDS = {"ESP32S3": 9, "ESP32C3": 5}  # chip -> the chip id in an ESP image header
_DISPLAY_PROJECT = "bridgebox_display"
_HEAD = 512              # an app image's header and description
_CHUNK = 16 * 1024
_HOST = re.compile(r"[A-Za-z0-9.-]{1,253}")
_PRINTING = ("printing", "paused")


# ------------------------------------------------------------------ versions

def _key(version: str):
    """A version's numbers, so versions compare in release order: AFC-2.93 is
    (2, 93) and 00.00.07.10 is (0, 0, 7, 10); None for none."""
    numbers = re.findall(r"\d+", version or "")
    return tuple(int(n) for n in numbers) if numbers else None


def newer(installed: str, latest: str) -> bool:
    """Whether `latest` is newer than the version installed."""
    mine, theirs = _key(installed), _key(latest)
    return mine is not None and theirs is not None and theirs > mine


def bridge_version(ver) -> str:
    """A bridge manifest's ver as the bridge reports its firmware: 290 is AFC-2.90."""
    return "AFC-%d.%02d" % divmod(int(ver), 100)


def app_version(head: bytes, project: str = _DISPLAY_PROJECT) -> str:
    """The version in an ESP32-S3 app image's description, "" when `head` is
    not the start of one built as `project`."""
    if len(head) < 112 or head[0] != _ESP_MAGIC or struct.unpack_from("<H", head, 12)[0] != _CHIP_IDS["ESP32S3"]:
        return ""
    if struct.unpack_from("<I", head, 32)[0] != _APP_MAGIC:
        return ""
    if head[80:112].split(b"\0", 1)[0].decode("ascii", "replace") != project:
        return ""
    return head[48:80].split(b"\0", 1)[0].decode("ascii", "replace")


def _get(url: str, opener, timeout: float, size: int = 0) -> bytes:
    """A file on GitHub, or its first `size` bytes."""
    headers = {"Range": "bytes=0-%d" % (size - 1)} if size else {}
    request = urllib.request.Request(url, headers=headers)
    with opener(request, timeout=timeout, context=ssl.create_default_context()) as answer:
        return answer.read(size) if size else answer.read()


def turtle_manifest(raw: bytes) -> dict:
    """A BoxTurtle image's manifest, checked to be one. ValueError when not."""
    manifest = json.loads(raw.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") != "BTFW1" \
            or not isinstance(manifest.get("version"), str) or not manifest["version"]:
        raise ValueError("the manifest on GitHub is not a BoxTurtle firmware manifest")
    return manifest


def read_newest(opener=urllib.request.urlopen, timeout: float = 15.0) -> dict:
    """The newest firmware on GitHub: {"bridge": {chip: version}, "display":
    version, "turtle": {"version", "proto"}}, with what GitHub did not answer
    for left out."""
    out = {"bridge": {}, "display": "", "turtle": {}}
    for chip, url in BRIDGE_IMAGES.items():
        try:
            manifest = json.loads(_get(url + ".manifest.json", opener, timeout).decode("utf-8"))
            out["bridge"][chip] = bridge_version(manifest["ver"])
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            continue
    try:
        out["display"] = app_version(_get(DISPLAY_IMAGE, opener, timeout, _HEAD))
    except (urllib.error.URLError, OSError, ValueError):
        pass
    try:
        manifest = turtle_manifest(_get(TURTLE_IMAGE + ".json", opener, timeout))
        out["turtle"] = {"version": manifest["version"], "proto": str(manifest.get("proto") or "")}
    except (urllib.error.URLError, OSError, ValueError):
        pass
    return out


# ------------------------------------------------------------------ what the printer has

def query(snapshot) -> dict:
    """The Klipper objects to read for the page: AFC's AMS updater and each
    Bambu unit, and each USB BoxTurtle; {} for a printer with none."""
    units = bambu_units(snapshot)
    out = {FIRMWARE: list(FIRMWARE_FIELDS)} if units else {}
    for unit in units:
        out["AFC_%s %s" % (unit.type, unit.name)] = list(UNIT_FIELDS)
    for unit in turtle_units(snapshot):
        out["AFC_%s %s" % (unit.type, unit.name)] = list(TURTLE_FIELDS)
    return out


def bambu_units(snapshot) -> list:
    return [u for u in getattr(snapshot, "units", None) or [] if u.type.upper().startswith("BAMBU")]


def turtle_units(snapshot) -> list:
    return [u for u in getattr(snapshot, "units", None) or [] if u.type.upper() == TURTLE_TYPE]


def display_url(value) -> str:
    """http://host[:port] from a display's record, "" for anything else."""
    url = urllib.parse.urlsplit(str(value or "").strip())
    try:
        port = url.port
    except ValueError:
        return ""
    if url.scheme != "http" or not _HOST.fullmatch(url.hostname or "") or url.path not in ("", "/") \
            or url.query or url.fragment or url.username or url.password:
        return ""
    return "http://%s%s" % (url.hostname, ":%d" % port if port else "")


def displays(items, seen: dict, now: float) -> list:
    """The displays in the database namespace's items: {"key", "url",
    "version"} of each whose record changed within DISPLAY_STALE. `seen` keeps
    each record and when it last changed, from one read to the next: a
    display rewrites its record every 10 minutes, so one that stops is gone."""
    out = []
    items = items.get("value") if isinstance(items, dict) and isinstance(items.get("value"), dict) else items
    for key, value in sorted(items.items()) if isinstance(items, dict) else ():
        url = display_url(value.get("url")) if isinstance(value, dict) else ""
        if not url:
            continue
        record = json.dumps(value, sort_keys=True)
        known = seen.get(key)
        if known is None or known[0] != record:
            seen[key] = (record, now)
        elif now - known[1] > DISPLAY_STALE:
            continue
        version = str(value.get("version") or "") or str(value.get("panel") or "").split(" on ", 1)[0]
        out.append({"key": str(key), "url": url, "version": version})
    return out


def rows(status: dict, snapshot, found: list, newest: dict, jobs: dict, print_state: str) -> list:
    """The page's rows: each BridgeBox, the AMS units on it, the displays,
    then each USB BoxTurtle. `status` is the objects query of query(), `found` displays(),
    `newest` read_newest(), `jobs` the updates the agent runs (row id ->
    {"state", "message", "progress"}) and `print_state` print_stats' state.

    Each row is {"id", "kind" (bridge, ams, display or turtle), "icon", "title",
    "model", "link", "units", "address", "version", "latest", "newer",
    "updatable" (Orca can update it), "ready" (... now), "resume" (Update
    finishes an update that stopped), "state" ("", running, done or failed),
    "message", "progress" (percent, -1 for none)}, and what the update needs
    under "do", which is not sent."""
    status = status if isinstance(status, dict) else {}
    newest = newest if isinstance(newest, dict) else {}

    def obj(name):
        value = status.get(name)
        return value if isinstance(value, dict) else {}

    firmware = obj(FIRMWARE)
    listed = firmware.get("units") if isinstance(firmware.get("units"), dict) else {}
    result = firmware.get("result") if isinstance(firmware.get("result"), dict) else {}
    running = bool(firmware.get("running"))
    waiting = str(firmware.get("waiting") or "")
    bridges, units = {}, []
    for unit in bambu_units(snapshot):
        own = obj("AFC_%s %s" % (unit.type, unit.name))
        lite = str(own.get("ams_model") or unit.model or "").lower() == "lite"
        info = listed.get(unit.name)
        if not isinstance(info, dict) and not lite:
            continue  # a pool's spare section, which holds no unit until one is claimed
        port = str(own.get("bridge_port") or "")
        if port:
            bridge = bridges.setdefault(port, {"units": [], "fw": "", "chip": "", "wifi": None, "link": "",
                                               "connected": False})
            bridge["units"].append(unit.name)
            bridge["fw"] = bridge["fw"] or str(own.get("bridge_fw") or "")
            bridge["chip"] = bridge["chip"] or str(own.get("bridge_chip") or "")
            bridge["link"] = bridge["link"] or str(own.get("bridge_link") or "")
            if own.get("bridge_wifi") is not None:
                bridge["wifi"] = bool(own.get("bridge_wifi"))
            bridge["connected"] = bridge["connected"] or bool(own.get("bridge_connected"))
        units.append((unit, own, info if isinstance(info, dict) else None, port))

    out = []
    for number, (port, bridge) in enumerate(bridges.items(), 1):
        chip = bridge["chip"].upper()
        latest = str((newest.get("bridge") or {}).get(chip) or "")
        title = "BridgeBox" if len(bridges) == 1 else "BridgeBox %d" % number
        row = _row("bridge:" + port, "bridge", "bridgebox", title)
        row.update(link=_link(port, bridge["wifi"], bridge["link"]),
                   units=", ".join(bridge["units"]), version=bridge["fw"], latest=latest,
                   updatable=bool(latest and chip in BRIDGE_IMAGES and bridge["connected"]),
                   do={"unit": bridge["units"][0], "chip": chip, "version": latest})
        if not bridge["connected"]:
            row["message"] = "Not connected to the printer"
        out.append(row)
        for unit, own, info, unit_port in units:
            if unit_port == port:
                out.append(_ams_row(unit, own, info, firmware, result, running, waiting))
                # The update goes over the bridge link.
                out[-1]["do"]["offline"] = not bridge["connected"]
    out += [_ams_row(unit, own, info, firmware, result, running, waiting)
            for unit, own, info, unit_port in units if not unit_port]
    for display in found or ():
        latest = str(newest.get("display") or "")
        row = _row("display:" + display["key"], "display", "display", "BridgeBox display")
        row.update(address=urllib.parse.urlsplit(display["url"]).netloc, version=display["version"], latest=latest,
                   updatable=bool(latest), do={"url": display["url"], "version": latest})
        out.append(row)
    for unit in turtle_units(snapshot):
        out.append(_turtle_row(unit, obj("AFC_%s %s" % (unit.type, unit.name)).get("turtle"),
                               newest.get("turtle")))

    busy = running or any(job.get("state") == "running" for job in (jobs or {}).values()) \
        or any(row["state"] == "running" for row in out)
    idle = str(print_state or "").lower() not in _PRINTING
    for row in out:
        job = (jobs or {}).get(row["id"])
        if job:
            arrived = job.get("arrived") or ()
            message = arrived[1] if arrived and row["version"] == arrived[0] else job.get("message", "")
            row.update(state=job.get("state", ""), message=message, progress=job.get("progress", -1))
            live = row["do"].get("live")
            if job.get("state") == "running" and live:
                # The printer's own words on the part of the update it runs.
                row.update(message=live["message"], progress=live["progress"])
        row["newer"] = newer(row["version"], row["latest"])
        row["ready"] = (row["updatable"] and idle and not busy and not row["do"].get("offline")
                        and (row["newer"] or row["resume"]))
    return out


_LINKS = {"wifi": "WiFi", "usb": "USB"}  # bridge_link -> what the page shows


def _link(port: str, wifi, link: str) -> str:
    """How a bridge reaches the printer: the link AFC says it uses now
    (bridge_link), else as far as the rest of its units' status tells: WiFi
    at a tcp: address, USB when its firmware has no WiFi, "" otherwise.
    bridge_wifi says whether the firmware has WiFi, not whether the bridge
    uses it, and with connection: wifi a bridge keeps its USB device as its
    port while it talks over WiFi. AFC builds before bridge_link send none."""
    if link.lower() in _LINKS:
        return _LINKS[link.lower()]
    if port.startswith("tcp:"):
        return "WiFi"
    return "USB" if wifi is False else ""


def _row(row_id: str, kind: str, icon: str, title: str) -> dict:
    return {"id": row_id, "kind": kind, "icon": icon, "title": title, "model": "", "link": "", "units": "",
            "address": "", "version": "", "latest": "", "newer": False, "updatable": False, "ready": False,
            "resume": False, "state": "", "message": "", "progress": -1, "do": {}}


def _turtle_row(unit, turtle, newest) -> dict:
    """A USB BoxTurtle's row: the firmware its board runs and the newest on
    GitHub, which it can take when it speaks the protocol of the AFC on the
    printer. AFC's own report on an update shows while it runs or after it
    failed; a board left in its bootloader takes Update to finish."""
    turtle = turtle if isinstance(turtle, dict) else {}
    newest = newest if isinstance(newest, dict) else {}
    row = _row("turtle:" + unit.name, "turtle", "boxturtle", unit.name)
    port = str(turtle.get("port") or "")
    connected = bool(turtle.get("connected"))
    usb = port.startswith("/dev/")
    latest = str(newest.get("version") or "")
    fits = not turtle.get("proto") or newest.get("proto") == turtle.get("proto")
    row.update(model="BoxTurtle", link="BridgeHub" if port.startswith("tcp:") else "USB" if usb else "",
               version=str(turtle.get("firmware") or "") if connected else "", latest=latest,
               resume=not connected and bool(turtle.get("bootloader")),
               do={"unit": unit.name, "version": latest})
    row["updatable"] = bool(latest and fits and turtle.get("flash_tool") and (connected and usb or row["resume"]))
    if not connected and not row["resume"]:
        row["message"] = "Not connected to the printer"
    elif connected and not usb:
        row["message"] = "Its firmware is updated over USB"
    elif latest and not fits:
        row["message"] = "The newest firmware needs a newer AFC; update AFC first"
    elif latest and not turtle.get("flash_tool"):
        row["message"] = "Katapult's flashtool is not installed on the printer (~/katapult)"
    elif row["resume"]:
        row["state"], row["message"] = "failed", "The BoxTurtle is waiting in its bootloader; Update finishes it"
    flash = turtle.get("flash") if isinstance(turtle.get("flash"), dict) else {}
    if flash.get("state") in ("running", "failed") and flash.get("message"):
        progress = flash.get("progress")
        live = {"message": str(flash["message"]), "progress": progress if isinstance(progress, int) else -1}
        if flash["state"] == "running":
            row["do"]["live"] = live
        row.update(state=flash["state"], **live)
    return row


def _ams_row(unit, own: dict, info, firmware: dict, result: dict, running: bool, waiting: str) -> dict:
    """An AMS unit's row: its firmware and the newest image AFC's AMS updater
    has for it, and how the updater's last run on it went."""
    lite = info is None
    model = "AMS Lite" if lite else str(info.get("model") or "AMS")
    row = _row("ams:" + unit.name, "ams", "ams_lite" if lite else "ams_ht" if "HT" in model else "ams", unit.name)
    choices = [c for c in (info or {}).get("choices") or () if isinstance(c, dict) and c.get("version")]
    newest = choices[0] if choices else {}
    row.update(model=model, version=str((info or {}).get("version") or own.get("fw_version") or ""),
               latest=str(newest.get("version") or ""), resume=waiting == unit.name)
    row["updatable"] = not lite and bool(firmware.get("updater")) and bool(row["latest"] or row["resume"])
    row["do"] = {"version": row["latest"], "file": str(newest.get("file") or "")}
    if not lite and not firmware.get("updater"):
        row["message"] = "AFC's AMS updater is not set up on this printer"
    if str(result.get("unit") or "") == unit.name and str(result.get("message") or ""):
        row["state"] = "running" if running else "done" if result.get("ok") else "failed"
        row["message"] = str(result["message"])
    if row["resume"]:
        row["state"], row["message"] = "failed", "An update stopped before it was done; Update finishes it"
    return row


def public(found: list) -> list:
    """The rows as the push sends them, without what only the agent uses."""
    return [{k: v for k, v in row.items() if k != "do"} for row in found]


def ams_gcode(row: dict) -> str:
    """The G-code that updates an AMS unit's row to its newest image, or
    finishes its stopped update."""
    unit = row["id"].split(":", 1)[1]
    if row["resume"]:
        return "AFC_BAMBU_FIRMWARE UNIT=%s RESUME=1" % unit
    script = "AFC_BAMBU_FIRMWARE UNIT=%s VERSION=%s" % (unit, row["do"]["version"])
    if row["do"].get("file"):
        script += " FILE=%s" % row["do"]["file"]
    return script + " CONFIRM=1"


# ------------------------------------------------------------------ updates

def reason(error) -> str:
    """Why a download or a request to a display failed, for its row."""
    return str(getattr(error, "reason", None) or error)


def bridge_files(chip: str, opener=urllib.request.urlopen, timeout: float = 60.0) -> tuple:
    """The signed image for a bridge chip and its manifest, from GitHub, as
    (image, manifest bytes, version). ValueError when they do not belong
    together or the image is not for that chip."""
    url = BRIDGE_IMAGES[chip]
    manifest_raw = _get(url + ".manifest.json", opener, timeout)
    image = _get(url, opener, timeout)
    manifest = json.loads(manifest_raw.decode("utf-8"))
    if not isinstance(manifest, dict) or manifest.get("format") not in ("BBFW1", "BBFW2"):
        raise ValueError("the manifest on GitHub is not a BridgeBox manifest")
    if len(image) < 24 or image[0] != _ESP_MAGIC or struct.unpack_from("<H", image, 12)[0] != _CHIP_IDS[chip]:
        raise ValueError("the image on GitHub is not a BridgeBox image")
    if manifest.get("len") != len(image) or manifest.get("sha512") != hashlib.sha512(image).hexdigest():
        raise ValueError("the image on GitHub does not match its manifest; try again later")
    return image, manifest_raw, bridge_version(manifest["ver"])


def turtle_files(opener=urllib.request.urlopen, timeout: float = 60.0) -> tuple:
    """The BoxTurtle image and its manifest from GitHub, as (image, manifest
    bytes, version). ValueError when they do not belong together."""
    manifest_raw = _get(TURTLE_IMAGE + ".json", opener, timeout)
    image = _get(TURTLE_IMAGE, opener, timeout)
    manifest = turtle_manifest(manifest_raw)
    if manifest.get("len") != len(image) or manifest.get("sha256") != hashlib.sha256(image).hexdigest():
        raise ValueError("the BoxTurtle image on GitHub does not match its manifest; try again later")
    return image, manifest_raw, manifest["version"]


def display_image(opener=urllib.request.urlopen, timeout: float = 60.0) -> tuple:
    """The display image from GitHub and its version. ValueError when it is
    not a BridgeBox display app image."""
    image = _get(DISPLAY_IMAGE, opener, timeout)
    version = app_version(image[:_HEAD])
    if not version or len(image) < 64 * 1024:
        raise ValueError("the display image on GitHub is not a BridgeBox display image")
    return image, version


def contact(url: str, opener=urllib.request.urlopen, timeout: float = CONTACT_TIMEOUT) -> str:
    """Ask a display for its state: the version it runs. Raises OSError or
    ValueError when it does not answer as a display does."""
    with opener(urllib.request.Request(url + "/state"), timeout=timeout) as answer:
        state = json.loads(answer.read(4096).decode("utf-8"))
    if not isinstance(state, dict) or not isinstance(state.get("panel"), str):
        raise ValueError("%s did not answer as a BridgeBox display" % url)
    return state["panel"].split(" on ", 1)[0]


def send_display(url: str, image: bytes, progress=None, opener=urllib.request.urlopen,
                 timeout: float = SEND_TIMEOUT) -> None:
    """Send an app image to a display's update server, which writes it and
    restarts on it; `progress(percent)` is called as it goes out. Raises
    OSError or ValueError with the display's reason when it is refused."""
    def body():
        for start in range(0, len(image), _CHUNK):
            if progress is not None:
                progress(start * 100 // len(image))
            yield image[start:start + _CHUNK]

    request = urllib.request.Request(url + "/ota", data=body(), method="POST",
                                     headers={"Content-Type": "application/octet-stream",
                                              "Content-Length": str(len(image))})
    try:
        with opener(request, timeout=timeout) as answer:
            text = answer.read(512).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as error:
        reason = error.read(512).decode("utf-8", "replace").strip() if error.fp else ""
        raise ValueError(reason or "the display answered %d" % error.code) from None
    if text != "ok":
        raise ValueError(text or "the display did not confirm the update")
    if progress is not None:
        progress(100)
