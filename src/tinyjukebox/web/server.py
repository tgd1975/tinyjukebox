"""HTTPS server for the rest api and the web page (python standard library only - runs on every Raspberry Pi)."""

import hashlib
import http.server
import json
import logging
import math
import os
import re
import socketserver
import ssl
import threading
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol

from tinyjukebox.backup import Incomplete
from tinyjukebox.backups import Backups
from tinyjukebox.control import BoxControl, CatalogControl
from tinyjukebox.core import unexpected
from tinyjukebox.core.logging import get_child
from tinyjukebox.data.targets import Targets
from tinyjukebox.errors import (
    ApiError,
    BadRequest,
    Conflict,
    Forbidden,
    LengthRequired,
    MethodNotAllowed,
    NotFound,
    PayloadTooLarge,
    TooManyRequests,
    Unauthorized,
)
from tinyjukebox.restore import Restore
from tinyjukebox.sync import acting_as
from tinyjukebox.web.auth import (
    MIN_PASSWORD_LENGTH,
    ROLES,
    LoginThrottle,
    Session,
    Sessions,
    UsersFile,
    has_role,
    hash_password,
)
from tinyjukebox.web.certificates import create_ssl_context, ensure_certificate
from tinyjukebox.web.logbuffer import LogBuffer
from tinyjukebox.web.uploads import Readable, Uploads

__all__ = ["DEFAULTS", "MAX_CONNECTIONS", "NO_CONTENT", "Request", "Response", "RestApi", "Route", "Writable"]


class Writable(Protocol):
    """The part of the response stream (``wfile``) that event streams use."""

    def write(self, data: bytes, /) -> int: ...

    def flush(self) -> None: ...


WEB_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}
# the texts of the web page, one file per language (index.html and app.js have none of their own)
STATIC_FILES.update(
    (f"/texts/{name}", (os.path.join("texts", name), "application/json; charset=utf-8"))
    for name in sorted(os.listdir(os.path.join(WEB_FOLDER, "texts")))
    if name.endswith(".json")
)
QUIET_PATHS = ("/api/status", "/api/logs", "/api/events", "/api/box/hardware")
SESSION_COOKIE = "tinyjukebox_session"
IDLE_HEADER = "X-TinyJukebox-Idle"  # the page: seconds since the user did something last (see Sessions.get)
CSRF_HEADER = "X-TinyJukebox-Request"  # cross site requests cannot set custom headers without a cors preflight
MAX_JSON_BODY = 64 * 1024
MAX_IMAGE_BODY = 10 * 2**20
MAX_USERNAME_LENGTH = 64
MAX_DISCARD_BODY = 16 * 2**20  # unread request bodies up to this size are read and discarded
MAX_CONNECTIONS = 32  # concurrent connections - each one has its own thread
EVENT_INTERVAL = 0.2  # seconds between two checks of an event stream for changes
STATUS_REFRESH = 5.0  # an event stream sends the status at least this often (playback position, keeps the line open)

SECURITY_HEADERS = {
    "Strict-Transport-Security": "max-age=31536000",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data: blob:; object-src 'none'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}

DEFAULT_FOLDER = os.path.join("~", ".config", "tinyjukebox")
DEFAULTS: dict[str, Any] = {
    "host": "0.0.0.0",  # noqa: S104 - the web page is meant to be reached from the local network
    "port": 8443,
    "certificate": os.path.join(DEFAULT_FOLDER, "tinyjukebox.crt"),
    "private-key": os.path.join(DEFAULT_FOLDER, "tinyjukebox.key"),
    "users-file": os.path.join(DEFAULT_FOLDER, "users.yaml"),
    "session-timeout": 7 * 24 * 3600,
    "idle-timeout": 30 * 60,
    "max-upload-size": 500,
    "hostnames": [],
}
PATH_KEYS = ["certificate", "private-key", "users-file"]


class Request:
    """What a route handler gets: path parameters, query, session and the request body."""

    def __init__(
        self, handler: "RequestHandler", params: dict[str, str], session: Session | None, token: str | None
    ) -> None:
        self._handler = handler
        self.params = params
        self.session = session
        self.token = token
        self.query = urllib.parse.parse_qs(urllib.parse.urlsplit(handler.path).query)
        self.client_address = str(handler.client_address[0])
        self.path = urllib.parse.urlsplit(handler.path).path
        self.method = str(handler.command)
        self.headers = handler.headers
        self.context: dict[str, Any] = {}  # what a guard found out, e.g. the box that asks

    def query_flag(self, name: str) -> bool:
        return self.query.get(name, ["0"])[-1].lower() in ("1", "true", "yes")

    def query_value(self, name: str, default: str) -> str:
        return self.query.get(name, [default])[-1]

    def content_length(self) -> int:
        if "chunked" in self._handler.headers.get("Transfer-Encoding", "").lower():
            raise LengthRequired("chunked transfer encoding is not supported - send a Content-Length.")
        try:
            length = int(self._handler.headers.get("Content-Length", 0))
        except ValueError:
            raise BadRequest("invalid Content-Length.") from None
        if length < 0:
            raise BadRequest("invalid Content-Length.")
        return length

    def json(self, limit: int = MAX_JSON_BODY) -> dict[str, Any]:
        length = self.content_length()
        if length > limit:
            raise PayloadTooLarge("request body too large.")
        body = self._handler.rfile.read(length) if length else b""
        self._handler.body_consumed = True
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            raise BadRequest("request body must be valid json.") from None
        if not isinstance(data, dict):
            raise BadRequest("request body must be a json object.")
        return data

    @property
    def stream(self) -> Readable:
        self._handler.body_consumed = True
        return self._handler.rfile

    def body(self) -> "Body":
        """The body as a stream of its length, for a body that is read while it arrives."""
        return Body(self, self.content_length())

    def close_connection(self) -> None:
        """The connection is closed after the answer: the body was not read to its end."""
        self._handler.close_connection = True

    def save(self, filename: str) -> str:
        """Store the body under ``filename`` and return its sha256 checksum."""
        remaining = self.content_length()
        stream = self.stream
        digest = hashlib.sha256()
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        with open(filename, "wb") as outfile:
            while remaining > 0:
                chunk = stream.read(min(remaining, 1 << 16))
                if not chunk:
                    raise BadRequest("the request ended early.")
                outfile.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
            outfile.flush()
            os.fsync(outfile.fileno())
        return digest.hexdigest()


