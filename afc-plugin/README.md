# AFC plugin for OrcaSlicer 2.5

Adds an **AFC** tab to OrcaSlicer's top bar showing what
[AFC](https://github.com/ArmoredTurtle/AFC-Klipper-Add-On) reports from the
printer: every unit and lane, their spools, and the unit dryers. From the tab
you can:

- edit a lane's color, material, weight, temperatures, T number and Spoolman spool;
- load, unload and eject a lane (only while the printer is idle);
- start and stop drying on an ACE, ACE 2 Pro, AMS 2 Pro or AMS HT;
- see each spool's flow K, which AFC autocal saves in Spoolman.

It talks to Moonraker over HTTP.

The plugin also has an **AFC G-code** step that fits each exported or uploaded
Klipper file to AFC:

- a change between two lanes that feed one toolhead gets `PURGE_LENGTH=<mm>` on
  its `T` line, so AFC's poop purges the flushing volume from the project's
  flush volume table (the prime tower only primes when "single extruder
  multi-material" is off). The first lane a toolhead loads and a switch to a
  toolhead that already holds its filament get none. In the AFC build of
  OrcaSlicer, with "Purge in prime tower" and "Multi-material per toolhead" on,
  the prime tower flushes the lane changes of its own tool changes instead,
  from the toolhead each tray reported at filament sync, and those get no
  `PURGE_LENGTH` unless the print dialog's mapping changed them;
- a lane whose Spoolman spool has a saved flow K prints with it:
  `SET_PRESSURE_ADVANCE ADVANCE=<k>` after its `T` line, and the file's own PA
  lines for it take that K (filaments with adaptive pressure advance keep theirs).

It reads AFC from the printer for which toolhead each T number feeds and its
spool's K. A printer that does not answer still gets its file; on a single-nozzle
printer the purges are still there, the spool K is not. Other G-code flavors are
left as they are.

## Install

1. Get `orca_afc.whl` from the **AFC plugin** workflow's artifacts, or build it
   yourself with `python3 afc-plugin/build_wheel.py` (it lands in `afc-plugin/dist/`).
2. In OrcaSlicer, open **File > Plugins** and choose **Install plugin**, then pick
   `orca_afc.whl`.
3. Turn the AFC plugin on if it is not on already. OrcaSlicer asks once whether it may
   start a thread; allow it, since the tab reads the printer from that thread.

Installing a newer `orca_afc.whl` replaces the old one. Keep the file name as it
is: OrcaSlicer names the plugin after the file.

To turn on the G-code step, switch OrcaSlicer to Advanced mode, open the process
settings, **Others**, and pick **AFC G-code** under **Slicing Pipeline Plugin**.
It runs on **Export G-code** and on uploads to the printer (not on the preview).

## Printer agent

The plugin is also a printer agent, **AFC (Klipper)**, for OrcaSlicer's Device
tab, filament sync and print dialog. To use it:

1. In **Preferences**, **Developer**, turn on **Use printer agents instead of
   print hosts**.
2. In the printer settings (Advanced mode), **Basic information**, **Advanced**,
   set **Printer Agent** to **AFC (Klipper)**. The printer's connection settings
   still give its address and API key. The Device tab and the print dialog name
   the printer after this printer preset (Moonraker's host name when no preset
   prints to it).
3. If the printer was used before with another agent (Snapmaker, Moonraker) and
   the Device tab shows no printer after the switch, restart OrcaSlicer once:
   the printer moves to the AFC agent then. One that was on the Moonraker agent
   without an API key needs a step before the switch: with the Moonraker agent
   still selected, open the Device tab's printer list (click the printer's name
   at the top), hover over the printer and click the unlink icon at the end of
   its row. Then switch the agent and restart OrcaSlicer: until the restart, no
   agent can use the printer.

Then:

- The **Device** tab shows the printer, with AFC's lanes as its AMS trays: one
  tray per T number, four to a unit (the first unit holds T0 to T3). The AFC
  build of OrcaSlicer shows each AFC unit as its own AMS instead, four lanes to
  one, and a unit of one lane (a Bambu AMS HT) or each tool of a toolchanger as
  an AMS HT. A unit with a dryer (ACE, Bambu AMS 2 Pro or AMS HT) shows its
  humidity and temperature; clicking the humidity opens OrcaSlicer's drying
  popup, which starts and stops the unit's dryer (ACE_DRY or
  AFC_BAMBU_HEATER_START). The popup allows the AMS 2 Pro's 65 °C; a unit that
  dries cooler (an ACE's 55 °C) dries at its most. AFC units that would need
  more than four AMS beside AMS HT units (or eight AMS HT) are shown by T number.
  The AFC build also shows the chamber's temperature when Klipper has a
  heater_generic, temperature_sensor or temperature_fan named with chamber,
  cavity, enclosure or cabinet.
- The sync button next to **Filament** in the Prepare sidebar puts each loaded
  lane in the slot of its T number (slot 1 is T0) with its spool's own preset,
  named like the spool ("Polymaker PolyLite PLA"; "AFC PLA 210C bed 60C" for a
  lane with no Spoolman spool). When there is none, sync makes it as
  "<name> @<printer>", copied from the preset that fits the spool best, with the
  spool's temperatures, density, diameter and pressure advance. A preset sync
  made takes the spool's values again on each sync until you delete the first
  line of its notes. Each slot also keeps its spool's flow K (Spoolman
  `flow_k`), which the slice uses over the preset's. This needs the AFC build of
  OrcaSlicer; official OrcaSlicer 2.5 picks the best fitting existing preset, or
  the generic one of the material.
