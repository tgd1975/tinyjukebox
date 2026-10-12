"""A stand-in for the git server with the releases, as a box without a library asks it (deploy/slot/tinyjukebox-slot):
the newest release, a release by its tag, and the files of a release. It answers like GitHub as far as the slot
script reads the answer."""

import json
import threading
import time
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPOSITORY = "/repos/owner/box"


class GitServer:
    def __init__(self, token: str = "") -> None:
        """``token``: the repository is private - only a request with this token finds it."""
        self.token = token
        self.releases: dict[str, dict[str, Any]] = {}  # version -> release, in the order they were published
        self.files: dict[str, bytes] = {}
        self.requests: list[str] = []  # the paths that were asked for
        self.credentials: set[str] = set()  # what the requests carried as authorization
        self.cut: dict[str, int] = {}  # file name -> bytes after which the next download of it breaks, once
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                server._answer(self)

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self._http.serve_forever, name="GitServer", daemon=True).start()

    @property
    def url(self) -> str:
        """What ``RELEASES`` of ``slot.conf`` names: the repository at the interface of the git server."""
        return f"http://127.0.0.1:{self._http.server_port}{REPOSITORY}"

    def stop(self) -> None:
        self._http.shutdown()
        self._http.server_close()

    def publish(self, archive: Path, days_ago: float = 0, notes: str = "") -> str:
        """A release with the slot archive ``archive``, its signature and its description; returns its version."""
        description = Path(str(archive).removesuffix(".tar.gz") + ".json")
        version = json.loads(description.read_text())["version"]
        base = f"http://127.0.0.1:{self._http.server_port}"
        assets = []
        for file in (description, archive, Path(f"{archive}.sig")):
            self.files[file.name] = file.read_bytes()
            assets.append({
                "name": file.name, "size": len(self.files[file.name]),
                "url": f"{base}{REPOSITORY}/releases/assets/{file.name}",
                "browser_download_url": f"{base}/download/v{version}/{file.name}",
            })  # fmt: skip
        published = datetime.fromtimestamp(time.time() - days_ago * 86400, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.releases[version] = {
            "id": len(self.releases) + 1, "tag_name": f"v{version}", "published_at": published, "prerelease": False,
            "body": notes, "assets": assets,
        }  # fmt: skip
        return str(version)

    def withdraw(self, version: str) -> None:
        """Mark a release as a pre-release: it is not the newest one any more."""
        self.releases[version]["prerelease"] = True

    def asked_for(self, part: str) -> list[str]:
        return [path for path in self.requests if part in path]

    def _answer(self, request: BaseHTTPRequestHandler) -> None:
        path = request.path
        self.requests.append(path)
        self.credentials.add(request.headers.get("Authorization", ""))
        name = path.rsplit("/", 1)[1]
        file, status, body = None, 404, b"{}"
        if path.startswith("/download/"):
            # the files of a private repository are not handed out here, with or without a token
            file = None if self.token else name
        elif self.token and request.headers.get("Authorization") != f"token {self.token}":
            pass  # what a git server answers to somebody without access: there is no such repository
        elif path == f"{REPOSITORY}/releases/latest":
            listed = [release for release in self.releases.values() if not release["prerelease"]]
            if listed:
                status, body = 200, json.dumps(listed[-1]).encode()
        elif path.startswith(f"{REPOSITORY}/releases/tags/"):
            release = self.releases.get(name.removeprefix("v"))
            if release:
                status, body = 200, json.dumps(release).encode()
        elif path.startswith(f"{REPOSITORY}/releases/assets/"):
            if request.headers.get("Accept") == "application/octet-stream":
                file = name
            else:
                status = 200  # the description of the file, not the file
        if file is None:
            self._send(request, status, body)
        else:
            self._file(request, file)

    def _file(self, request: BaseHTTPRequestHandler, name: str) -> None:
        data = self.files.get(name)
        if data is None:
            return self._send(request, 404, b"")
        start = 0
        ranged = request.headers.get("Range", "")
        if ranged.startswith("bytes="):
            start = int(ranged[6:].split("-")[0])
        cut = self.cut.pop(name, None)
        request.send_response(206 if start else 200)
        request.send_header("Content-Length", str(len(data) - start))
        if start:
            request.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        request.end_headers()
        # a connection that breaks: fewer bytes than announced
        request.wfile.write(data[start:] if cut is None else data[start : start + cut])

    @staticmethod
    def _send(request: BaseHTTPRequestHandler, status: int, body: bytes) -> None:
        request.send_response(status)
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)
