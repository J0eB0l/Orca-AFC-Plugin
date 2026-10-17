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
    info      the /printer/info result
    uploads   files uploaded to the gcodes root, by name
    metadata  /server/files/metadata results by file name (404 for any other)
    files     paths under the gcodes root that a GET serves (thumbnails)
    started   files /printer/print/start was asked to print
    offline   True to answer everything 503, as Moonraker does while Klipper restarts
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
        self.info = {"state": "ready", "hostname": "voron", "software_version": "v0.12.0-500"}
        self.uploads = {}
        self.metadata = {}
        self.files = set()
        self.started = []
        self.offline = False
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
        if self.offline:
            return 503, {"error": {"code": 503, "message": "Klippy Disconnected"}}
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
        if method == "GET" and path == "/printer/info":
            return 200, {"result": self.info}
        if method == "POST" and path == "/server/files/upload":
            self.uploads[body["filename"]] = body["data"]
            return 201, {"result": {"item": {"path": body["filename"], "root": body.get("root")},
                                    "action": "create_file"}}
        if method == "GET" and path == "/server/files/metadata":
            meta = self.metadata.get(query.get("filename"))
            return (404, {"error": {"code": 404, "message": "Metadata not available"}}) if meta is None else (
                200, {"result": meta})
        if method == "GET" and path.startswith("/server/files/gcodes/"):
            name = urllib.parse.unquote(path[len("/server/files/gcodes/"):])
            return (200, {"path": name}) if name in self.files else (404, {"error": {"code": 404, "message": "No file"}})
        if method == "POST" and path == "/printer/print/start":
            self.started.append(body.get("filename"))
            return 200, {"result": "ok"}
        if method == "POST" and path in ("/printer/print/pause", "/printer/print/resume", "/printer/print/cancel"):
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
            raw = self.rfile.read(length) if length else b""
            kind = self.headers.get("Content-Type") or ""
            if kind.startswith("multipart/form-data"):
                body = _form(raw, kind.split("boundary=", 1)[1])
            else:
                body = json.loads(raw.decode("utf-8")) if raw else {}
            query = dict(urllib.parse.parse_qsl(parts.query))
            fake.requests.append((method, parts.path, body))
            code, answer = fake.answer(method, parts.path, query, body, self.headers)
            raw = json.dumps(answer).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up waiting, as a timed-out call does

        def do_GET(self):
            self._serve("GET")

        def do_POST(self):
            self._serve("POST")

        def log_message(self, *args):
            pass

    return Handler


def _form(raw: bytes, boundary: str) -> dict:
    """A multipart upload's fields; the file's bytes go in "data", its name in "filename"."""
    out = {}
    for part in raw.split(b"--" + boundary.encode())[1:]:
        if part.startswith(b"--"):
            break
        head, _, value = part[2:].partition(b"\r\n\r\n")
        value = value[:-2] if value.endswith(b"\r\n") else value
        disposition = head.decode("utf-8").split("\r\n")[0]
        name = disposition.split('name="', 1)[1].split('"', 1)[0]
        if 'filename="' in disposition:
            out["filename"] = disposition.split('filename="', 1)[1].split('"', 1)[0]
            out["data"] = value
        else:
            out[name] = value.decode("utf-8")
    return out


def closed_port_url():
    """An address nothing listens on."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
    url = "http://127.0.0.1:%d" % server.server_address[1]
    server.server_close()
    return url
