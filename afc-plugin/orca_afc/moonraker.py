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

    def print_state(self) -> str:
        """print_stats.state: "standby", "printing", "paused", ... Empty when unknown."""
        status = self.objects_query({"print_stats": ["state"]})
        try:
            return str(status["status"]["print_stats"]["state"] or "")
        except (KeyError, TypeError):
            return ""

    def spoolman_spool(self, spool_id: int, timeout: float = 4.0):
        """The spool record through Moonraker's Spoolman proxy."""
        answer = self.post("/server/spoolman/proxy",
                           {"request_method": "GET", "path": "/v1/spool/%d" % spool_id, "use_v2_response": True},
                           timeout=timeout)
        # use_v2_response: a failed Spoolman call still answers, with its error inside.
        if isinstance(answer, dict) and answer.get("error") is not None:
            raise MoonrakerError("Spoolman: %s" % answer["error"])
        return answer


def fetch_json(url: str, timeout: float = 4.0, opener=None):
    """GET a JSON document from anywhere (Spoolman's own address)."""
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=timeout, context=_ssl_context(url)) as answer:
            return json.loads(answer.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        raise MoonrakerError("Cannot read %s: %s" % (url, error)) from None


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