class Response:
    def __init__(
        self,
        status: int = 200,
        data: Any = None,
        body: bytes | None = None,
        content_type: str = "application/json",
        headers: Mapping[str, str] | None = None,
        stream: Callable[[Writable], None] | None = None,
        file: str | None = None,
    ) -> None:
        """``stream``: writes the body itself until it returns (no length - the connection is closed afterwards).
        ``file``: the body is this file; the client may ask for the rest of it (``Range: bytes=N-``)."""
        self.status = status
        self.stream = stream
        self.file = file
        if file is not None:
            content_type = "application/octet-stream"
        self.headers: dict[str, str] = dict(headers or {})
        if body is None and data is not None:
            body = json.dumps(data).encode("utf-8")
        self.body = body if body is not None else b""
        if self.body or stream is not None or file is not None or status != 204:
            self.headers.setdefault("Content-Type", content_type)


NO_CONTENT = Response(204)


class Body:
    """The body of a request, ``size`` bytes, read while it arrives (:meth:`Request.body`)."""

    def __init__(self, request: Request, size: int) -> None:
        self._request = request
        self._stream = request.stream
        self.size = size
        self.left = size

    def read(self, size: int = -1) -> bytes:
        if self.left <= 0:
            return b""
        data = self._stream.read(self.left if size < 0 else min(size, self.left))
        self.left -= len(data)
        if not data:
            raise BadRequest("the request ended early.")
        return data

    def finish(self) -> None:
        """After an answer that does not need the rest: a small rest is read, otherwise the connection is closed -
        an unread body would be taken for the next request."""
        try:
            if self.left > MAX_DISCARD_BODY:
                raise BadRequest("too much is left")
            while self.left > 0:
                self.read(min(self.left, 1 << 16))
        except BadRequest:
            self._request.close_connection()


Handler = Callable[[Request], Response]


class Route:
    def __init__(self, method: str, pattern: str, handler: Handler, role: str | None) -> None:
        self.method = method
        self.regex = re.compile("^" + re.sub(r"{(\w+)}", r"(?P<\1>[^/]+)", pattern) + "$")
        self.handler = handler
        self.role = role  # None: no login needed; GUARDED: the guard of the path decides


GUARDED = "guarded"
"""Role of a route that is not for users of the page: the guard of its path decides who may ask (``guards`` of
:class:`RestApi`)."""


