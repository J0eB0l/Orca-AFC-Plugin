"""The AFC tab: OrcaSlicer's side of the AFC page."""

from __future__ import annotations

import json
import traceback
import webbrowser
from pathlib import Path

import orca

from . import manager as afc_manager
from . import protocol, worker
from .connection import printer_connection
from .moonraker import Moonraker
from .protocol import AFCError

WEB = Path(__file__).resolve().parent / "web"


def page_html(view: str = "panel", mode: str = "") -> str:
    """index.html with afc.css, afc-icons.js and afc.js put inline, since
    OrcaSlicer loads a plugin page from a string and the page cannot fetch
    files next to it."""
    html = (WEB / "index.html").read_text(encoding="utf-8")
    css = (WEB / "afc.css").read_text(encoding="utf-8")
    icons = (WEB / "afc-icons.js").read_text(encoding="utf-8")
    js = (WEB / "afc.js").read_text(encoding="utf-8")
    setup = "window.AFC_VIEW = %s; window.AFC_MODE = %s;" % (json.dumps(view), json.dumps(mode))
    html = html.replace('<link rel="stylesheet" href="afc.css">', "<style>\n" + css + "</style>")
    html = html.replace('<script src="afc-icons.js"></script>', "<script>\n" + icons + "</script>")
    html = html.replace('<script src="afc-mock.js"></script>\n', "")
    return html.replace('<script src="afc.js"></script>', "<script>" + setup + "</script>\n<script>\n" + js + "</script>")


class AFCPage(orca.pages.PagesPluginCapabilityBase):
    def __init__(self):
        super().__init__()
        self.manager = afc_manager.shared()
        self.worker = worker.shared()

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
            url = protocol.spoolman_for_printer(manager.snapshot.spoolman_url, printer)
            if not url:
                raise AFCError("AFC did not report a Spoolman address")
            spool = params.get("spool_id")
            webbrowser.open(protocol.spoolman_page(url, spool if protocol._is_integer(spool) else 0))
            return {}
        commands = {
            "edit_lane": manager.edit_lane,
            "set_runout": manager.set_runout,
            "set_backup": manager.set_backup,
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
