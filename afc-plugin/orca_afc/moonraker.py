"""A small Moonraker client over its HTTP API.

The printer's address and API key come from the printer preset's connection
settings (print_host, printhost_apikey), as OrcaSlicer's own Moonraker agent
reads them.
"""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.parse
import urllib.request
import uuid

_CHUNK = 64 * 1024


class _Canceled(Exception):
    pass


class MoonrakerError(Exception):
    pass


class PrinterUnreachable(MoonrakerError):
    """No answer at all: wrong address, printer off, network down."""


def base_url(print_host: str) -> str:
    """"192.168.1.20", "printer.local:7125/", "https://x" -> a URL with a scheme
    and no trailing slash. Empty when there is no host."""
    host = (print_host or "").strip()
    if not host:
        return ""
    if "://" not in host:
        host = "http://" + host
    return host.rstrip("/")


class Moonraker:
    def __init__(self, url: str, api_key: str = "", timeout: float = 4.0, opener=None):
        self.url = url
        self.api_key = api_key or ""
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def _request(self, method: str, path: str, body=None, timeout=None):
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        request = urllib.request.Request(self.url + path, data=data, headers=headers, method=method)
        try:
            with self._open(request, timeout=timeout or self.timeout, context=_ssl_context(self.url)) as answer:
                raw = answer.read()
        except urllib.error.HTTPError as error:
            raise MoonrakerError(_http_error_message(error)) from None
        except urllib.error.URLError as error:
            raise PrinterUnreachable("Cannot reach %s: %s" % (self.url, error.reason)) from None
        except (OSError, ValueError) as error:
            raise PrinterUnreachable("Cannot reach %s: %s" % (self.url, error)) from None
        try:
            answer = json.loads(raw.decode("utf-8") or "null")
        except ValueError:
            raise MoonrakerError("%s did not answer with JSON" % path) from None
        # Moonraker wraps every answer in {"result": ...}.
        return answer.get("result") if isinstance(answer, dict) and "result" in answer else answer

    def get(self, path: str, query: dict | None = None, timeout=None):
        if query:
            path += "?" + urllib.parse.urlencode(query)
        return self._request("GET", path, timeout=timeout)

    def post(self, path: str, body: dict, timeout=None):
        return self._request("POST", path, body, timeout=timeout)

    # -- the calls the AFC tab makes

    def afc_status(self):
        return self.get("/printer/afc/status")

    def objects_query(self, objects: dict):
        return self.post("/printer/objects/query", {"objects": objects})

    def objects_list(self):
        return self.get("/printer/objects/list")

    def database_namespace(self, namespace: str):
        return self.get("/server/database/item", {"namespace": namespace})

    def gcode(self, script: str, timeout: float = 60.0):
        # A lane load can run for a while before Klipper answers.
        return self.post("/printer/gcode/script", {"script": script}, timeout=timeout)

    def file_metadata(self, filename: str):
        """Moonraker's metadata for a file in the gcodes root: the slicer's time
        estimate, layer count and heights, thumbnails."""
        return self.get("/server/files/metadata", {"filename": filename})

    def webcams(self) -> list:
        """The webcams Moonraker knows (its config and Fluidd's or Mainsail's)."""
        answer = self.get("/server/webcams/list")
        cams = answer.get("webcams") if isinstance(answer, dict) else None
        return cams if isinstance(cams, list) else []

    def print_state(self) -> str:
        """print_stats.state: "standby", "printing", "paused", ... Empty when unknown."""
        status = self.objects_query({"print_stats": ["state"]})
        try:
            return str(status["status"]["print_stats"]["state"] or "")
        except (KeyError, TypeError):
            return ""

    def upload(self, name: str, data: bytes, progress=None, timeout: float = 120.0):
        """Upload a G-code file to Moonraker's gcodes root as `name`.
        `progress(sent, total)` is called as the body goes out; when it
        returns False the upload stops with MoonrakerError("canceled")."""
        boundary = "----AFCPlugin" + uuid.uuid4().hex
        head = ('--%s\r\nContent-Disposition: form-data; name="root"\r\n\r\ngcodes\r\n'
                '--%s\r\nContent-Disposition: form-data; name="file"; filename="%s"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n" % (boundary, boundary, name.replace('"', "_"))).encode("utf-8")
        tail = ("\r\n--%s--\r\n" % boundary).encode("utf-8")
        total = len(head) + len(data) + len(tail)

        def body():
            sent = 0
            for part in [head] + [data[i:i + _CHUNK] for i in range(0, len(data), _CHUNK)] + [tail]:
                if progress is not None and progress(sent, total) is False:
                    raise _Canceled()
                yield part
                sent += len(part)
            if progress is not None:
                progress(total, total)

        headers = {"Accept": "application/json", "Content-Type": "multipart/form-data; boundary=" + boundary,
                   "Content-Length": str(total)}
        if self.api_key:
            headers["X-Api-Key"] = self.api_key
        request = urllib.request.Request(self.url + "/server/files/upload", data=body(), headers=headers, method="POST")
        try:
            with self._open(request, timeout=timeout, context=_ssl_context(self.url)) as answer:
                answer.read()
        except _Canceled:
            raise MoonrakerError("canceled") from None
        except urllib.error.HTTPError as error:
            raise MoonrakerError(_http_error_message(error)) from None
        except urllib.error.URLError as error:
            if isinstance(error.reason, _Canceled):
                raise MoonrakerError("canceled") from None
            raise PrinterUnreachable("Cannot reach %s: %s" % (self.url, error.reason)) from None
        except (OSError, ValueError) as error:
            raise PrinterUnreachable("Cannot reach %s: %s" % (self.url, error)) from None

    def spoolman_spool(self, spool_id: int, timeout: float = 4.0):
        """The spool record through Moonraker's Spoolman proxy."""
        return self.spoolman("GET", "/v1/spool/%d" % spool_id, timeout=timeout)

    def spoolman(self, method: str, path: str, body=None, timeout: float = 4.0):
        """A Spoolman API call ("/v1/...") through Moonraker's Spoolman proxy:
        Spoolman's answer."""
        request = {"request_method": method, "path": path, "use_v2_response": True}
        if body is not None:
            request["body"] = body
        answer = self.post("/server/spoolman/proxy", request, timeout=timeout)
        # use_v2_response: a failed Spoolman call still answers, with its error inside.
        if isinstance(answer, dict) and answer.get("error") is not None:
            raise MoonrakerError("Spoolman: %s" % answer["error"])
        return answer.get("response") if isinstance(answer, dict) and "response" in answer else answer


def fetch_json(url: str, timeout: float = 4.0, opener=None, method: str = "GET", body=None):
    """A JSON request to anywhere (Spoolman's own address): its answer."""
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout, context=_ssl_context(url)) as answer:
            return json.loads(answer.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        raise MoonrakerError("Cannot read %s: %s" % (url, error)) from None


def opens_without_key(url: str, timeout: float = 4.0, opener=None) -> bool:
    """Whether a GET of `url` without the API key succeeds, as OrcaSlicer's
    own requests (a print's thumbnail) are made."""
    try:
        with (opener or urllib.request.urlopen)(urllib.request.Request(url), timeout=timeout, context=_ssl_context(url)):
            return True
    except (OSError, ValueError):
        return False


def _ssl_context(url: str):
    return ssl.create_default_context() if url.startswith("https://") else None


def _http_error_message(error: urllib.error.HTTPError) -> str:
    # Moonraker answers a failed request with {"error": {"code", "message"}}.
    try:
        body = json.loads(error.read().decode("utf-8"))
        message = body.get("error", {}).get("message") if isinstance(body.get("error"), dict) else body.get("error")
        if message:
            return str(message)
    except (OSError, ValueError, AttributeError):
        pass
    if error.code == 401 or error.code == 403:
        return "The printer refused the request (%d); check the API key in the printer's connection settings" % error.code
    if error.code == 404:
        return "Not found on the printer (%s)" % error.url
    return "The printer answered %d %s" % (error.code, error.reason)
