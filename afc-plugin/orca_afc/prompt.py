"""Klipper's action prompts, which AFC asks its calibration with, for the AFC
build of OrcaSlicer to show as Mainsail and Fluidd do.

A macro opens a prompt by writing lines to the console: `// action:prompt_begin
<title>`, then `prompt_text`, `prompt_button <label>|<gcode>|<style>` (in rows
between `prompt_button_group_start` and `_end`), `prompt_footer_button`, and
`prompt_show`; `prompt_end` closes it. The agent reads them from Moonraker's
G-code store."""

from __future__ import annotations

PREFIX = "// action:prompt_"
LINES = 100   # newest G-code store lines read at a time
WATCH = 600.0  # seconds the store is read after OrcaSlicer sends G-code, for the prompts it opens


def _button(rest: str) -> dict:
    """"Lane 1|CALIBRATE_AFC LANE=lane1|primary" -> its label, G-code and style;
    the G-code is the label when the line names none, as in Mainsail."""
    parts = rest.split("|")
    label = parts[0].strip()
    gcode = parts[1].strip() if len(parts) > 1 and parts[1].strip() else label
    style = parts[2].strip() if len(parts) > 2 else ""
    return {"label": label, "gcode": gcode, "style": style}


class Watch:
    """The prompt the console's lines leave open. `id` changes each time one
    opens or closes, so OrcaSlicer tells a new prompt from one it already showed."""

    def __init__(self):
        self.seen = None      # time of the newest line read, None before the first read
        self.prompt = None    # the open prompt: {title, text, rows, footer}
        self.id = 0
        self._building = None
        self._group = None

    def feed(self, entries) -> bool:
        """Reads the G-code store's entries (oldest first) newer than the last
        read; True when a prompt opened or closed."""
        changed = False
        newest = self.seen
        for entry in entries or ():
            if not isinstance(entry, dict):
                continue
            when = entry.get("time")
            when = when if isinstance(when, (int, float)) else 0.0
            if self.seen is not None and when <= self.seen:
                continue
            newest = when if newest is None else max(newest, when)
            message = entry.get("message")
            for line in (message.splitlines() if isinstance(message, str) else ()):
                changed |= self._line(line.strip())
        self.seen = newest if newest is not None else (self.seen or 0.0)
        return changed

    def _line(self, line: str) -> bool:
        if not line.startswith(PREFIX):
            return False
        word, _, rest = line[len(PREFIX):].partition(" ")
        building = self._building
        if word == "begin":
            self._building = {"title": rest.strip(), "text": [], "rows": [], "footer": []}
            self._group = None
        elif word == "end":
            self._building = self._group = None
            if self.prompt is not None:
                self.prompt = None
                self.id += 1
                return True
        elif building is None:
            return False
        elif word == "text":
            building["text"].append(rest.strip())
        elif word == "button":
            if self._group is not None:
                self._group.append(_button(rest))
            else:
                building["rows"].append([_button(rest)])
        elif word == "button_group_start":
            self._group = []
        elif word == "button_group_end":
            if self._group:
                building["rows"].append(self._group)
            self._group = None
        elif word == "footer_button":
            building["footer"].append(_button(rest))
        elif word == "show":
            self.prompt = building
            self.id += 1
            return True
        return False

    def json(self) -> dict:
        """The agent_prompt the AFC build of OrcaSlicer shows: {id, open} and the
        open prompt's title, text lines, rows of buttons and footer buttons."""
        out = {"id": self.id, "open": self.prompt is not None}
        if self.prompt is not None:
            out.update(self.prompt)
        return out
