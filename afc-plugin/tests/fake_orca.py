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


class SlicingPipelineCapabilityBase(PythonPluginBase):
    pass


class Step:
    posSlice = "posSlice"
    psGCodePostProcess = "psGCodePostProcess"


class PluginResult:
    Success = "Success"
    Skipped = "Skipped"
    RecoverableError = "RecoverableError"
    FatalError = "FatalError"


class ExecutionResult:
    def __init__(self, status, message="", data=""):
        self.status, self.message, self.data = status, message, data

    @staticmethod
    def success(message="", data=""):
        return ExecutionResult(PluginResult.Success, message, data)

    @staticmethod
    def skipped(message=""):
        return ExecutionResult(PluginResult.Skipped, message)

    @staticmethod
    def failure(status, message, data=""):
        return ExecutionResult(status, message, data)


class SlicingContext:
    """What OrcaSlicer hands the post-process step: a G-code file and the slice's settings."""
    def __init__(self, gcode_path="", step=Step.psGCodePostProcess, config=None, host="File"):
        self.step, self.gcode_path, self.host, self.output_name = step, gcode_path, host, gcode_path
        self.config = config or {}
        self.print = None
        self.object = None

    def config_value(self, key):
        return self.config.get(key)


class base:
    def __init__(self):
        pass

    def register_capabilities(self):
        pass


class Preset:
    def __init__(self, values, name="", file="", is_system=False, is_visible=True, is_compatible=True,
                 is_default=False):
        self.values = values
        self.name, self.file = name, file
        self.is_system, self.is_visible, self.is_compatible, self.is_default = (
            is_system, is_visible, is_compatible, is_default)

    def config_value(self, key):
        return self.values.get(key)  # None for a key the preset lacks, as OrcaSlicer answers


class PresetCollection:
    def __init__(self, presets=()):
        self.presets = list(presets)

    def size(self):
        return len(self.presets)

    def preset(self, index):
        return self.presets[index]

    def find_preset(self, name):
        return next((p for p in self.presets if p.name == name), None)


class PresetBundle:
    """`printer` is the selected printer preset (a Preset, or a dict of its settings)."""
    def __init__(self, printer=None, filaments=(), printers=()):
        self.printer = printer
        self.filaments = PresetCollection(filaments)
        self.printers = PresetCollection(printers)

    def current_printer_preset(self):
        if self.printer is None or isinstance(self.printer, Preset):
            return self.printer
        return Preset(self.printer)


# -- orca.printer_agent

class FilamentSyncMode:
    None_ = "None_"
    Subscription = "Subscription"
    Pull = "Pull"


class AgentInfo:
    def __init__(self, id="", name="", version="", description=""):
        self.id, self.name, self.version, self.description = id, name, version, description


class DetectResult:
    def __init__(self):
        self.result_msg = self.command = self.dev_id = self.model_id = self.dev_name = ""
        self.version = self.bind_state = self.connect_type = ""


class PrinterConnectionParams:
    def __init__(self, dev_id="", host="", port="", username="", password="", use_ssl=False, ca_file=""):
        self.dev_id, self.host, self.port, self.username = dev_id, host, port, username
        self.password, self.use_ssl, self.ca_file = password, use_ssl, ca_file


class PrintParams:
    def __init__(self, **values):
        self.dev_id = self.task_name = self.project_name = self.preset_name = self.filename = ""
        self.config_filename = self.ams_mapping = self.ams_mapping2 = self.dst_file = ""
        self.plate_index = 0
        self.task_bed_leveling = self.task_flow_cali = self.task_record_timelapse = False
        for key, value in values.items():
            setattr(self, key, value)


class PrinterAgentBase(PythonPluginBase):
    pass


orca = types.ModuleType("orca")
orca.PythonPluginBase = PythonPluginBase
orca.base = base
orca.pages = types.SimpleNamespace(PagesPluginCapabilityBase=PagesPluginCapabilityBase)
orca.slicing = types.SimpleNamespace(SlicingPipelineCapabilityBase=SlicingPipelineCapabilityBase, Step=Step)
orca.printer_agent = types.SimpleNamespace(
    PrinterAgentBase=PrinterAgentBase, AgentInfo=AgentInfo, FilamentSyncMode=FilamentSyncMode,
    DetectResult=DetectResult, PrinterConnectionParams=PrinterConnectionParams, PrintParams=PrintParams)
orca.ExecutionResult = ExecutionResult
orca.PluginResult = PluginResult
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
