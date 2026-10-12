"""Albums and the covers of the stations on a box (docs/developer/library.md, section 4.8).

A file that one side lacks is copied from the side that has it, in both directions with the same manifest. A file gets
its name only after its checksum was verified, so a power cut in the middle of a transfer leaves no half file.

An album is fetched on demand: first the files that are not played (playlist, cover), then the track that is to be
played, then the rest in the order of the playlist.
"""

import contextlib
import os
import shutil
import threading
import urllib.parse
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from tinyjukebox.images import is_cover
from tinyjukebox.sync import manifest as manifests
from tinyjukebox.sync.client import Client, HttpError, Unreachable
from tinyjukebox.sync.link import V
from tinyjukebox.sync.util import read_json, sha256_file, write_json

if TYPE_CHECKING:
    from tinyjukebox.sync.box import BoxSync

__all__ = ["ATTEMPTS", "AlbumFiles", "ImageFiles"]

ATTEMPTS = 3  # a file whose checksum is wrong is discarded and requested again, this often


def _is_album(value: Any) -> bool:
    return isinstance(value, dict) and value.get("target-type") == "album"


class AlbumFiles:
    def __init__(self, box: "BoxSync", albums_folder: str, manifests_folder: str, keep_free: int = 0) -> None:
        """``keep_free``: bytes that stay free on the card of the box (0: nothing is ever deleted for space)."""
        self._box = box
        self._logger = box.logger
        self._albums = albums_folder
        self._manifests = manifests_folder
        self._keep_free = keep_free
        os.makedirs(manifests_folder, exist_ok=True)
        self._played_file = os.path.join(os.path.dirname(manifests_folder), "played.json")
        self._lock = threading.Lock()
        self._wanted: dict[str, int] = {}  # album -> index of the track that is needed first
        self._fetching: set[str] = set()
        self._failed: dict[str, str] = {}  # album -> why it cannot be fetched
        self._listeners: list[Callable[[str], None]] = []

    # --- what is here ------------------------------------------------------------------------------------------------

    def _album(self, album: str) -> dict[str, Any] | None:
        value = self._box.data.working("target").get(album)
        return value if _is_album(value) else None

    def folder(self, value: dict[str, Any]) -> str:
        return os.path.join(self._albums, value["album-folder"])

    def _manifest_file(self, album: str) -> str:
        return os.path.join(self._manifests, f"{album}.json")

    def manifest(self, album: str) -> dict[str, Any] | None:
        manifest: dict[str, Any] | None = read_json(self._manifest_file(album))
        return manifest

    def lacking(self, album: str) -> list[str] | None:
        """The files of the album that this box lacks; None if the box does not know them yet.

        An album that the library has never seen has no files to fetch: it is complete as it is.
        """
        value = self._album(album)
        if value is None or self._box.data.album(self._box.data.final(album)) is None:
            return []
        manifest = self.manifest(album)
        return None if manifest is None else manifests.missing(self.folder(value), manifest)

    def ready(self, album: str, track: int) -> bool:
        """Whether the track ``track`` of the playlist can be played: it is here, and so are the files that are not
        played."""
        lacking = self.lacking(album)
        manifest = self.manifest(album)
        if lacking is None or (lacking and manifest is None):
            return False
        if not lacking or manifest is None:
            return True
        needed = manifests.extras(manifest) + manifest["order"][track : track + 1]
        return not any(name in lacking for name in needed)

    def failed(self, album: str) -> str | None:
        with self._lock:
            return self._failed.get(album)

    def forget(self) -> None:
        """The box left its library: what it knew about the files of the albums there goes. The files stay."""
        with self._lock:
            self._wanted.clear()
            self._failed.clear()
            for name in os.listdir(self._manifests):
                os.remove(os.path.join(self._manifests, name))
            with contextlib.suppress(FileNotFoundError):
                os.remove(self._played_file)

    def fetching(self) -> list[str]:
        with self._lock:
            return sorted(self._fetching)

    def subscribe(self, listener: Callable[[str], None]) -> None:
        """``listener(album)`` is called whenever a file of an album arrived or its download ended."""
        with self._lock:
            self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[str], None]) -> None:
        with self._lock, contextlib.suppress(ValueError):
            self._listeners.remove(listener)

    def _announce(self, album: str) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(album)

    # --- space on the card (section 4.16) ----------------------------------------------------------------------------

    def played(self, album: str) -> None:
        """The album was played just now. The order is kept as an order, not as times: without WLAN the clock of
        the box is wrong."""
        with self._lock:
            order = [entry for entry in read_json(self._played_file, []) if entry != album]
            write_json(self._played_file, [*order, album])

    def _expendable(self) -> list[str]:
        """Albums whose audio files may be deleted here, the one not played for the longest time first: the library
        has all their files, and they are fetched again when their tag is placed. Never the album that plays, and
        never one that exists only on this box."""
        with self._lock:
            order: list[str] = read_json(self._played_file, [])
            busy = set(self._fetching)
        candidates = []
        for album, value in self._box.data.working("target").items():
            described = self._box.data.album(album)
            manifest = self.manifest(album)
            if not _is_album(value) or described is None or described["lacks"] or manifest is None:
                continue
            if self._box.data.is_provisional(album) or album in busy or self._box.playing(album):
                continue
            if len(manifests.missing(self.folder(value), manifest)) < len(manifest["files"]):
                candidates.append(album)
        return sorted(candidates, key=lambda album: order.index(album) if album in order else -1)

    def make_room(self, needed: int = 0) -> None:
        """Keep the configured amount of space free: delete the audio files of albums until it is, the album not
        played for the longest time first. The album stays in the catalog."""
        if not self._keep_free or not os.path.isdir(self._albums):
            return
        for album in self._expendable():
            if shutil.disk_usage(self._albums).free - needed >= self._keep_free:
                return
            value, manifest = self._album(album), self.manifest(album)
            if value is None or manifest is None:
                continue
            for entry in manifest["files"]:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(manifests.path(self.folder(value), entry["name"]))
            self._logger.info(
                "space on the card: the files of album %s (%s) were deleted here; the library has them",
                album, value["album-folder"],
            )  # fmt: skip

    # --- an album that was added on this box -------------------------------------------------------------------------

    def described(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``entries`` of the outbox, every new album with its manifest. A manifest is created only now, when the
        album is about to be sent: computing the checksums of an album takes time. An album that cannot be read is
        sent without one, as the entry it is."""
        return [self._described(entry) for entry in entries]

    def _described(self, entry: dict[str, Any]) -> dict[str, Any]:
        if entry["op"] != "create" or not _is_album(entry["value"]) or "manifest" in entry:
            return entry
        manifest = None
        try:
            manifest = manifests.create(self.folder(entry["value"]), entry["value"]["playlist"])
            write_json(self._manifest_file(entry["key"]), manifest)
        except OSError as error:
            self._logger.warning("the album %s cannot be read: %s", entry["value"]["album-folder"], error)
        self._box.data.amend(entry["n"], manifest=manifest)
        return {**entry, "manifest": manifest}

    def changed(self, album: str) -> None:
        """The files of an album changed on this box, e.g. its playlist was written anew: tell the library."""
        value = self._album(album)
        if value is None or self.manifest(album) is None:
            return  # not sent yet: its manifest is created when it is
        manifest = manifests.create(self.folder(value), value["playlist"])
        self._box.data.add("target", album, value, manifest=manifest)
        # after the entry of the outbox: a manifest that differs from the library's without such an entry would
        # mean "changed in the library", and :meth:`balance` would delete what was changed here
        write_json(self._manifest_file(album), manifest)

    def renamed(self, provisional: str, final: str, value: dict[str, Any] | None) -> None:
        """The library assigned the final id - or found that it has an album with identical files already. In the
        library the folder may have another name, because two boxes uploaded different albums under one name."""
        if os.path.exists(self._manifest_file(provisional)):
            os.replace(self._manifest_file(provisional), self._manifest_file(final))
        old = next(
            (e["value"] for e in self._box.data.outbox() if e["op"] == "create" and e["key"] == provisional), None
        )
        if old is None or not _is_album(value) or value is None or old["album-folder"] == value["album-folder"]:
            return
        source, target = self.folder(old), self.folder(value)
        if os.path.isdir(source) and not os.path.exists(target):
            os.makedirs(os.path.dirname(target), exist_ok=True)
            os.replace(source, target)
            self._logger.info("album folder '%s' is '%s' now", old["album-folder"], value["album-folder"])

    def removed(self, value: dict[str, Any]) -> None:
        """An album that was removed in the library is removed from the box as well."""
        if not _is_album(value):
            return
        album = str(value["target-id"])
        with contextlib.suppress(FileNotFoundError):
            os.remove(self._manifest_file(album))
        folder = os.path.realpath(self.folder(value))
        root = os.path.realpath(self._albums)
        if folder != root and os.path.commonpath([root, folder]) == root and os.path.isdir(folder):
            shutil.rmtree(folder, ignore_errors=True)
            self._logger.info("album %s was removed in the library: folder '%s' deleted", album, value["album-folder"])

    # --- towards the library -----------------------------------------------------------------------------------------

    def send(self, client: Client, album: str, missing: list[str]) -> bool:
        """Send the files that the library lacks. No list of the progress is kept: the library says what is missing."""
        value = self._album(self._box.data.final(album))
        sent = False
        for name in missing if value is not None else []:
            filename = manifests.path(self.folder(value), name) if value is not None else ""
            if os.path.isfile(filename):
                client.request("PUT", f"{V}/albums/{album}/files/{_quoted(name)}", upload=filename)
                self._logger.info("file %s of album %s sent to the library", name, album)
                sent = True
        return sent

    def balance(self, client: Client) -> bool:
        """Compare the albums on this box with the library: take over a changed manifest, send what the library
        lacks, go on fetching what was begun here."""
        sent = False
        data = self._box.data
        # albums with a change of this box that the library has not answered yet: what the library says about their
        # files is the older state, so nothing is taken over from it until the change is settled
        pending = {str(data.final(entry["key"])) for entry in data.outbox() if entry["kind"] == "target"}
        for album, value in data.working("target").items():
            described = data.album(album)
            manifest = self.manifest(album)
            if not _is_album(value) or described is None or manifest is None:
                continue
            differs = manifests.checksum(manifest) != described["checksum"]
            if differs and album not in pending and not self._box.playing(album):
                manifest = self._take_over(client, album, value, manifest)
            lacking = manifests.missing(self.folder(value), manifest)
            here = [name for name in described["lacks"] if name not in lacking]
            if here:
                sent = self.send(client, album, here) or sent
            if lacking and os.path.isdir(self.folder(value)):
                self.need(album, 0)
        return sent

    def _take_over(self, client: Client, album: str, value: dict[str, Any], old: dict[str, Any]) -> dict[str, Any]:
        """The files of the album changed in the library: files that differ are removed here and fetched again."""
        new = client.get(f"{V}/albums/{album}/manifest")
        checksums = {entry["name"]: entry["sha256"] for entry in new["files"]}
        for entry in old["files"]:
            if checksums.get(entry["name"]) != entry["sha256"]:
                with contextlib.suppress(FileNotFoundError):
                    os.remove(manifests.path(self.folder(value), entry["name"]))
        manifest = {"files": new["files"], "order": new["order"]}
        write_json(self._manifest_file(album), manifest)
        self._logger.info("the files of album %s changed in the library", album)
        return manifest

    # --- an album that is needed here --------------------------------------------------------------------------------

    def need(self, album: str, track: int) -> None:
        """Fetch the album from the library, beginning with the track ``track`` of its playlist. A second call for
        another track pulls that one forward."""
        with self._lock:
            self._wanted[album] = track
            self._failed.pop(album, None)
            if album in self._fetching:
                return
            self._fetching.add(album)
        threading.Thread(target=self._fetch, args=(album,), name=f"AlbumFiles.fetch-{album}", daemon=True).start()

    def _fetch(self, album: str) -> None:
        try:
            self._fetch_files(album)
        except (Unreachable, HttpError) as error:
            self._logger.info("fetching album %s is interrupted: %s", album, error)
            with self._lock:
                self._failed[album] = str(error)
        except Exception as error:
            with self._lock:
                self._failed[album] = repr(error)
            self._box.unexpected(error)
        finally:
            with self._lock:
                self._fetching.discard(album)
            self._announce(album)

    def _client(self) -> Client:
        client: Client | None = self._box.link.client
        if client is None or not self._box.link.connected:
            raise Unreachable("the files are not on this box, and the library cannot be reached")
        return client

    def _fetch_files(self, album: str) -> None:
        value = self._album(album)
        if value is None:
            raise Unreachable(f"album {album} is not in the catalog")
        folder = self.folder(value)
        manifest = self.manifest(album)
        if manifest is None:
            answer = self._client().get(f"{V}/albums/{album}/manifest")
            manifest = {"files": answer["files"], "order": answer["order"]}
            write_json(self._manifest_file(album), manifest)
        entries = {entry["name"]: entry for entry in manifest["files"]}
        attempts: dict[str, int] = {}
        failed: set[str] = set()
        while True:
            missing = [name for name in manifests.missing(folder, manifest) if name not in failed]
            if not missing:
                if failed:
                    raise Unreachable(f"{len(failed)} files have a wrong checksum again and again")
                self._logger.info("album %s is complete", album)
                return
            name = self._next(album, manifest, missing)
            target = manifests.path(folder, name)
            part = os.path.join(os.path.dirname(target), f".{os.path.basename(target)}.part")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            self.make_room(entries[name]["size"])
            # continues a part that is there
            self._client().request("GET", f"{V}/albums/{album}/files/{_quoted(name)}", sink=part)
            if os.path.getsize(part) == entries[name]["size"] and sha256_file(part) == entries[name]["sha256"]:
                os.replace(part, target)
                self._logger.info("file %s of album %s fetched", name, album)
            else:
                os.remove(part)
                attempts[name] = attempts.get(name, 0) + 1
                if attempts[name] < ATTEMPTS:
                    self._logger.warning("file %s of album %s has a wrong checksum: requested again", name, album)
                else:
                    self._logger.error("file %s of album %s has a wrong checksum again and again", name, album)
                    self._box.data.journal("file", album=album, file=name, error="wrong checksum")
                    failed.add(name)
            self._announce(album)

    def _next(self, album: str, manifest: dict[str, Any], missing: list[str]) -> str:
        """The file to fetch now: what is not played first, then the wanted track, then on in the playlist."""
        other = [name for name in manifests.extras(manifest) if name in missing]
        if other:
            return other[0]
        order: list[str] = manifest["order"]
        with self._lock:
            start = min(max(self._wanted.get(album, 0), 0), max(len(order) - 1, 0))
        return next(name for name in order[start:] + order[:start] if name in missing)


def _quoted(name: str) -> str:
    """A file name as one segment of a path: a file in a folder of the album has ``/`` in its name."""
    return urllib.parse.quote(name, safe="")


class ImageFiles:
    """The covers of the stations: a box fetches all of them once, and afterwards only what has changed."""

    def __init__(self, box: "BoxSync", folder: str) -> None:
        self._box = box
        self._logger = box.logger
        self._folder = folder
        self._checksums: dict[str, tuple[int, float, str]] = {}  # name -> (size, modified, checksum)

    def _checksum(self, name: str) -> str:
        filename = os.path.join(self._folder, name)
        stat = os.stat(filename)
        known = self._checksums.get(name)
        if known is None or known[:2] != (stat.st_size, stat.st_mtime):
            known = self._checksums[name] = (stat.st_size, stat.st_mtime, sha256_file(filename))
        return known[2]

    def _local(self) -> list[str]:
        try:
            names = os.listdir(self._folder)
        except FileNotFoundError:
            return []
        return sorted(name for name in names if is_cover(name))

    def balance(self, client: Client) -> bool:
        """Fetch the images that are missing or differ here, remove what the library removed, and send the images
        that were uploaded on this box. Returns whether something was sent."""
        images = self._box.data.images()
        local = self._local()
        sent = False
        for name in local:
            if name not in images:
                client.request("PUT", f"{V}/images/{_quoted(name)}", upload=os.path.join(self._folder, name))
                self._logger.info("cover %s sent to the library", name)
                sent = True
        for name, image in images.items():
            if image.get("deleted"):
                if name in local:
                    os.remove(os.path.join(self._folder, name))
                    self._logger.info("cover %s was removed in the library", name)
                continue
            if name in local and self._checksum(name) == image["sha256"]:
                continue
            os.makedirs(self._folder, exist_ok=True)
            part = os.path.join(self._folder, f".{name}.part")
            with contextlib.suppress(FileNotFoundError):
                os.remove(part)
            client.request("GET", f"{V}/images/{_quoted(name)}", sink=part)
            if sha256_file(part) == image["sha256"]:
                os.replace(part, os.path.join(self._folder, name))
            else:
                os.remove(part)
                self._logger.warning("cover %s has a wrong checksum, discarded", name)
        return sent
