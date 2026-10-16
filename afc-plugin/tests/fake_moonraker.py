"""A Moonraker stand-in on localhost that answers the calls the AFC tab makes."""

import json
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeMoonraker:
    """Serves AFC's webhook, Klipper objects, the lane_data namespace, G-code
    and the Spoolman proxy from plain dicts the test sets up.

    webhook   the /printer/afc/status result, or None for a 404 (no webhook)
    objects   Klipper objects by name, for /printer/objects/query and /list
    lane_data the lane_data namespace's value, or None for a 404
    spools    Spoolman spools by id, for the proxy and /api/v1/spool/<id>
    gcode_error  a message G-code calls fail with, as Klipper's errors come back
    gcode_delay  seconds a G-code call takes before Moonraker answers, as a lane load does
    """

    def __init__(self):
        self.webhook = None
        self.objects = {}
        self.lane_data = None
        self.spools = {}
        self.proxy = True
        self.gcode_error = ""
        self.gcode_delay = 0.0
        self.api_key = ""
        self.requests = []   # (method, path, body)
        self.scripts = []    # G-code scripts run
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(self))
        self._thread = threading.Thread(target=self._server.serve_forever, args=(0.05,), daemon=True)
        self._thread.start()

    @property
    def url(self):
        return "http://127.0.0.1:%d" % self._server.server_address[1]

    def close(self):
        self._server.shutdown()
        self._server.server_close()

    def paths(self):
        return [path for _, path, _ in self.requests]

    # -- answers

    def answer(self, method, path, query, body, headers):
        if self.api_key and headers.get("X-Api-Key") != self.api_key:
            return 401, {"error": {"code": 401, "message": "Unauthorized"}}
        if method == "GET" and path == "/printer/afc/status":
            return (404, {"error": {"code": 404, "message": "Not Found"}}) if self.webhook is None else (
                200, {"result": self.webhook})
        if method == "GET" and path == "/printer/objects/list":
            return 200, {"result": {"objects": list(self.objects)}}
        if method == "POST" and path == "/printer/objects/query":
            status = {}
            for name, fields in (body.get("objects") or {}).items():
                obj = self.objects.get(name)
                if obj is None:
                    # Klipper answers an object it lacks with null fields.
                    status[name] = {f: None for f in fields} if fields else {}
                else:
                    status[name] = {f: obj.get(f) for f in fields} if fields else dict(obj)
            return 200, {"result": {"eventtime": 1.0, "status": status}}
        if method == "GET" and path == "/server/database/item":
            if query.get("namespace") != "lane_data" or self.lane_data is None:
                return 404, {"error": {"code": 404, "message": "Namespace '%s' not found" % query.get("namespace")}}
            return 200, {"result": {"namespace": "lane_data", "key": None, "value": self.lane_data}}
        if method == "POST" and path == "/printer/gcode/script":
            self.scripts.append(body.get("script"))
            if self.gcode_delay:
                time.sleep(self.gcode_delay)
            if self.gcode_error:
                return 400, {"error": {"code": 400, "message": self.gcode_error}}
            return 200, {"result": "ok"}
        if method == "POST" and path == "/server/spoolman/proxy":
            if not self.proxy:
                return 404, {"error": {"code": 404, "message": "Spoolman not configured"}}
            spool = self.spools.get(int(body["path"].rsplit("/", 1)[1]))
            if spool is None:
                return 200, {"result": {"response": None, "error": {"status_code": 404, "message": "No spool"}}}
            return 200, {"result": {"response": spool, "error": None}}
        if method == "GET" and path.startswith("/api/v1/spool/"):
            spool = self.spools.get(int(path.rsplit("/", 1)[1]))
            return (404, {"message": "No spool"}) if spool is None else (200, spool)
        return 404, {"error": {"code": 404, "message": "Not Found"}}


def _handler(fake):
    class Handler(BaseHTTPRequestHandler):
        def _serve(self, method):
            parts = urllib.parse.urlsplit(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
            query = dict(urllib.parse.parse_qsl(parts.query))
            fake.requests.append((method, parts.path, body))
            code, answer = fake.answer(method, parts.path, query, body, self.headers)
            raw = json.dumps(answer).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def log_message(self, *args):
            pass

    return Handler


def closed_port_url():
    """An address nothing listens on."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    url = "http://127.0.0.1:%d" % server.server_address[1]
    server.server_close()
    return url
