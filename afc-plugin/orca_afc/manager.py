"""Reads AFC from the printer and runs what the AFC page asks for.

Everything here runs on the plugin's worker thread, never on OrcaSlicer's UI
thread, since each request waits on the network.
"""

from __future__ import annotations

import json
import threading
import time

from . import protocol
from .moonraker import Moonraker, MoonrakerError, PrinterUnreachable, fetch_json
from .protocol import AFCError, Snapshot

# How long a spool's flow K is trusted before it is read again. AFC autocal only
# writes it after a calibration, so this mostly bounds how soon a new one shows.
FLOW_K_MAX_AGE = 120.0

# Moonraker answers a G-code script only once Klipper has run it, and a lane load
# (a long bowden, an ACE feeding, a purge) can take minutes. A lane move waits this
# long on its own thread; past it, Klipper is still at it and nothing is reported.
LANE_MOVE_TIMEOUT = 900.0

_ACTION_WORDS = {"load": "loading", "unload": "unloading", "eject": "ejecting"}


def read_snapshot(client: Moonraker) -> Snapshot:
    """One AFC read: the webhook first, Klipper objects as the fallback, then
    the lane_data database and the unit dryers to fill in the rest."""
    try:
        snap = protocol.parse_status_endpoint(client.afc_status())
        webhook_error = None
    except PrinterUnreachable:
        raise
    except MoonrakerError as error:
        snap, webhook_error = None, error  # an AFC without the webhook, or none at all

    if snap is not None:
        # The webhook leaves out some settings, multiple tool mapping among them.
        try:
            protocol.merge_afc_object(client.objects_query(protocol.afc_object_query()), snap)
        except MoonrakerError:
            pass
    else:
        try:
            status = client.objects_query({"AFC": None})
        except MoonrakerError:
            # The printer is not answering at all; say why the first call failed.
            raise webhook_error or MoonrakerError("AFC did not answer") from None
        afc = (status or {}).get("status", {}).get("AFC") if isinstance(status, dict) else None
        if not isinstance(afc, dict) or not afc:
            return Snapshot()  # no AFC on this printer
        lanes = protocol._string_list(afc.get("lanes"))
        try:
            objects_list = client.objects_list()
        except MoonrakerError:
            objects_list = {}
        objects = {"AFC": None, "print_task_config": protocol.afc_object_query()["print_task_config"]}
        for name in protocol.lane_object_names(objects_list, lanes):
            objects[name] = None
        snap = protocol.parse_objects_status(client.objects_query(objects)) or Snapshot()

    try:
        protocol.merge_lane_data(client.database_namespace("lane_data"), snap)
    except MoonrakerError:
        pass  # older AFC, or no lane_data yet

    # Unit dryers (ACE, AMS 2 Pro, AMS HT) live on the units' own Klipper objects.
    dryers = protocol.dryer_objects_query(snap)
    if dryers:
        try:
            protocol.merge_dryer_status(client.objects_query(dryers), snap)
        except MoonrakerError:
            pass
    return snap