- On the Device tab, a tray's edit popup lists the printer's presets and sets
  the lane's color and material; the preset picked there is what sync uses
  until the lane gets another spool. In the AFC build of OrcaSlicer the popup
  also has the spool's Factor K, saved as its Spoolman `flow_k`. A lane
  without a Spoolman spool keeps its K in the plugin until OrcaSlicer closes,
  and sync and prints use it as they would a spool's. A tray's refresh button reads the spool's
  tag again with the command of the lane's unit (AFC_BAMBU_SCAN, ACE_RFID_RESCAN,
  AFC_OAMS_RFID_SCAN, AFC_BT_RFID_STAGE, VIVID_RFID_READ or the U1's
  FILAMENT_DT_UPDATE); a lane with no reader says so. **Load** and **Unload** run
  AFC's CHANGE_TOOL and TOOL_UNLOAD. The lamp switches the printer's lights:
  Klipper `led`, `neopixel`, `dotstar`, `pca9533`, `pca9632` and `output_pin`
  objects named with main, chamber, case, cabinet, enclosure, light or lamp
  (`[led main_led]`, `[output_pin caselight]`), full white on or off.
- The Device tab's camera plays the first enabled webcam Moonraker lists (from
  `moonraker.conf` or Fluidd's and Mainsail's settings): an MJPEG stream
  (`mjpegstreamer`, `mjpegstreamer-adaptive`), else the webcam's snapshot,
  refreshed several times a second (WebRTC cameras such as camera-streamer).
  A relative webcam address is opened on the printer's web server, its address
  without Moonraker's port 7125. A webcam named for the nozzle or tool alignment
  (nozzle, tool, align, calib, ktamv or offset in its name) plays only when no
  other is set up.
- **Print** opens OrcaSlicer's print dialog, where each filament gets a tray, so a
  lane. The agent renumbers the file's tool changes to those T numbers, adds the
  lane-change purges and spool flow K the AFC G-code step adds, uploads the file
  to Moonraker and starts it. On a Snapmaker U1, the dialog's bed leveling, flow
  calibration and timelapse switches go into the file. In the AFC build of
  OrcaSlicer the dialog also has **Upload**, which does the same without starting
  the print. **Send** in the menu next to **Print plate** uploads the file as
  sliced, with no tray choice.

Without the agent, OrcaSlicer's own Moonraker agent also syncs AFC's lanes by T
number, with generic presets only.

## Printer address

The tab and the G-code step use the selected printer's connection settings: **host type** Octo/Klipper
(or Moonraker), the **hostname or IP** and the **API key** if Moonraker needs one.
That is the same address OrcaSlicer uploads to, so a printer that already uploads
works as it is.

To point them somewhere else, set the config of the AFC tab or of AFC G-code in the
Plugins dialog (each has its own):

```json
{ "printer_url": "http://192.168.1.20:7125", "api_key": "" }
```

`printer_url` wins over the printer's connection settings when it is set.

## Development

```sh
python3 -m unittest discover -s afc-plugin/tests   # standard library only
python3 afc-plugin/build_wheel.py
```

The tests run the plugin against a stand-in for OrcaSlicer's `orca` module and a
fake Moonraker on localhost. To look at the page in a browser without OrcaSlicer,
open `orca_afc/web/index.html?mock=1`; `afc-mock.js` answers with made-up lanes.

| File | What it does |
| --- | --- |
| `orca_afc/protocol.py` | Reads AFC's answers and builds every G-code line the tab sends |
| `orca_afc/moonraker.py` | The Moonraker HTTP client |
| `orca_afc/manager.py` | Reads AFC (webhook first, Klipper objects as fallback) and runs the tab's commands |
| `orca_afc/page.py` | The tab itself: the page HTML and its messages |
| `orca_afc/gcode.py` | The G-code rewrite: T numbers, lane-change purges, spool PA |
| `orca_afc/postprocess.py` | The AFC G-code step OrcaSlicer runs on exported files |
| `orca_afc/agent.py` | The AFC printer agent: connecting, status pushes, requests and prints |
| `orca_afc/trays.py` | AFC's lanes as OrcaSlicer's AMS trays, and the print dialog's tray choice |
| `orca_afc/presets.py` | Which filament preset fits each lane's spool |
| `orca_afc/opc.py` | Reads the filament ids out of OrcaSlicer's packed vendor profiles (`system/<vendor>.opc`) |
| `orca_afc/connection.py` | Which Moonraker to talk to: the printer's settings or the plugin config |
| `orca_afc/worker.py` | The plugin's own threads, where all printer traffic runs |
| `orca_afc/web/` | The page (shared with the Snapmaker build's AFC page) |
| `orca_afc/web/afc-icons.js` | Unit icons from Fluidd's AFC cards (GPL-3.0) and Material Design Icons (Apache-2.0) |