class RequestHandler(http.server.BaseHTTPRequestHandler):
    server_version = "TinyJukebox"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    api: "RestApi"  # set by RestApi
    timeout = 60  # seconds without data from the client - also for uploads
    disable_nagle_algorithm = True  # headers and body are written separately - no 40 ms delayed-ack stall
    body_consumed = False

    def log_request(self, code: int | str = "-", size: int | str = "-") -> None:
        # the web page polls these every second (or more often) - successful polls would only flood the log
        path = urllib.parse.urlsplit(self.path).path
        if path in QUIET_PATHS and str(code).startswith("2"):
            return
        super().log_request(code, size)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - signature of the base class
        self.api.logger.debug("%s - %s", self.client_address[0], format % args)

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PUT(self) -> None:
        self._dispatch("PUT")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        self.body_consumed = False
        path = urllib.parse.urlsplit(self.path).path
        try:
            if method == "GET" and path in STATIC_FILES:
                response = self._static(*STATIC_FILES[path])
            else:
                response = self.api.handle(self, method, path)
        except ApiError as e:
            response = Response(e.status, dict({"error": e.message}, **e.details))
            if isinstance(e, TooManyRequests):
                response.headers["Retry-After"] = str(e.details.get("retry_after", 60))
        except Exception as error:
            self.api.logger.exception("%s %s failed", method, path)
            unexpected.report(error, f"request {method} {path}")
            response = Response(500, {"error": "internal error."})
        self._send(response)

    def _request_has_body(self) -> bool:
        try:
            return int(self.headers.get("Content-Length", 0) or 0) > 0 or "Transfer-Encoding" in self.headers
        except ValueError:
            return True

    def _discard_body(self) -> None:
        """An unread request body would be taken for the next request. Small bodies are read (closing the connection
        with unread data would reset it before the client reads the response), otherwise the connection is closed."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except ValueError:
            length = -1
        if "Transfer-Encoding" not in self.headers and 0 <= length <= MAX_DISCARD_BODY:
            self.rfile.read(length)
        else:
            self.close_connection = True

    def _static(self, filename: str, content_type: str) -> Response:
        with open(os.path.join(WEB_FOLDER, filename), "rb") as f:
            return Response(200, body=f.read(), content_type=content_type, headers={"Cache-Control": "no-cache"})

    def _send(self, response: Response) -> None:
        if not self.body_consumed and self._request_has_body():
            self._discard_body()
        self.send_response(response.status)
        for key, value in SECURITY_HEADERS.items():
            self.send_header(key, value)
        response.headers.setdefault("Cache-Control", "no-store")
        for key, value in response.headers.items():
            self.send_header(key, value)
        if response.stream is not None:
            self.close_connection = True
            self.send_header("Connection", "close")
            self.end_headers()
            response.stream(self.wfile)
            return
        if response.file is not None:
            self._send_file(response.file)
            return
        self.send_header("Content-Length", str(len(response.body)))
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(response.body)

    def _send_file(self, filename: str) -> None:
        """The headers so far are sent; now the file, or the rest of it that the client asks for."""
        size = os.path.getsize(filename)
        match = re.fullmatch(r"bytes=(\d+)-", self.headers.get("Range", ""))
        start = min(int(match.group(1)), size) if match else 0
        if start:
            self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
        self.send_header("Content-Length", str(size - start))
        self.end_headers()
        with open(filename, "rb") as infile:
            infile.seek(start)
            while chunk := infile.read(1 << 16):
                self.wfile.write(chunk)


class _Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[RequestHandler],
        ssl_context: ssl.SSLContext,
        max_connections: int = MAX_CONNECTIONS,
    ) -> None:
        self._ssl_context = ssl_context
        self._connections = threading.BoundedSemaphore(max_connections)
        super().__init__(address, handler)

    def process_request(self, request: Any, client_address: Any) -> None:
        # one thread per connection - but not without limit (slow or idle clients would exhaust the box)
        if not self._connections.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._connections.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._connections.release()

    def finish_request(self, request: Any, client_address: Any) -> None:
        # tls handshake in the worker thread - a slow client must not block the accept loop
        try:
            request.settimeout(RequestHandler.timeout)
            tls_socket = self._ssl_context.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            super().finish_request(tls_socket, client_address)
        finally:
            tls_socket.close()

    def handle_error(self, request: Any, client_address: Any) -> None:
        pass  # connection errors of single clients are not interesting


def _without_position(status: dict[str, Any]) -> dict[str, Any]:
    """``status`` without the playback position (``album/playback/position`` and the stored position
    ``tag/activity/track_time``) - it changes all the time while playing."""

    def without(entry: Any, key: str, inner: str) -> Any:
        if not isinstance(entry, dict) or not isinstance(entry.get(key), dict):
            return entry
        return {**entry, key: {k: v for k, v in entry[key].items() if k != inner}}

    return {**status, "album": without(status.get("album"), "playback", "position"),
            "tag": without(status.get("tag"), "activity", "track_time")}  # fmt: skip


def _write_event(out: Writable, event: str, data: Any) -> None:
    out.write(f"event: {event}\ndata: {json.dumps(data)}\n\n".encode())
    out.flush()


class RestApi:
    """The rest api and the web page (section ``rest-api`` of the config, see :data:`DEFAULTS`)."""

    def __init__(
        self,
        api_config: Mapping[str, Any],
        control: CatalogControl,
        targets: Targets,
        logger: logging.Logger,
        log_buffer: LogBuffer | None = None,
        routes: Sequence[Route] = (),
        guards: Mapping[str, Callable[[Request], None]] | None = None,
        mode: str = "box",
    ) -> None:
        """``log_buffer``: :class:`LogBuffer` that is already attached to the logger - otherwise the api attaches
        its own while it is running.

        ``routes``: further routes, e.g. the interface for the boxes of a library. ``guards``: for the paths that
        begin with a key, its guard decides who may ask instead of the login - it raises an error for a request that
        may not, also for a path that does not exist. ``mode``: what the page is the page of (``box``/``library``).
        """
        self.logger = get_child(logger, type(self).__name__)
        self._root_logger = logger
        self._config: dict[str, Any] = dict(DEFAULTS, **api_config)
        for key in PATH_KEYS:
            self._config[key] = os.path.expanduser(self._config[key])
        self._own_log_buffer = log_buffer is None
        self.log_buffer = log_buffer if log_buffer is not None else LogBuffer()
        self.control = control
        self.uploads = Uploads(targets, control.playlist_filename, int(self._config["max-upload-size"] * 2**20), logger)
        self.users = UsersFile(self._config["users-file"], self.logger)
        self.sessions = Sessions(self._config["session-timeout"], idle_timeout=self._config["idle-timeout"])
        self.throttle = LoginThrottle()
        self.mode = mode
        # the page of the library has a cookie of its own: a box and the library on one host name (the PC, with two
        # ports) would otherwise log each other out - a cookie belongs to the host, whatever the port
        self._cookie = SESSION_COOKIE if mode == "box" else f"{SESSION_COOKIE}_{mode}"
        self._guards = dict(guards or {})
        self._routes = [*self._create_routes(), *routes]
        self._server: _Server | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()  # ends the event streams
        self.fingerprint: str | None = None

    # --- routes ------------------------------------------------------------------------------------------------

    def _create_routes(self) -> list[Route]:
        c = self.control
        u = self.uploads

        def done(action: Callable[[], object]) -> Response:
            action()
            return NO_CONTENT

        routes = [
            Route("POST", "/api/login", self._login, None),
            Route("POST", "/api/logout", self._logout, None),
            Route("GET", "/api/session", self._session, "user"),
            Route("PUT", "/api/session/password", self._change_own_password, "user"),
            Route("GET", "/api/users", self._list_users, "admin"),
            Route("POST", "/api/users", self._add_user, "admin"),
            Route("PUT", "/api/users/{name}/password", self._set_password, "admin"),
            Route("DELETE", "/api/users/{name}", self._remove_user, "admin"),
            Route("GET", "/api/status", lambda r: Response(data=c.status()), "user"),
            Route("GET", "/api/events", self._events, "user"),
            Route("GET", "/api/tags", lambda r: Response(data=c.list_tags(r.query_flag("unmapped"))), "user"),
            Route("POST", "/api/tags", self._create_tag, "admin"),
            Route("GET", "/api/logs", self._logs, "admin"),
            Route("GET", "/api/tags/{uid}", lambda r: Response(data=c.get_tag(r.params["uid"])), "user"),
            Route(
                "PATCH",
                "/api/tags/{uid}",
                lambda r: Response(data=c.set_tag_name(r.params["uid"], r.json().get("name"))),
                "admin",
            ),
            Route(
                "PUT",
                "/api/tags/{uid}/album",
                lambda r: Response(data=c.link_tag_album(r.params["uid"], r.json().get("album_id"))),
                "admin",
            ),
            Route("DELETE", "/api/tags/{uid}/album", lambda r: Response(data=c.unlink_tag(r.params["uid"])), "admin"),
            Route(
                "PUT",
                "/api/tags/{uid}/radio",
                lambda r: Response(data=c.link_tag_radio(r.params["uid"], r.json().get("radio_id"))),
                "admin",
            ),
            Route("DELETE", "/api/tags/{uid}/radio", lambda r: Response(data=c.unlink_tag(r.params["uid"])), "admin"),
            Route(
                "PUT",
                "/api/tags/{uid}/spotify",
                lambda r: Response(data=c.link_tag_spotify(r.params["uid"], r.json().get("spotify_id"))),
                "admin",
            ),
            Route("DELETE", "/api/tags/{uid}/spotify", lambda r: Response(data=c.unlink_tag(r.params["uid"])), "admin"),
            Route("DELETE", "/api/tags/{uid}", lambda r: done(lambda: c.remove_tag(r.params["uid"])), "admin"),
            Route("GET", "/api/albums", lambda r: Response(data=c.list_albums(r.query_flag("unmapped"))), "user"),
            Route("POST", "/api/albums", self._add_album, "admin"),
            Route("GET", "/api/albums/new", lambda r: Response(data=c.new_albums()), "admin"),
            Route(
                "POST",
                "/api/albums/new",
                lambda r: Response(data=c.add_new_albums(r.json().get("titles"))),
                "admin",
            ),
            Route("GET", "/api/albums/{id}", lambda r: Response(data=c.get_album(r.params["id"])), "user"),
            Route(
                "PATCH",
                "/api/albums/{id}",
                lambda r: Response(data=c.update_album(r.params["id"], r.json().get("title"))),
                "admin",
            ),
            Route("GET", "/api/albums/{id}/cover", self._cover, "user"),
            Route("PUT", "/api/albums/{id}/cover", self._set_cover, "admin"),
            Route(
                "POST",
                "/api/albums/{id}/playlist",
                lambda r: Response(data=c.rebuild_playlist(r.params["id"])),
                "admin",
            ),
            Route(
                "DELETE",
                "/api/albums/{id}",
                lambda r: done(lambda: c.delete_album(r.params["id"], r.query_flag("delete_files"))),
                "admin",
            ),
            Route("GET", "/api/radios", lambda r: Response(data=c.list_radios()), "user"),
            Route("POST", "/api/radios", self._add_radio, "admin"),
            Route("GET", "/api/radios/{id}", lambda r: Response(data=c.get_radio(r.params["id"])), "user"),
            Route("PATCH", "/api/radios/{id}", self._update_radio, "admin"),
            Route("DELETE", "/api/radios/{id}", lambda r: done(lambda: c.delete_radio(r.params["id"])), "admin"),
            Route("GET", "/api/radios/{id}/cover", self._radio_cover, "user"),
            Route("PUT", "/api/radios/{id}/cover", self._set_radio_cover, "admin"),
            Route("GET", "/api/spotify", lambda r: Response(data=c.list_spotify()), "user"),
            Route("POST", "/api/spotify", self._add_spotify, "admin"),
            Route(
                "POST", "/api/spotify-lookup", lambda r: Response(data=c.look_up_spotify(r.json().get("link"))), "admin"
            ),
            Route("GET", "/api/spotify/{id}", lambda r: Response(data=c.get_spotify(r.params["id"])), "user"),
            Route("PATCH", "/api/spotify/{id}", self._update_spotify, "admin"),
            Route("DELETE", "/api/spotify/{id}", lambda r: done(lambda: c.delete_spotify(r.params["id"])), "admin"),
            Route("GET", "/api/spotify/{id}/cover", self._spotify_cover, "user"),
            Route("PUT", "/api/spotify/{id}/cover", self._set_spotify_cover, "admin"),
            Route("GET", "/api/activities", lambda r: Response(data=c.list_activities()), "user"),
            Route("POST", "/api/activities/rebuild", lambda r: Response(data=c.rebuild_activities()), "admin"),
            Route("POST", "/api/sanity-check", lambda r: Response(data=c.sanity_checks()), "admin"),
            Route("GET", "/api/backup", self._backup, "admin"),
            Route("GET", "/api/uploads", lambda r: Response(data=u.list()), "admin"),
            Route("POST", "/api/uploads", lambda r: Response(201, u.create(r.json().get("name"))), "admin"),
            Route("GET", "/api/uploads/{id}", lambda r: Response(data=u.get(r.params["id"])), "admin"),
            Route("DELETE", "/api/uploads/{id}", lambda r: done(lambda: u.abort(r.params["id"])), "admin"),
            Route("PUT", "/api/uploads/{id}/files/{filename}", self._upload_file, "admin"),
            Route(
                "DELETE",
                "/api/uploads/{id}/files/{filename}",
                lambda r: done(lambda: u.remove_file(r.params["id"], r.params["filename"])),
                "admin",
            ),
            Route("POST", "/api/uploads/{id}/finish", self._finish_upload, "admin"),
        ]
        # the backups that are made here by a schedule (tinyjukebox.backups); GET /api/backup sends one at once
        routes += self._backups_routes("/api/backups" if isinstance(c, BoxControl) else "/api/library/backups")
        routes += self._restore_routes()
        if isinstance(c, BoxControl):
            routes += self._box_routes(c)
        return routes

    def _restore_routes(self) -> list[Route]:
        """Putting a backup back (tinyjukebox.restore): an upload, or a backup of the folders of the backups, is
        unpacked beside the data; when it is confirmed the program starts again with it."""

        def restore() -> Restore:
            if self.control.restore is None:
                raise NotFound("no backup can be put back here.")
            return self.control.restore

        def status() -> dict[str, Any]:
            c = self.control
            # a box with a library: the library wins at the next synchronization - the page says so before
            attached = isinstance(c, BoxControl) and c.sync is not None and c.sync.attached
            return {**restore().status(), "kind": restore().kind, "library": attached}

        def upload(request: Request) -> Response:
            body = request.body()
            if not body.size:
                raise BadRequest("the body must be a backup.", code="restore.incomplete", reason="nothing was sent")
            try:
                restore().stage(body, body.size)  # type: ignore[arg-type]
            except Incomplete as error:
                body.finish()
                raise BadRequest(f"no backup: {error}", code="restore.incomplete", reason=str(error)) from None
            except Exception:
                body.finish()
                raise
            return Response(data=status())

        def stored(request: Request) -> Response:
            if self.control.backups is None:
                raise NotFound("no backups are kept here.")
            name = request.json().get("name")
            restore().stage_in_background(self.control.backups.path(str(name)))
            return Response(202, status())

        def confirm(request: Request) -> Response:
            restore().confirm()
            return Response(202, status())

        def discard(request: Request) -> Response:
            restore().discard()
            return Response(data=status())

        return [
            Route("GET", "/api/restore", lambda r: Response(data=status()), "admin"),
            Route("PUT", "/api/restore", upload, "admin"),
            Route("POST", "/api/restore", stored, "admin"),
            Route("POST", "/api/restore/confirm", confirm, "admin"),
            Route("DELETE", "/api/restore", discard, "admin"),
        ]

    def _backups_routes(self, prefix: str) -> list[Route]:
        """The schedules of the backups and the backups that are kept: the same for a box and the library."""

        def backups() -> Backups:
            if self.control.backups is None:
                raise NotFound("no backups are kept here.")
            return self.control.backups

        def ask(request: Request) -> Response:
            backups().ask(bool(request.json().get("albums", False)))
            return Response(202, backups().status())

        def stored(request: Request) -> Response:
            name = request.params["name"]
            headers = {"Content-Disposition": f'attachment; filename="{name}"'}
            return Response(200, headers=headers, file=backups().path(name))

        def remove(request: Request) -> Response:
            backups().remove(request.params["name"])
            return NO_CONTENT

        return [
            Route("GET", prefix, lambda r: Response(data=backups().status()), "admin"),
            Route("PUT", prefix, lambda r: Response(data=backups().set(r.json())), "admin"),
            Route("POST", prefix, ask, "admin"),
            Route("GET", f"{prefix}/{{name}}", stored, "admin"),
            Route("DELETE", f"{prefix}/{{name}}", remove, "admin"),
        ]

    def _box_routes(self, c: BoxControl) -> list[Route]:
        """What only a box has: virtual reader, radio test, player, volume, headset, buttons."""

        def done(action: Callable[[], object]) -> Response:
            action()
            return NO_CONTENT

        return [
            Route("GET", "/api/box", lambda r: Response(data=c.library_info()), "user"),
            Route("PUT", "/api/box/library", self._set_library, "admin"),
            Route("DELETE", "/api/box/library", lambda r: Response(data=c.unlink_library()), "admin"),
            Route("PUT", "/api/box/name", lambda r: Response(data=c.set_box_name(r.json().get("name"))), "admin"),
            # what was played on this box is for everybody with a login on its page, as in the library
            Route("GET", "/api/box/statistics", lambda r: Response(data=c.statistics()), "user"),
            Route("GET", "/api/box/system", lambda r: Response(data=c.system_info()), "admin"),
            Route("GET", "/api/box/hardware", lambda r: Response(data=c.hardware_info()), "admin"),
            Route("POST", "/api/box/system/{command}", self._system_command, "admin"),
            Route("POST", "/api/rfid", lambda r: done(lambda: c.place_tag(r.json().get("uid"))), "user"),
            Route("DELETE", "/api/rfid", lambda r: done(c.remove_placed_tag), "user"),
            Route("POST", "/api/radio-test", self._test_radio, "admin"),
            Route(
                "POST",
                "/api/spotify-test",
                lambda r: Response(202, c.test_spotify(r.json().get("id"), r.json().get("link"))),
                "admin",
            ),
            Route("DELETE", "/api/spotify-test", lambda r: done(c.stop_radio_test), "admin"),
            Route("GET", "/api/spotify-account", lambda r: Response(data=c.spotify_account()), "admin"),
            Route(
                "PUT",
                "/api/spotify-account/key",
                lambda r: Response(data=c.set_spotify_key(r.json().get("key"))),
                "admin",
            ),
            Route(
                "PUT",
                "/api/spotify-account/speaker",
                lambda r: Response(data=c.set_spotify_speaker(r.json().get("enabled"))),
                "admin",
            ),
            Route("POST", "/api/spotify-account", lambda r: Response(202, c.connect_spotify()), "admin"),
            Route("DELETE", "/api/spotify-account", lambda r: Response(data=c.disconnect_spotify()), "admin"),
            Route("GET", "/api/spotify-player", lambda r: Response(data=c.spotify_player()), "admin"),
            Route("POST", "/api/spotify-player", lambda r: Response(202, c.update_spotify_player()), "admin"),
            Route("DELETE", "/api/radio-test", lambda r: done(c.stop_radio_test), "admin"),
            Route("POST", "/api/player/next", lambda r: done(c.track_next), "user"),
            Route("POST", "/api/player/prev", lambda r: done(c.track_prev), "user"),
            Route("POST", "/api/player/restart", lambda r: done(c.restart_album), "user"),
            Route("POST", "/api/player/pause", lambda r: done(c.pause), "user"),
            Route("POST", "/api/player/play", lambda r: done(c.play), "user"),
            Route("GET", "/api/volume", lambda r: Response(data=c.get_volume()), "user"),
            Route("PUT", "/api/volume", lambda r: Response(data=c.set_volume(r.json().get("volume"))), "user"),
            # start value and maximum (hearing protection): admins only
            Route("PUT", "/api/volume/limits/{profile}", self._set_volume_limits, "admin"),
            Route("GET", "/api/bluetooth", lambda r: Response(data=c.bluetooth_info()), "user"),
            Route("POST", "/api/bluetooth/pair", lambda r: Response(202, c.bluetooth_pair()), "admin"),
            Route("DELETE", "/api/bluetooth/device", lambda r: done(c.bluetooth_unpair), "admin"),
            Route("POST", "/api/bluetooth/virtual", self._virtual_headset, "user"),
            Route(
                "POST",
                "/api/buttons/{button}",
                lambda r: done(lambda: c.press_button(r.params["button"], r.json().get("action", "press"))),
                "user",
            ),
        ]

    @property
    def _box(self) -> BoxControl:
        """The control of a box - for the handlers of the routes that only a box has."""
        assert isinstance(self.control, BoxControl)  # noqa: S101 - these routes exist for a box only
        return self.control

    def _login(self, request: Request) -> Response:
        address = request.client_address
        retry_after = self.throttle.retry_after(address)
        if retry_after:
            raise TooManyRequests("too many failed logins - try again later.", retry_after=retry_after)
        data = request.json()
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            raise BadRequest("username and password must be provided.")
        role = self.users.authenticate(username, password)
        if role is None:
            self.throttle.failed(address)
            self.logger.warning("failed login for '%s' from %s.", username, address)
            raise Unauthorized("wrong username or password.")
        self.throttle.succeeded(address)
        token = self.sessions.create(username, role)
        self.logger.info("'%s' (%s) logged in from %s.", username, role, address)
        cookie = f"{self._cookie}={token}; Path=/; Secure; HttpOnly; SameSite=Strict"
        return Response(
            data={
                "username": username,
                "role": role,
                "token": token,
                "mode": self.mode,
                "idle_timeout": self.sessions.idle_timeout,
            },
            headers={"Set-Cookie": cookie},
        )

    def _logout(self, request: Request) -> Response:
        if request.token:
            self.sessions.remove(request.token)
        cookie = f"{self._cookie}=; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=0"
        return Response(204, headers={"Set-Cookie": cookie})

    def _session(self, request: Request) -> Response:
        assert request.session is not None  # noqa: S101 - routes with a role always have a session
        return Response(
            data={
                "username": request.session.username,
                "role": request.session.role,
                "mode": self.mode,
                "idle_timeout": self.sessions.idle_timeout,
            }
        )

    def _list_users(self, request: Request) -> Response:
        roles = self.users.roles()
        return Response(data=[{"username": name, "role": roles[name]} for name in sorted(roles)])

    @staticmethod
    def _new_password_hash(data: dict[str, Any]) -> str:
        password = data.get("password")
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise BadRequest(
                f"password must have at least {MIN_PASSWORD_LENGTH} characters.",
                code="password.too_short",
                min=MIN_PASSWORD_LENGTH,
            )
        return hash_password(password)

    def _add_user(self, request: Request) -> Response:
        assert request.session is not None  # noqa: S101 - routes with a role always have a session
        data = request.json()
        username, role = data.get("username"), data.get("role", "user")
        if (
            not isinstance(username, str)
            or not username
            or username != username.strip()
            or ":" in username
            or len(username) > MAX_USERNAME_LENGTH
        ):
            raise BadRequest("invalid user name.", code="user.invalid_name")
        if role not in ROLES:
            raise BadRequest(f"role must be one of {', '.join(ROLES)}.")
        password_hash = self._new_password_hash(data)

        def add(users: dict[str, dict[str, Any]]) -> None:
            if username in users:
                raise Conflict(f"user '{username}' exists already.", code="user.exists", name=username)
            users[username] = {"password-hash": password_hash, "role": role}

        self.users.change(add)
        self.logger.info("'%s' added the user '%s' (%s).", request.session.username, username, role)
        return Response(201, {"username": username, "role": role})

    def _set_password(self, request: Request) -> Response:
        assert request.session is not None  # noqa: S101 - routes with a role always have a session
        username = request.params["name"]
        self._store_password(request, username, self._new_password_hash(request.json()))
        self.logger.info("'%s' changed the password of '%s'.", request.session.username, username)
        return NO_CONTENT

    def _change_own_password(self, request: Request) -> Response:
        """Every user may change the own password - with the current one, so that an open browser or a stolen
        session does not suffice. Wrong current passwords count as failed logins."""
        assert request.session is not None  # noqa: S101 - routes with a role always have a session
        username = request.session.username
        address = request.client_address
        retry_after = self.throttle.retry_after(address)
        if retry_after:
            raise TooManyRequests("too many failed logins - try again later.", retry_after=retry_after)
        data = request.json()
        current = data.get("current_password")
        if not isinstance(current, str):
            raise BadRequest("current_password must be provided.")
        password_hash = self._new_password_hash(data)
        # not Unauthorized: the session is still valid
        if self.users.authenticate(username, current) is None:
            self.throttle.failed(address)
            self.logger.warning("'%s' entered a wrong current password from %s.", username, address)
            raise Forbidden("wrong current password.")
        self.throttle.succeeded(address)
        self._store_password(request, username, password_hash)
        self.logger.info("'%s' changed the own password.", username)
        return NO_CONTENT

    def _store_password(self, request: Request, username: str, password_hash: str) -> None:
        def set_password(users: dict[str, dict[str, Any]]) -> None:
            if username not in users:
                raise NotFound(f"unknown user '{username}'.", code="user.unknown", name=username)
            users[username]["password-hash"] = password_hash

        self.users.change(set_password)
        # whoever is logged in with the old password has to log in again - except the one who just changed it
        self.sessions.remove_user(username, keep=request.token)

    def _remove_user(self, request: Request) -> Response:
        assert request.session is not None  # noqa: S101 - routes with a role always have a session
        username = request.params["name"]
        # only admins get here - so at least one admin always remains
        if username == request.session.username:
            raise Conflict("you cannot remove yourself.", code="user.self")

        def remove(users: dict[str, dict[str, Any]]) -> None:
            if username not in users:
                raise NotFound(f"unknown user '{username}'.", code="user.unknown", name=username)
            del users[username]

        self.users.change(remove)
        self.sessions.remove_user(username)
        self.logger.info("'%s' removed the user '%s'.", request.session.username, username)
        return NO_CONTENT

    def _create_tag(self, request: Request) -> Response:
        data = request.json()
        return Response(
            201,
            self.control.create_tag(
                data.get("uid"), data.get("name"), data.get("album_id"), data.get("radio_id"), data.get("spotify_id")
            ),
        )

    def _add_radio(self, request: Request) -> Response:
        data = request.json()
        return Response(201, self.control.add_radio(data.get("name"), data.get("url")))

    def _test_radio(self, request: Request) -> Response:
        data = request.json()
        return Response(202, self._box.test_radio(data.get("url"), data.get("name")))

    def _update_radio(self, request: Request) -> Response:
        data = request.json()
        return Response(data=self.control.update_radio(request.params["id"], data.get("name"), data.get("url")))

    def _radio_cover(self, request: Request) -> Response:
        data, mime = self.control.get_radio_cover(request.params["id"])
        # the address names the image (page: "?v=<name>"), so a new cover is never the cached one
        return Response(body=data, content_type=mime, headers={"Cache-Control": "private, max-age=300"})

    def _set_radio_cover(self, request: Request) -> Response:
        length = request.content_length()
        if length > MAX_IMAGE_BODY:
            raise PayloadTooLarge("image is too large.", code="image.too_large")
        return Response(data=self.control.set_radio_cover(request.params["id"], request.stream.read(length)))

    def _add_spotify(self, request: Request) -> Response:
        data = request.json()
        return Response(201, self.control.add_spotify(data.get("link"), data.get("name")))

    def _update_spotify(self, request: Request) -> Response:
        data = request.json()
        return Response(data=self.control.update_spotify(request.params["id"], data.get("name"), data.get("link")))

    def _spotify_cover(self, request: Request) -> Response:
        data, mime = self.control.get_spotify_cover(request.params["id"])
        return Response(body=data, content_type=mime, headers={"Cache-Control": "private, max-age=300"})

    def _set_spotify_cover(self, request: Request) -> Response:
        length = request.content_length()
        if length > MAX_IMAGE_BODY:
            raise PayloadTooLarge("image is too large.", code="image.too_large")
        return Response(data=self.control.set_spotify_cover(request.params["id"], request.stream.read(length)))

    def _set_volume_limits(self, request: Request) -> Response:
        data = request.json()
        return Response(
            data=self._box.set_volume_limits(
                request.params["profile"], data.get("default_volume"), data.get("max_volume")
            )
        )

    def _set_library(self, request: Request) -> Response:
        data = request.json()
        return Response(data=self._box.set_library(data.get("url"), data.get("bootstrap")))

    def _system_command(self, request: Request) -> Response:
        """A command of the tab "System": everything goes through the slot script."""
        assert request.session is not None  # noqa: S101 - routes with a role always have a session
        arguments = request.json().get("arguments", [])
        by = f"web page, user {request.session.username}"
        return Response(data=self._box.system_command(request.params["command"], arguments, by))

    def _virtual_headset(self, request: Request) -> Response:
        data = request.json()
        return Response(data=self._box.virtual_headset(data.get("connected"), data.get("button")))

    def _backup(self, request: Request) -> Response:
        """The data as one archive (:mod:`tinyjukebox.backup`), written while it is sent: with ``albums=1`` it may be
        many gigabytes. If it fails on the way the archive is cut off, which its end shows."""
        backup = self.control.backup(request.query_flag("albums"))

        def send(out: Writable) -> None:
            try:
                result = backup.write(out)
            except OSError as error:
                self.logger.warning("backup %s was not sent to its end: %r", backup.filename, error)
                return
            self.logger.info("backup %s sent: %d files, %d bytes", backup.filename, result["files"], result["bytes"])

        headers = {"Content-Disposition": f'attachment; filename="{backup.filename}"'}
        return Response(200, content_type=backup.content_type, headers=headers, stream=send)

    def _events(self, request: Request) -> Response:
        return Response(content_type="text/event-stream", stream=lambda out: self._stream_events(request.token, out))

    def _stream_events(self, token: str | None, out: Writable) -> None:
        """Server-sent events until the client disconnects, the session ends or the api stops.

        * ``status`` (as ``/api/status``) whenever it changes - the playback position does not count, the page
          interpolates it - and at least every :data:`STATUS_REFRESH` seconds,
        * ``log`` (admins only) with the id of the newest record whenever there are new log records.

        Each stream checks for changes every :data:`EVENT_INTERVAL` seconds in its own (connection) thread.
        """
        last_key: Any = None
        last_sent = 0.0
        log_id = self.log_buffer.last_id
        try:
            while not self._stopping.is_set():
                session = self.sessions.get(token, idle=None)  # the stream says nothing about the user
                role = self.users.get_role(session.username) if session is not None else None
                if role is None:
                    break  # logged out, expired or removed - the page reconnects and gets a 401
                status = self.control.status()
                key = _without_position(status)
                now = time.monotonic()
                if key != last_key or now - last_sent >= STATUS_REFRESH:
                    _write_event(out, "status", status)
                    last_key, last_sent = key, now
                if has_role(role, "admin") and self.log_buffer.last_id != log_id:
                    log_id = self.log_buffer.last_id
                    _write_event(out, "log", {"last_id": log_id})
                self._stopping.wait(EVENT_INTERVAL)
        except OSError:  # the client has gone (ssl errors are OSErrors as well)
            pass
        except Exception:
            self.logger.exception("event stream failed")

    def _logs(self, request: Request) -> Response:
        def number(name: str, default: int) -> int:
            try:
                return int(request.query_value(name, str(default)))
            except ValueError:
                raise BadRequest(f"'{name}' must be a number.") from None

        text = request.query.get("text", [None])[-1]
        return Response(
            data=self.log_buffer.get(
                after=number("after", 0),
                level=request.query_value("level", "DEBUG").upper(),
                text=text,
                limit=max(1, min(number("limit", 500), 5000)),
            )
        )

    def _add_album(self, request: Request) -> Response:
        data = request.json()
        return Response(201, self.control.add_album(data.get("folder"), data.get("title")))

    def _cover(self, request: Request) -> Response:
        data, mime = self.control.get_cover(request.params["id"])
        return Response(body=data, content_type=mime, headers={"Cache-Control": "private, max-age=300"})

    def _set_cover(self, request: Request) -> Response:
        length = request.content_length()
        if length > MAX_IMAGE_BODY:
            raise PayloadTooLarge("image is too large.", code="image.too_large")
        return Response(data=self.control.set_cover(request.params["id"], request.stream.read(length)))

    def _upload_file(self, request: Request) -> Response:
        length = request.content_length()
        info = self.uploads.add_file(request.params["id"], request.params["filename"], request.stream, length)
        return Response(201, info)

    def _finish_upload(self, request: Request) -> Response:
        data = request.json()
        album = self.uploads.finish(request.params["id"], data.get("order"))
        result = {"album": self.control.get_album(album.target_id), "tag": None}
        tag_uid = data.get("tag_uid")
        if tag_uid:
            result["tag"] = self.control.link_tag_album(str(tag_uid), album.target_id)
        return Response(201, result)

    # --- request handling --------------------------------------------------------------------------------------

    def _token(self, handler: RequestHandler) -> tuple[str | None, bool]:
        """(token, from cookie)"""
        authorization = handler.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip(), False
        for part in handler.headers.get("Cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == self._cookie and value:
                return value, True
        return None, False

    @staticmethod
    def _idle(handler: RequestHandler, from_cookie: bool) -> float | None:
        """What a request says about when the user did something last (:meth:`Sessions.get`). The page tells with
        every request of its own; a request with the cookie that does not tell - an image the browser loads - says
        nothing. A request with a token in the header comes from a program: it is an action itself."""
        if not from_cookie:
            return 0
        try:
            idle = float(handler.headers.get(IDLE_HEADER, ""))
        except ValueError:
            return None
        return max(idle, 0.0) if math.isfinite(idle) else None

    def handle(self, handler: RequestHandler, method: str, path: str) -> Response:
        allowed_methods = []
        guard = next((guard for prefix, guard in self._guards.items() if path.startswith(prefix)), None)
        for route in self._routes:
            match = route.regex.match(path)
            if match is None:
                continue
            if route.method != method:
                allowed_methods.append(route.method)
                continue
            params = {k: urllib.parse.unquote(v) for k, v in match.groupdict().items()}
            if guard is not None:
                guarded = Request(handler, params, None, None)
                if route.role is not None:
                    guard(guarded)
                return route.handler(guarded)
            token, from_cookie = self._token(handler)
            session = self.sessions.get(token, self._idle(handler, from_cookie))
            if route.role is not None:
                if session is None:
                    raise Unauthorized("login required.")
                # role changes and removed users apply immediately
                role = self.users.get_role(session.username)
                if role is None:
                    self.sessions.remove_user(session.username)
                    raise Unauthorized("login required.")
                session.role = role
                if not has_role(role, route.role):
                    raise Forbidden(f"role '{route.role}' required.")
                if from_cookie and method != "GET" and handler.headers.get(CSRF_HEADER) is None:
                    raise Forbidden(f"header '{CSRF_HEADER}' missing.")
            # what the handler changes is noted as changed by this user (tinyjukebox.sync)
            with acting_as(f"web:{session.username}" if session is not None else "web"):
                return route.handler(Request(handler, params, session, token))
        if guard is not None:
            guard(Request(handler, {}, None, None))  # who may not ask does not learn which paths exist
        if allowed_methods:
            raise MethodNotAllowed(allowed_methods)
        raise NotFound("not found.")

    # --- start / stop ------------------------------------------------------------------------------------------

    def start(self) -> None:
        self.fingerprint = ensure_certificate(
            self._config["certificate"], self._config["private-key"], self.logger, self._config["hostnames"]
        )
        context = create_ssl_context(self._config["certificate"], self._config["private-key"])
        self.uploads.remove_stale_uploads()
        if self._own_log_buffer:
            self._root_logger.addHandler(self.log_buffer)
        self._stopping.clear()
        handler = type("Handler", (RequestHandler,), {"api": self})
        self._server = _Server((self._config["host"], self._config["port"]), handler, context)
        self._thread = threading.Thread(target=self._server.serve_forever, name="RestApi", daemon=True)
        self._thread.start()
        self.logger.info(
            "rest api listening on https://%s:%s - certificate fingerprint (sha256): %s",
            self._config["host"],
            self.port,
            self.fingerprint,
        )

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def certificate_file(self) -> str:
        """The certificate of the page (pem)."""
        return str(self._config["certificate"])

    @property
    def port(self) -> int:
        return int(self._server.server_address[1]) if self._server is not None else int(self._config["port"])

    def stop(self) -> None:
        if self._server is None:
            return
        self._stopping.set()
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join()
        self._server = None
        self.logger.info("rest api stopped.")
        if self._own_log_buffer:
            self._root_logger.removeHandler(self.log_buffer)
