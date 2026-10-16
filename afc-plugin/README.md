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
  toolhead that already holds its filament get none;
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
   still give its address and API key.
3. If the printer was used before with another agent (Snapmaker, Moonraker) and
   the Device tab shows no printer after the switch, restart OrcaSlicer once:
   the printer moves to the AFC agent then. One that was on the Moonraker agent
   without an API key needs a step before the switch: with the Moonraker agent
   still selected, log out of the printer in the Device tab's printer list.

Then:

- The **Device** tab shows the printer, with AFC's lanes as its AMS trays: one
  tray per T number, four to a unit (the first unit holds T0 to T3).
- The sync button next to **Filament** in the Prepare sidebar puts each loaded
  lane in the slot of its T number (slot 1 is T0) with the filament preset that
  fits its spool: the spool's own vendor and product preset when the printer has
  one, else OrcaSlicer's generic preset of its material.
- **Print** opens OrcaSlicer's print dialog, where each filament gets a tray, so a
  lane. The agent renumbers the file's tool changes to those T numbers, adds the
  lane-change purges and spool flow K the AFC G-code step adds, uploads the file
  to Moonraker and starts it. On a Snapmaker U1, the dialog's bed leveling, flow
  calibration and timelapse switches go into the file.

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
| `orca_afc/connection.py` | Which Moonraker to talk to: the printer's settings or the plugin config |
| `orca_afc/worker.py` | The plugin's own threads, where all printer traffic runs |
| `orca_afc/web/` | The page (shared with the Snapmaker build's AFC page) |
| `orca_afc/web/afc-icons.js` | Unit icons from Fluidd's AFC cards (GPL-3.0) and Material Design Icons (Apache-2.0) |
