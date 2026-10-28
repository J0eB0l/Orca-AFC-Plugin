"""What the plugin asks OrcaSlicer for while it loads (orca.request_permissions),
so one prompt at startup covers its threads, GitHub (the Update page's AFC
versions) and the Moonraker of each printer preset that uses the AFC agent,
instead of a prompt the first time each is used. OrcaSlicer refuses a use it
has not been asked for while that prompt is open, which a first print or sync
would hit.

The printer presets come from their files in OrcaSlicer's user folder:
OrcaSlicer loads plugins on a worker while it loads its presets, so its preset
bundle is not there to read yet. A printer added later is asked for when it
is first reached, and kept from then on."""

from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

GITHUB = "https://raw.githubusercontent.com"  # upstream.py
AGENT_ID = "afc"  # agent.AGENT_ID, which a printer preset names in printer_agent
_INHERITS_MAX = 10


def data_dir(module_file: str = __file__):
    """OrcaSlicer's data folder, the one that holds orca_plugins/ where the
    plugin is extracted; None when the plugin runs from elsewhere."""
    for parent in Path(module_file).resolve().parents:
        if parent.name == "orca_plugins":
            return parent.parent
    return None


def _host(print_host: str) -> str:
    """"192.168.4.36", "printer.local:7125", "https://x/" -> scheme://host, the
    form OrcaSlicer grants a whole host by; "" when it names none."""
    text = (print_host or "").strip()
    if not text:
        return ""
    url = urllib.parse.urlsplit(text if "://" in text else "http://" + text)
    if url.scheme not in ("http", "https") or not url.hostname:
        return ""
    host = url.hostname if ":" not in url.hostname else "[%s]" % url.hostname
    return "%s://%s" % (url.scheme, host)


def printer_hosts(folder) -> list:
    """scheme://host of each user printer preset in `folder` (OrcaSlicer's data
    folder) whose printer agent is AFC, following inherits between user presets."""
    presets = {}
    for path in sorted(Path(folder, "user").glob("*/machine/*.json")) if folder else ():
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(values, dict):
            presets.setdefault(str(values.get("name") or path.stem), values)

    def value(values, key):
        for _ in range(_INHERITS_MAX):
            if key in values:
                found = values[key]
                return found[0] if isinstance(found, list) and found else found
            values = presets.get(str(values.get("inherits") or ""))
            if values is None:
                return None
        return None

    hosts = []
    for values in presets.values():
        if value(values, "printer_agent") != AGENT_ID:
            continue
        host = _host(str(value(values, "print_host") or ""))
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def declare(request, folder=None) -> None:
    """Asks for the plugin's permissions through `request` (orca.request_permissions),
    which an OrcaSlicer without it lacks; the plugin then asks as it goes."""
    if request is None:
        return
    folder = data_dir() if folder is None else folder
    request(threading=True, network=[GITHUB] + printer_hosts(folder))