class AFCManager:
    """The last AFC snapshot of the printer the tab shows, and the commands
    the page sends. Callers serialize the calls (one worker thread)."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._lock = threading.Lock()
        self.snapshot = Snapshot()
        self.printer = ""       # the printer the snapshot came from
        self._flow_k = {}       # spool id -> (k, read at)
        self._k_way = ""        # how K was read last: "direct" (AFC's Spoolman) or "proxy" (Moonraker's)
        self._move = None       # (thread, "lane4 is loading") while a lane move runs
        self._move_error = ""   # how the last lane move failed, until the page shows it

    # ------------------------------------------------------------ reading

    def refresh(self, client: Moonraker) -> str:
        """Read AFC again. Returns an error to show, or "". A failed read on
        the same printer keeps its last good snapshot."""
        if client.url != self.printer:
            self._flow_k.clear()
            self._k_way = ""
        try:
            snap = read_snapshot(client)
        except MoonrakerError as error:
            if client.url != self.printer:
                self.snapshot, self.printer = Snapshot(), client.url
            return str(error)
        self._read_flow_k(client, snap)
        with self._lock:
            self.snapshot, self.printer = snap, client.url
        return ""

    def read(self, client: Moonraker) -> Snapshot | None:
        """A fresh read for the G-code step and the printer agent: what AFC
        has now, or None when the printer or AFC did not answer."""
        error = self.refresh(client)
        with self._lock:
            snap, source = self.snapshot, self.printer
        return snap if not error and source == client.url and snap.present else None

    def _read_flow_k(self, client: Moonraker, snap: Snapshot) -> None:
        """Each spool's saved pressure advance, from Spoolman."""
        now = self._clock()
        for lane in snap.lanes:
            if lane.spool_id <= 0:
                continue
            known = self._flow_k.get(lane.spool_id)
            if known is None or now - known[1] >= FLOW_K_MAX_AGE:
                spoolman = protocol.spoolman_for_printer(snap.spoolman_url, client.url)
                known = (self._spool_flow_k(client, lane.spool_id, spoolman), now)
                self._flow_k[lane.spool_id] = known
            lane.flow_k = known[0]

    def _spool_flow_k(self, client: Moonraker, spool: int, spoolman_url: str) -> float:
        """Asks AFC's own Spoolman address (where autocal writes K) and Moonraker's
        Spoolman proxy, the way that answered last first. One that is slow or
        fails does not stop the other."""
        ways = [("proxy", lambda: client.spoolman_spool(spool))]
        if spoolman_url:
            direct = ("direct", lambda: fetch_json("%s/api/v1/spool/%d" % (spoolman_url.rstrip("/"), spool)))
            ways.insert(1 if self._k_way == "proxy" else 0, direct)
        for way, ask in ways:
            try:
                k = protocol.spool_flow_k(ask())
            except MoonrakerError:
                continue
            self._k_way = way
            return k
        return -1.0  # counts as "no K" until it is due again

    def save_flow_k(self, client: Moonraker, spool: int, k: float, spoolman_url: str = "") -> str:
        """Save a spool's pressure advance in its "flow_k" Spoolman field, as AFC
        autocal does, through the way K was read last first. Returns an error
        to show, or ""."""
        def direct(method, path, body=None):
            return fetch_json("%s/api%s" % (spoolman_url.rstrip("/"), path), timeout=10.0, method=method, body=body)

        ways = [("proxy", lambda method, path, body=None: client.spoolman(method, path, body, timeout=10.0))]
        if spoolman_url:
            ways.insert(1 if self._k_way == "proxy" else 0, ("direct", direct))
        error = ""
        for way, call in ways:
            try:
                fields = call("GET", "/v1/field/spool")
                if not any(isinstance(f, dict) and f.get("key") == "flow_k" for f in fields or []):
                    call("POST", "/v1/field/spool/flow_k", {"name": "Flow K", "field_type": "float"})
                record = call("GET", "/v1/spool/%d" % spool)
                extra = dict(record.get("extra") or {}) if isinstance(record, dict) else {}
                # Spoolman keeps every extra field JSON-encoded.
                extra["flow_k"] = json.dumps(round(float(k), 6))
                call("PATCH", "/v1/spool/%d" % spool, {"extra": extra})
            except MoonrakerError as failure:
                error = str(failure)
                continue
            self._k_way = way
            self._flow_k[spool] = (float(k), self._clock())
            return ""
        return error

    def state(self, printer: str, hint: str = "", error: str = "") -> dict:
        """What the page draws: {connected, afc, error?, hint?}. A lane move
        that failed since the last state is reported here, once."""
        with self._lock:
            snap, source = self.snapshot, self.printer
            if not error:
                error, self._move_error = self._move_error, ""
        afc = protocol.to_json(snap)
        if not printer or source != printer:
            afc["present"] = False
        out = {"connected": bool(printer), "afc": afc}
        if hint:
            out["hint"] = hint
        if error:
            out["error"] = error
        return out

    # ------------------------------------------------------------ commands

    def send(self, client: Moonraker, gcode) -> str:
        """Run G-code on the printer, one command per line. Returns an error or ""."""
        lines = [gcode] if isinstance(gcode, str) else list(gcode)
        if not lines:
            return ""
        # Klipper runs one script at a time, so this one would wait behind the lane move.
        moving = self.lane_moving()
        if moving:
            return moving + "; try again when it is done"
        try:
            client.gcode("\n".join(lines))
        except MoonrakerError as error:
            return str(error)
        return ""

    def edit_lane(self, client: Moonraker, params: dict) -> str:
        gcode = protocol.build_lane_edit_gcode(self.snapshot, params.get("lane", ""), params.get("changes", {}))
        return self.send(client, gcode)

    def lane_action(self, client: Moonraker, params: dict) -> str:
        """Start a lane load, unload or eject and return without waiting for
        it: the page follows it on its next reads, and an error from Klipper
        comes with the first state after it."""
        lane, action = params.get("lane", ""), params.get("action", "")
        gcode = protocol.build_lane_action_gcode(self.snapshot, lane, action)
        moving = self.lane_moving()
        if moving:
            return moving + "; wait until it is done"
        # Filament moves only on an idle printer: never in the middle of a print.
        busy = self._busy(client, "The printer is {state}; lane moves wait until it is idle",
                          "Could not read the printer state, so nothing was moved")
        if busy:
            return busy
        what = "%s is %s" % (lane, _ACTION_WORDS.get(action, action))

        def move():
            try:
                client.gcode(gcode, timeout=LANE_MOVE_TIMEOUT)
            except PrinterUnreachable as error:
                # A timeout only means Klipper is still at it; anything else is worth saying.
                if "timed out" not in str(error):
                    self._move_error = "%s: %s" % (lane, error)
            except MoonrakerError as error:
                self._move_error = "%s: %s" % (lane, error)
            finally:
                with self._lock:
                    self._move = None

        thread = threading.Thread(target=move, name="AFC lane move", daemon=True)
        with self._lock:
            self._move = (thread, what)
        thread.start()
        return ""

    def lane_moving(self) -> str:
        """"lane4 is loading" while a lane move from the page runs, else ""."""
        with self._lock:
            move = self._move
        return move[1] if move is not None and move[0].is_alive() else ""

    def wait_lane_move(self, timeout: float) -> bool:
        """Wait for a running lane move to end (the tests use it). True once none runs."""
        with self._lock:
            move = self._move
        if move is not None:
            move[0].join(timeout)
        return not self.lane_moving()

    def dry(self, client: Moonraker, params: dict) -> str:
        return self.send(client, protocol.build_dry_gcode(self.snapshot, params.get("unit", ""), params))

    def apply_mapping(self, client: Moonraker, params: dict) -> str:
        mapping = params.get("mapping")
        if not isinstance(mapping, list):
            raise AFCError("param [mapping] required")
        pairs = []
        for m in mapping:
            if not isinstance(m, dict) or not protocol._is_integer(m.get("tool")) or not isinstance(m.get("lane"), str):
                raise AFCError("mapping entries need an integer tool and a lane name")
            pairs.append((m["tool"], m["lane"]))
        gcode = protocol.build_tool_mapping_gcode(self.snapshot, pairs)
        if params.get("idle_only"):
            # An upload maps lanes for a print that starts later, so a print running now keeps its lanes.
            busy = self._busy(client, "The printer is {state}, so AFC's lanes stay as they are. Upload without "
                                      "lanes and map them when the print starts.",
                              "Could not read the printer state, so AFC's lanes were not changed")
            if busy:
                return busy
        return self.send(client, gcode)

    def set_print_options(self, client: Moonraker, params: dict) -> str:
        if not self.snapshot.u1_print.present:
            raise AFCError("This printer has no U1 print options")
        return self.send(client, protocol.u1_print_options_gcode(protocol.U1PrintOptions.from_page(params)))

    @staticmethod
    def _busy(client: Moonraker, printing: str, unknown: str) -> str:
        """`printing` (with {state}) while a print runs or is paused, `unknown`
        when the state cannot be read, "" when the printer is idle."""
        try:
            state = client.print_state()
        except MoonrakerError:
            state = ""
        if state in ("printing", "paused"):
            return printing.format(state=state)
        return "" if state else unknown


_shared = AFCManager()


def shared() -> AFCManager:
    """The one manager the tab, the G-code step and the printer agent share,
    so the tab shows the latest read whoever made it."""
    return _shared
