"""The query for releases (docs/developer/library.md, section 8): the library asks the git server and
fetches the slot for the boxes.

GitHub cannot reach the home network, so the library asks. The interval follows the activity: often after a release was
sighted or a check was triggered by hand, then less and less often until the quiet interval is reached again.

The interface of Gitea is similar to the one of GitHub, not equal. The differences are in ``_Remote``.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from tinyjukebox import __version__
from tinyjukebox.library.store import Store
from tinyjukebox.sync.client import Unreachable
from tinyjukebox.sync.util import read_json, sha256_file, version_key, write_json

__all__ = ["DEFAULTS", "INSTALLER", "Releases", "app_name", "installer_name", "interval"]

INSTALLER = "install.sh"
"""The installer of a box as a file of a release (docs/developer/deployment.md, section 5)."""


def installer_name(version: str) -> str:
    """The installer of a version in the archive of the library and in the package."""
    return f"tinyjukebox-install-{version}.sh"


def app_name(version: str) -> str:
    """The app for phones of a version: its name in a release, in the archive of the library and in the package."""
    return f"tinyjukebox-app-{version}.apk"


DEFAULTS: dict[str, Any] = {
    "kind": "github",  # or "gitea"
    "api": "https://api.github.com",
    "repository": "",
    "token": "",  # empty: a public repository, asked without credentials. None: a token is needed and missing
    "quiet": 6 * 3600,  # seconds between two checks when nothing happens
    "busy": 600,  # ... after a release was sighted or a check was triggered by hand
    "busy-for": 6 * 3600,  # how long the short interval lasts
    "back-after": 14 * 24 * 3600,  # after this time the interval is the quiet one again
    "keep-versions": 5,  # versions the archive keeps at most; what a box needs stays in any case
}


def interval(config: dict[str, Any], sighted: float | None, now: float) -> float:
    """Seconds until the next check, ``sighted`` being the time of the last sighting or check by hand."""
    quiet, busy = float(config["quiet"]), float(config["busy"])
    if sighted is None:
        return quiet
    age = now - sighted
    if age < config["busy-for"]:
        return busy
    if age >= config["back-after"]:
        return quiet
    # longer step by step: the same factor for every equal span of time
    span = float(config["back-after"] - config["busy-for"])
    return float(busy * (quiet / busy) ** ((age - config["busy-for"]) / span))


class _NoRedirectedSecret(urllib.request.HTTPRedirectHandler):
    """A file of a release is redirected to a storage service, which must not get the token of the git server."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.headers.pop("Authorization", None)
            new.unredirected_hdrs.pop("Authorization", None)
        return new


class _Remote:
    """The one narrow place that knows the git server, with its two variants."""

    def __init__(self, config: dict[str, Any]) -> None:
        self._github = config["kind"] == "github"
        self._repository = config["repository"]
        self._base = f"{config['api'].rstrip('/')}/repos/{config['repository']}"
        # a public repository is asked without credentials
        self._credentials = (
            {"Authorization": f"{'Bearer' if self._github else 'token'} {config['token']}"} if config["token"] else {}
        )
        self._opener = urllib.request.build_opener(_NoRedirectedSecret)
        self.remaining: int | None = None  # requests the git server still allows in this hour, if it says so

    def _open(self, url: str, headers: dict[str, str]) -> Any:
        if not url.startswith(("https://", "http://")):
            raise Unreachable(f"{url}: not an address of a git server")
        request = urllib.request.Request(url, headers={**self._credentials, **headers})  # noqa: S310
        try:
            return self._opener.open(request, timeout=20)
        except urllib.error.HTTPError:
            raise
        except (OSError, urllib.error.URLError) as error:
            raise Unreachable(f"{url}: {error}") from None

    def release(self, which: str, etag: str | None) -> tuple[dict[str, Any] | None, str | None]:
        """The release ``which`` ("latest" or "tags/vX.Y.Z") and its ETag; (None, etag) if unchanged or not there.

        GitHub answers a conditional request with "unchanged", which costs nothing of the request limit. Gitea has no
        such answer; the caller compares the id of the release instead.
        """
        headers = {"Accept": "application/json"}
        if self._github and etag:
            headers["If-None-Match"] = etag
        try:
            with self._open(f"{self._base}/releases/{which}", headers) as answer:
                self._note_limit(answer.headers)
                return json.loads(answer.read()), answer.headers.get("ETag")
        except urllib.error.HTTPError as error:
            self._note_limit(error.headers)
            if error.code in (304, 404):
                return None, etag if error.code == 304 else None
            raise Unreachable(f"the git server answered {error.code}") from None

    def check_access(self) -> None:
        """Raise ``Unreachable`` if the token cannot read the releases of the repository.

        ``release`` cannot tell: a git server answers "not there" to a token without access, as it does for a
        repository without a release.
        """
        try:
            with self._open(f"{self._base}/releases?per_page=1", {"Accept": "application/json"}) as answer:
                self._note_limit(answer.headers)
        except urllib.error.HTTPError as error:
            self._note_limit(error.headers)
            if error.code == 401:
                raise Unreachable("the git server does not know this token") from None
            if error.code == 404:
                raise Unreachable(f"this token may not read the releases of {self._repository}") from None
            raise Unreachable(f"the git server answered {error.code}") from None

    def _note_limit(self, headers: Any) -> None:
        if headers is not None and headers.get("X-RateLimit-Remaining", "").isdigit():
            self.remaining = int(headers["X-RateLimit-Remaining"])

    def download(self, asset: dict[str, Any], filename: str) -> None:
        # GitHub: the file of a private repository is fetched through the API. Gitea: through its download address
        url = asset["url"] if self._github else asset["browser_download_url"]
        try:
            with self._open(url, {"Accept": "application/octet-stream"}) as answer, open(filename, "wb") as outfile:
                while chunk := answer.read(1 << 16):
                    outfile.write(chunk)
        except urllib.error.HTTPError as error:
            raise Unreachable(f"{asset['name']}: the git server answered {error.code}") from None


