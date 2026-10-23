"""AFC's newest versions on GitHub, for the Device tab's Update page in the
AFC build of OrcaSlicer: AFC_VERSION in extras/AFC.py on Armored Turtle's
release branch (main) and development branch (DEV)."""

from __future__ import annotations

import re
import ssl
import urllib.error
import urllib.request

REPO = "https://github.com/ArmoredTurtle/AFC-Klipper-Add-On"
BRANCHES = ("main", "DEV")
EVERY = 6 * 3600.0  # seconds between reads
RETRY = 600.0       # seconds before a failed read is tried again
_SOURCE = "https://raw.githubusercontent.com/ArmoredTurtle/AFC-Klipper-Add-On/%s/extras/AFC.py"
_HEAD = 16 * 1024   # AFC_VERSION sits near the top of AFC.py
_VERSION = re.compile(r"""^AFC_VERSION\s*=\s*["']([^"']+)["']""", re.M)


def read(opener=urllib.request.urlopen, timeout: float = 15.0) -> dict:
    """{branch: version} for each branch GitHub answered for."""
    out = {}
    for branch in BRANCHES:
        request = urllib.request.Request(_SOURCE % branch, headers={"Range": "bytes=0-%d" % (_HEAD - 1)})
        try:
            with opener(request, timeout=timeout, context=ssl.create_default_context()) as answer:
                text = answer.read(_HEAD).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError):
            continue
        match = _VERSION.search(text)
        if match:
            out[branch] = match.group(1)
    return out


def newer(installed: str, latest: dict) -> bool:
    """Whether a branch has a newer version than the one installed."""
    mine = _parts(installed)
    return mine is not None and any((_parts(v) or ()) > mine for v in latest.values())


def _parts(version: str):
    numbers = re.findall(r"\d+", version or "")
    return tuple(int(n) for n in numbers) if numbers else None
