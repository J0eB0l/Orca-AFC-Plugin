"""A stand-in for OrcaSlicer's embedded `orca` module, so the plugin imports
and runs outside OrcaSlicer. Import this before anything from orca_afc."""

import json
import sys
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class PythonPluginBase:
    def __init__(self):
        self._config = "{}"

    def get_config(self):
        return self._config

    def save_config(self, config):
        json.loads(config)
        self._config = config
        return True


class PagesPluginCapabilityBase(PythonPluginBase):
    def __init__(self):
        super().__init__()
        self.posted = []
        self.posted_event = threading.Event()

    def post_message(self, data):
        self.posted.append(data)
        self.posted_event.set()

    def wait_posted(self, count=1, timeout=10.0):
        """The posted messages once there are `count` of them."""
        while len(self.posted) < count:
            self.posted_event.clear()
            if len(self.posted) >= count:
                break
            if not self.posted_event.wait(timeout):
                raise AssertionError("the page got %d messages, expected %d" % (len(self.posted), count))
        return self.posted


class base:
    def __init__(self):
        pass

    def register_capabilities(self):
        pass


class Preset:
    def __init__(self, values):
        self.values = values

    def config_value(self, key):
        return self.values.get(key)  # None for a key the preset lacks, as OrcaSlicer answers


class PresetBundle:
    def __init__(self, printer=None):
        self.printer = printer

    def current_printer_preset(self):
        return None if self.printer is None else Preset(self.printer)


orca = types.ModuleType("orca")
orca.PythonPluginBase = PythonPluginBase
orca.base = base
orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesPluginCapabilityBase)
orca.plugins = []         # classes decorated with @orca.plugin
orca.capabilities = []    # classes passed to orca.register_capability()
orca.bundle = PresetBundle()


def _preset_bundle():
    # OrcaSlicer raises while it starts up or closes; a test sets bundle to the error.
    if isinstance(orca.bundle, Exception):
        raise orca.bundle
    return orca.bundle


orca.host = types.SimpleNamespace(preset_bundle=_preset_bundle)


def _plugin(cls):
    orca.plugins.append(cls)
    return cls


orca.plugin = _plugin
orca.register_capability = orca.capabilities.append
orca.Preset = Preset
orca.PresetBundle = PresetBundle

sys.modules["orca"] = orca