class Releases:
    def __init__(
        self, store: Store, config: dict[str, Any], platform: str, folder: str, logger: logging.Logger
    ) -> None:
        self._store = store
        self._config = {**DEFAULTS, **config}
        self._platform = platform
        self._folder = folder
        self._logger = logger
        self._remote = _Remote(self._config)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._state: dict[str, Any] = read_json(
            os.path.join(folder, "releases.json"),
            {"etag": None, "release": None, "sighted": None, "rejected": [], "checks": []},
        )

    def state(self) -> dict[str, Any]:
        return {
            **self._state,
            "interval": interval(self._config, self._state["sighted"], time.time()),
            "repository": self._config["repository"],
            "requests-left": self._remote.remaining,
            "keep": self._config["keep-versions"],
        }

    def start(self) -> None:
        if self._config["repository"]:
            threading.Thread(target=self._run, name="releases", daemon=True).start()

    def _run(self) -> None:
        self.check()
        while True:
            # a check by hand wakes this thread only so that it waits for the shortened interval from now on
            by_hand = self._wake.wait(interval(self._config, self._state["sighted"], time.time()))
            self._wake.clear()
            if not by_hand:
                self.check()

    def set_token(self, token: str) -> None:
        """Ask with this token from now on, if it can read the releases. Raises ``Unreachable`` if it cannot."""
        if not self._config["repository"]:
            raise Unreachable("no repository with releases is configured")
        remote = _Remote({**self._config, "token": token})
        remote.check_access()
        with self._lock:
            self._config["token"] = token
            self._remote = remote
            self._state["etag"] = None  # belongs to the answers for the token before

    def set_keep(self, versions: int) -> None:
        """Keep at most this number of versions in the archive, from now on."""
        with self._lock:
            self._config["keep-versions"] = versions
            self.prune()

    def check(self, by_hand: bool = False) -> dict[str, Any]:
        """Ask for the newest release now, and fetch it if it is new."""
        if not self._config["repository"]:
            return {"error": "no repository with releases is configured", "code": "releases.no_repository"}
        if self._config["token"] is None:
            return {
                "error": 'the token for the releases is missing: it is set on the page, tab "Versions"',
                "code": "releases.token_missing",
            }
        with self._lock:
            now = time.time()
            self._state["checks"] = [*self._state["checks"][-49:], now]
            if by_hand:
                self._state["sighted"] = now  # shortens the interval like a sighting
                self._wake.set()
            try:
                result = self._check(by_hand)
            except Unreachable as error:
                self._logger.warning("the git server cannot be reached, nothing changes: %s", error)
                result = {"error": str(error)}
            write_json(os.path.join(self._folder, "releases.json"), self._state)
            return result

    def _check(self, by_hand: bool) -> dict[str, Any]:
        release, etag = self._remote.release("latest", self._state["etag"])
        if release is None or (release["id"] == self._state["release"]):
            self._state["etag"] = etag
            return {"unchanged": True}
        if release["id"] in self._state["rejected"] and not by_hand:
            return {"unchanged": True}
        version = release["tag_name"].removeprefix("v")
        self._logger.info("release %s sighted", release["tag_name"])
        self._state["sighted"] = time.time()
        if not self._fetch(release, version):
            return {"rejected": version}
        self._state["release"] = release["id"]
        self._state["etag"] = etag
        return {"found": version}

    def _fetch(self, release: dict[str, Any], version: str) -> bool:
        """Download the slot of a release and put it into the archive, if its checksum fits."""
        base = f"tinyjukebox-box-{version}-{self._platform}"
        assets = {asset["name"]: asset for asset in release["assets"]}
        names = {"description": f"{base}.json", "archive": f"{base}.tar.gz", "signature": f"{base}.tar.gz.sig"}
        if any(name not in assets for name in names.values()):
            self._logger.warning("release %s has no slot for %s (yet)", release["tag_name"], self._platform)
            return False
        # downloaded next to the archive under a temporary name; in the archive only with a verified checksum
        folder = os.path.join(self._folder, "archive")
        parts = {kind: os.path.join(folder, f".{name}.part") for kind, name in names.items()}
        for kind, name in names.items():
            self._remote.download(assets[name], parts[kind])
        description = read_json(parts["description"])
        archive = parts["archive"]
        if os.path.getsize(archive) != description.get("size") or sha256_file(archive) != description.get("sha256"):
            self._logger.error("release %s: the slot has a wrong checksum, rejected", release["tag_name"])
            self._state["rejected"] = [*self._state["rejected"][-9:], release["id"]]
            self._store.journal.record("release", None, {"version": version, "rejected": "wrong checksum"})
            for filename in parts.values():
                os.remove(filename)
            return False
        files = {kind: os.path.join(folder, name) for kind, name in names.items()}
        for kind in names:
            os.replace(parts[kind], files[kind])
        # the installer of a box (install.sh, from 0.38.0 on): the library hands it out with the slot of the same
        # version, so that a new box joins it without a PC. Its name is the same in every release
        if INSTALLER in assets:
            files["installer"] = os.path.join(folder, installer_name(version))
            self._remote.download(assets[INSTALLER], f"{files['installer']}.part")
            os.replace(f"{files['installer']}.part", files["installer"])
        # the app for phones (from 0.42.0 on): handed out with the installer, so that app and library fit
        if app_name(version) in assets:
            files["app"] = os.path.join(folder, app_name(version))
            self._remote.download(assets[app_name(version)], f"{files['app']}.part")
            os.replace(f"{files['app']}.part", files["app"])
        self._store.add_version(description, files, "release")
        self.prune(version)
        return True

    def restore(self, version: str) -> None:
        """Fetch a version again whose files were removed from the archive."""
        release, _ = self._remote.release(f"tags/v{version}", None)
        if release is None or not self._fetch(release, version):
            raise Unreachable(f"version {version} cannot be fetched again")

    def prune(self, fetched: str | None = None) -> None:
        """Keep the archive within its limits: a number of versions and a size, whichever is reached first.

        Never removed: versions that run on a box or lie in one of its slots, the version of the library itself, the
        newest version in the state ``use``, and ``fetched``, the version that was fetched just now - a box is waiting
        for it. They count for the limits all the same. Versions in the state ``dontuse`` go first, then the oldest.
        """
        keep, limit = self._config.get("keep-versions"), self._config.get("keep-bytes")
        versions = {version: entry for version, entry in self._store.versions().items() if entry.get("files")}
        needed = {
            slot.get("version") for box in self._store.boxes() for slot in box["status"].get("slots", {}).values()
        } | {__version__, fetched}
        usable = [version for version, entry in versions.items() if entry["state"] == "use"]
        if usable:
            needed.add(max(usable, key=version_key))
        candidates = sorted(
            (version for version in versions if version not in needed),
            key=lambda version: (versions[version]["state"] != "dontuse", version_key(version)),
        )

        def size(version: str) -> int:
            return os.path.getsize(self._store.version_file(version, "archive"))

        total = sum(size(version) for version in versions)
        count = len(versions)
        for version in candidates:
            if (keep is None or count <= keep) and (limit is None or total <= limit):
                break
            total -= size(version)
            count -= 1
            self._store.drop_version_files(version)
            self._logger.info("version %s removed from the archive", version)
