"""Which Moonraker the plugin talks to: the selected printer's connection
settings, or the address in the plugin's own config."""

from __future__ import annotations

from .moonraker import base_url

# OrcaSlicer's connection types that are Moonraker, or usually are ("Octo/Klipper"
# hosts are often Moonraker too; a real OctoPrint just answers 404).
MOONRAKER_HOST_TYPES = ("moonraker", "octoprint")


def connection_from_values(config: dict, value) -> tuple:
    """(Moonraker URL, API key, hint) from the plugin's config and `value(key)`,
    which reads a printer setting (None when there is none). The URL is empty,
    and the hint says why, when there is none to use. An address in the
    plugin's config wins over the printer's connection settings."""
    if config.get("printer_url"):
        return base_url(str(config["printer_url"])), str(config.get("api_key") or ""), ""
    if value is None:
        return "", "", "OrcaSlicer has no printer selected."
    host_type = value("host_type") or ""
    host = value("print_host") or ""
    if host_type and host_type not in MOONRAKER_HOST_TYPES:
        return "", "", ("This printer connects as %s. AFC runs on Klipper, so set its connection to "
                        "Octo/Klipper with the Moonraker address." % host_type)
    if not host:
        return "", "", ("Set the printer's address in its connection settings (the button next to the "
                        "printer's name), or put printer_url in the AFC plugin's config.")
    return base_url(host), value("printhost_apikey") or "", ""


def printer_connection(config: dict, bundle) -> tuple:
    """connection_from_values() for the printer selected in OrcaSlicer."""
    preset = bundle.current_printer_preset() if bundle is not None else None
    return connection_from_values(config, preset.config_value if preset is not None else None)
