"""The AFC tab: OrcaSlicer's side of the AFC page."""

from __future__ import annotations

import json
import queue
import threading
import traceback
import webbrowser
from pathlib import Path

import orca

from . import protocol
from .manager import AFCManager
from .moonraker import Moonraker, base_url
from .protocol import AFCError

WEB = Path(__file__).resolve().parent / "web"

# OrcaSlicer's connection types that are Moonraker, or usually are ("Octo/Klipper"
# hosts are often Moonraker too; a real OctoPrint just answers 404).
MOONRAKER_HOST_TYPES = ("moonraker", "octoprint")


def page_html(view: str = "panel", mode: str = "") -> str:
    """index.html with afc.css and afc.js put inline, since OrcaSlicer loads
    a plugin page from a string and the page cannot fetch files next to it."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "afc.css").read_text(encoding="utf-8")
    js = (WEB / "afc.js").read_text(encoding="utf-8")
    setup = "window.AFC_VIEW = %s; window.AFC_MODE = %s;" % (json.dumps(view), json.dumps(mode))
    html = html.replace('<link rel="stylesheet" href="afc.css">', "<style>\n" + css + "</style>")
    html = html.replace('<script src="afc-mock.js"></script>\n', "")
    return html.replace('<script src="afc.js"></script>', "<script>" + setup + "</script>\n<script>\n" + js + "</script>")


def printer_connection(config: dict, bundle) -> tuple:
    """(Moonraker URL, API key, hint) for the selected printer. The URL is
    empty, and the hint says why, when there is none to use. An address in
    the plugin's config wins over the printer's connection settings."""
    if config.get("printer_url"):
        return base_url(str(config["printer_url"])), str(config.get("api_key") or ""), ""
    preset = bundle.current_printer_preset() if bundle is not None else None
    if preset is None:
        return "", "", "OrcaSlicer has no printer selected."
    host_type = preset.config_value("host_type") or ""
    host = preset.config_value("print_host") or ""
    if host_type and host_type not in MOONRAKER_HOST_TYPES:
        return "", "", ("This printer connects as %s. AFC runs on Klipper, so set its connection to "
                        "Octo/Klipper with the Moonraker address." % host_type)
    if not host:
        return "", "", ("Set the printer's address in its connection settings (the button next to the "
                        "printer's name), or put printer_url in the AFC plugin's config.")
    return base_url(host), preset.config_value("printhost_apikey") or "", ""


class Worker:
    """One thread that runs the page's requests in order, so the network never
    holds up OrcaSlicer's window."""

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def submit(self, job) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="AFC plugin", daemon=True)
                self._thread.start()
        self._jobs.put(job)

    def stop(self) -> None:
        self._jobs.put(None)

    def _run(self) -> None:
        while True:
            job = self._jobs.get()
            if job is None:
                return
            try:
                job()
            except Exception:  # a job reports its own errors; this keeps the thread alive
                traceback.print_exc()


class AFCPage(orca.pages.PagesPluginCapabilityBase):
    def __init__(self):
        super().__init__()
        self.manager = AFCManager()
        self.worker = Worker()

    def get_name(self):
        return "AFC"

    def get_icon(self):
        return str(WEB / "afc.svg")

    def get_ui(self):
        return page_html("panel")

    def get_default_config(self):
        return {"printer_url": "", "api_key": ""}

    def on_unload(self):
        self.worker.stop()

    # Called on the UI thread. Presets are read here; the work goes to the worker.
    def on_message(self, message):
        if not isinstance(message, dict) or "cmd" not in message:
            return
        try:
            config = json.loads(self.get_config() or "{}")
        except ValueError:
            config = {}
        try:
            url, api_key, hint = printer_connection(config if isinstance(config, dict) else {},
                                                    orca.host.preset_bundle())
        except RuntimeError as error:  # OrcaSlicer is starting up or closing
            url, api_key, hint = "", "", str(error)
        client = Moonraker(url, api_key) if url else None
        self.worker.submit(lambda: self._answer(message, client, hint))

    def _answer(self, message: dict, client, hint: str) -> None:
        afc_id = message.get("afc_id")
        try:
            data = self._handle(str(message.get("cmd")), message.get("params") or {}, client, hint)
            self.post_message({"afc_id": afc_id, "ok": True, "data": data})
        except AFCError as error:
            self.post_message({"afc_id": afc_id, "ok": False, "message": str(error)})
        except Exception as error:
            traceback.print_exc()
            self.post_message({"afc_id": afc_id, "ok": False, "message": "AFC plugin error: %s" % error})

    def _handle(self, cmd: str, params: dict, client, hint: str):
        manager = self.manager
        printer = client.url if client else ""
        if cmd == "get_state":
            error = manager.refresh(client) if client and params.get("refresh", True) else ""
            return manager.state(printer, hint, error)
        if cmd == "open_spoolman":
            url = manager.snapshot.spoolman_url
            if not url:
                raise AFCError("AFC did not report a Spoolman address")
            spool = params.get("spool_id")
            webbrowser.open(protocol.spoolman_page(url, spool if protocol._is_integer(spool) else 0))
            return {}
        commands = {
            "edit_lane": manager.edit_lane,
            "lane_action": manager.lane_action,
            "dry": manager.dry,
            "apply_mapping": manager.apply_mapping,
            "set_print_options": manager.set_print_options,
        }
        if cmd not in commands:
            raise AFCError("unknown command " + cmd)
        if client is None:
            raise AFCError(hint or "The printer is not connected")
        error = commands[cmd](client, params)
        # What the printer has now, with the error (if any) for the page to show.
        refresh_error = manager.refresh(client)
        return manager.state(printer, hint, error or refresh_error)
