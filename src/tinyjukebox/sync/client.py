"""HTTP(S) client on the standard library: JSON, files that can be resumed, server-sent events.

The client accepts exactly one certificate, the one it was given (docs/developer/library.md, section 4.14);
the name in it does not matter. The counterpart is the web server of the library (``tinyjukebox.web.server``).
"""

import contextlib
import http.client
import json
import os
import socket
import ssl
import urllib.parse
from collections.abc import Iterator
from typing import Any

__all__ = ["KEEP_ALIVE", "Client", "HttpError", "Response", "Unreachable", "WrongCertificate"]

KEEP_ALIVE = 5.0  # seconds without an event after which an event stream sends a comment, so that both ends notice a cut


class HttpError(Exception):
    """An answer with a status of 400 or above - raised by a route, and by the client when it receives one."""

    def __init__(self, status: int, message: str = "", **details: Any) -> None:
        super().__init__(f"{status} {message}".strip())
        self.status = status
        self.message = message
        self.details = details


class Unreachable(Exception):  # noqa: N818 - a state of the network, not a programming error
    """The other side cannot be reached, or the connection broke."""


class WrongCertificate(Exception):  # noqa: N818
    """The server presented another certificate than the one that is expected."""


class Response:
    def __init__(self, status: int, headers: http.client.HTTPMessage, body: bytes) -> None:
        self.status = status
        self.headers = headers
        self.body = body

    def json(self) -> Any:
        return json.loads(self.body) if self.body else None


class Client:
    """Requests to one server. With ``certificate`` (PEM) only a server that presents exactly this one is accepted."""

    def __init__(
        self, url: str, certificate: str | None = None, headers: dict[str, str] | None = None, timeout: float = 10.0
    ) -> None:
        split = urllib.parse.urlsplit(url)
        self._https = split.scheme == "https"
        self._host = split.hostname or "localhost"
        self._port = split.port or (443 if self._https else 80)
        self._prefix = split.path.rstrip("/")
        self._certificate = ssl.PEM_cert_to_DER_cert(certificate) if certificate else None
        self.headers = dict(headers or {})
        self._timeout = timeout
        self.peer_certificate: str | None = None  # PEM of the certificate the server presented last
        self._stream: socket.socket | None = None  # the connection that events are read from

    def _connect(self, timeout: float | None = None) -> http.client.HTTPConnection:
        timeout = self._timeout if timeout is None else timeout
        try:
            if not self._https:
                connection = http.client.HTTPConnection(self._host, self._port, timeout=timeout)
                connection.connect()
                return connection
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE  # checked below: equal to the one certificate that is expected
            secure = http.client.HTTPSConnection(self._host, self._port, timeout=timeout, context=context)
            secure.connect()
        except (OSError, http.client.HTTPException) as error:
            raise Unreachable(f"{self._host}:{self._port}: {error}") from None
        presented = secure.sock.getpeercert(binary_form=True) or b""
        self.peer_certificate = ssl.DER_cert_to_PEM_cert(presented)
        if self._certificate is not None and presented != self._certificate:
            secure.close()
            raise WrongCertificate(f"{self._host}:{self._port} presents another certificate than the stored one")
        return secure

    def request(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        upload: str | None = None,
        sink: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        """One request. ``body`` is sent as JSON, ``upload`` names a file to send, ``sink`` a file for the answer.

        A ``sink`` that exists already is continued. An answer with a status of 400 or above raises ``HttpError``.
        """
        send_headers = {**self.headers, **(headers or {})}
        data: Any = None
        if upload is not None:
            send_headers["Content-Length"] = str(os.path.getsize(upload))
            send_headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            send_headers["Content-Type"] = "application/json"
        offset = os.path.getsize(sink) if sink is not None and os.path.exists(sink) else 0
        if offset:
            send_headers["Range"] = f"bytes={offset}-"
        connection = self._connect()
        try:
            if upload is not None:
                with open(upload, "rb") as infile:
                    connection.request(method, self._prefix + path, body=infile, headers=send_headers)
            else:
                connection.request(method, self._prefix + path, body=data, headers=send_headers)
            answer = connection.getresponse()
            if sink is not None and answer.status in (200, 206):
                # the rest of what is there already: 206 - from a library before 0.45, and for a file that is complete
                # already, 200 with the range
                rest = offset > 0 and str(answer.headers.get("Content-Range", "")).startswith(f"bytes {offset}-")
                if answer.status == 206 and not rest:
                    raise Unreachable(f"{self._host}:{self._port}: {path} answered another range than asked for")
                with open(sink, "ab" if rest else "wb") as outfile:
                    while chunk := answer.read(1 << 16):
                        outfile.write(chunk)
                    outfile.flush()
                    os.fsync(outfile.fileno())
                content = b""
            else:
                content = answer.read()
        except (OSError, http.client.HTTPException) as error:
            raise Unreachable(f"{self._host}:{self._port}: {error!r}") from None
        finally:
            connection.close()
        response = Response(answer.status, answer.headers, content)
        if answer.status >= 400:
            details: dict[str, Any] = {}
            with contextlib.suppress(ValueError):
                details = response.json() or {}
            failure = HttpError(answer.status, str(details.pop("error", "") or details.get("message", "")))
            failure.details = details
            raise failure
        return response

    def get(self, path: str) -> Any:
        return self.request("GET", path).json()

    def post(self, path: str, body: Any = None) -> Any:
        return self.request("POST", path, body).json()

    def put(self, path: str, body: Any = None) -> Any:
        return self.request("PUT", path, body).json()

    def delete(self, path: str) -> Any:
        return self.request("DELETE", path).json()

    def events(self, path: str) -> Iterator[tuple[str, Any]]:
        """Server-sent events until the connection breaks; then ``Unreachable`` is raised."""
        connection = self._connect(timeout=3 * KEEP_ALIVE)
        try:
            connection.request("GET", self._prefix + path, headers=self.headers)
            self._stream = connection.sock  # the answer takes the socket over: the connection forgets it
            answer = connection.getresponse()
            if answer.status != 200:
                raise HttpError(answer.status, "no events")
            event = ""
            while line := answer.readline():
                text = line.decode("utf-8").rstrip("\n")
                if text.startswith("event: "):
                    event = text[7:]
                elif text.startswith("data: "):
                    yield event, json.loads(text[6:])
        except (TimeoutError, OSError, http.client.HTTPException) as error:
            raise Unreachable(f"{self._host}:{self._port}: {error!r}") from None
        finally:
            self._stream = None
            connection.close()
        raise Unreachable(f"{self._host}:{self._port}: the event stream ended")

    def interrupt(self) -> None:
        """End :meth:`events` from another thread: its connection is cut."""
        stream = self._stream
        if stream is not None:
            with contextlib.suppress(OSError):
                stream.shutdown(socket.SHUT_RDWR)
