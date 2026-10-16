"""The "AFC G-code" step: OrcaSlicer runs it on each file it exports or
uploads once it is picked under Slicing Pipeline Plugin (process settings,
Others, Advanced mode).

It reads AFC from the printer for the toolhead each T number's lane feeds and
its spool's flow K, then rewrites the file with gcode.transform(). A printer
that does not answer still gets the file, without what only AFC knows.
"""

from __future__ import annotations

import json
import traceback

import orca

from . import gcode, worker
from . import manager as afc_manager
from .connection import connection_from_values
from .moonraker import Moonraker

# How long the step waits for the printer before it writes the file without AFC's data.
READ_TIMEOUT = 15.0


def printer_setting(value):
    """`value(key)` for the printer's connection settings. OrcaSlicer leaves
    them out of a slice's settings, so they come from the selected printer."""
    def read(key):
        answer = value(key) if value is not None else None
        if answer is not None:
            return answer
        try:
            preset = orca.host.preset_bundle().current_printer_preset()
        except Exception:  # OrcaSlicer is starting up or closing
            return None
        return preset.config_value(key) if preset is not None else None
    return read


def read_afc(config: dict, value, timeout: float = READ_TIMEOUT):
    """(AFC snapshot or None, why there is none)."""
    url, api_key, hint = connection_from_values(config, printer_setting(value))
    if not url:
        return None, hint
    client = Moonraker(url, api_key)
    try:
        snapshot = worker.shared().call(lambda: afc_manager.shared().read(client), timeout)
    except Exception as error:  # no thread, no answer in time
        return None, str(error)
    return snapshot, "" if snapshot is not None else "AFC did not answer at " + url


def process_file(path: str, config: dict, value, timeout: float = READ_TIMEOUT) -> str:
    """Rewrite the G-code file at `path` in place. Returns what was done."""
    # newline="" and surrogateescape give the file back byte for byte where nothing changed.
    with open(path, "r", encoding="utf-8", errors="surrogateescape", newline="") as f:
        text = f.read()
    settings = gcode.FileSettings.from_config(gcode.read_config_block(text))
    if not settings.klipper:
        return "AFC G-code: not a Klipper file, left as it is"
    snapshot, why = read_afc(config, value, timeout)
    plan = gcode.plan_for(snapshot, settings.filaments, None, settings)
    result = gcode.transform(text, plan, settings)
    if result.text != text:
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(result.text)
    message = "AFC G-code: " + result.summary()
    return message + (" (without AFC: %s)" % why if why else "")


class AFCGcode(orca.slicing.SlicingPipelineCapabilityBase):
    def get_name(self):
        return "AFC G-code"

    def get_default_config(self):
        return {"printer_url": "", "api_key": ""}

    def execute(self, ctx):
        # The geometry steps are not ours; only the finished file is.
        if ctx.step != orca.slicing.Step.psGCodePostProcess or not ctx.gcode_path:
            return orca.ExecutionResult.success()
        try:
            config = json.loads(self.get_config() or "{}")
        except ValueError:
            config = {}
        try:
            message = process_file(ctx.gcode_path, config if isinstance(config, dict) else {}, ctx.config_value)
        except OSError as error:
            return orca.ExecutionResult.failure(orca.PluginResult.FatalError, "AFC G-code could not rewrite the file: %s" % error)
        except Exception as error:
            traceback.print_exc()
            return orca.ExecutionResult.failure(orca.PluginResult.FatalError, "AFC G-code failed: %s" % error)
        return orca.ExecutionResult.success(message)
