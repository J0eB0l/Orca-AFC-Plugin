# AFC plugin for OrcaSlicer 2.5

Adds an **AFC** tab to OrcaSlicer's top bar showing what
[AFC](https://github.com/ArmoredTurtle/AFC-Klipper-Add-On) reports from the
printer: every unit and lane, their spools, and the unit dryers. From the tab
you can:

- edit a lane's color, material, weight, temperatures, T number and Spoolman spool;
- load, unload and eject a lane (only while the printer is idle);
- start and stop drying on an ACE, ACE 2 Pro, AMS 2 Pro or AMS HT;
- see each spool's flow K, which AFC autocal saves in Spoolman.

It talks to Moonraker over HTTP. Nothing here changes how OrcaSlicer slices.

## Install

1. Get `orca_afc.whl` from the **AFC plugin** workflow's artifacts, or build it
   yourself with `python3 afc-plugin/build_wheel.py` (it lands in `afc-plugin/dist/`).
2. In OrcaSlicer, open **File > Plugins** and choose **Install plugin**, then pick
   `orca_afc.whl`.
3. Turn the AFC plugin on if it is not on already. OrcaSlicer asks once whether it may
   start a thread; allow it, since the tab reads the printer from that thread.

Installing a newer `orca_afc.whl` replaces the old one. Keep the file name as it
is: OrcaSlicer names the plugin after the file.

## Printer address

The tab uses the selected printer's connection settings: **host type** Octo/Klipper
(or Moonraker), the **hostname or IP** and the **API key** if Moonraker needs one.
That is the same address OrcaSlicer uploads to, so a printer that already uploads
works as it is.

To point the tab somewhere else, set the plugin's config in the Plugins dialog:

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
| `orca_afc/page.py` | The tab itself: page HTML, printer address, the worker thread |
| `orca_afc/web/` | The page (shared with the Snapmaker build's AFC page) |
