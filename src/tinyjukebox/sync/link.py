"""The synchronization of a box with the library (docs/developer/library.md, section 4).

The box always opens the connection. One pass: who are you and do we fit together; send the outbox; fetch what
differs; settle provisional ids and files; deliver the journal; report the status. A pass runs on start, on an event
of the library and after a change on the box; without a connection it is tried again.
"""

import contextlib
import os
import socket
import threading
from typing import TYPE_CHECKING, Any

from tinyjukebox.sync import INTERFACE, handshake
from tinyjukebox.sync.client import Client, HttpError, Unreachable, WrongCertificate
from tinyjukebox.sync.data import CATALOG_FILES, COPY, STATE
from tinyjukebox.sync.util import sha256_bytes, write_atomic

if TYPE_CHECKING:
    from tinyjukebox.sync.box import BoxSync

__all__ = ["FITS", "Link", "V"]

V = f"/api/sync/v{INTERFACE}"
FITS = "ok"
IDLE_SECONDS = 60.0  # a pass at least this often while nothing happens
BATCH = 20  # entries of the outbox in one request


class _Alone(Exception):  # noqa: N818
    """The box works without the library for now; the text says why."""


class Link:
    def __init__(self, box: "BoxSync") -> None:
        self._box = box
        self._data = box.data
        self._logger = box.logger
        self._identity = box.identity
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._passed = threading.Condition()
        self._passes = 0
        self._threads: list[threading.Thread] = []
        self._said = ""  # the reason for working alone that was logged last
        self._reported: Any = None
        self._checked = False  # whether the versions were looked at since the connection came back
        self.connected = False
        self.alone = ""  # why the box works without the library, for its page
        self.alone_code = ""  # ... as a code, if the page has a text for this reason
        self.client: Client | None = None
        self._listening: Client | None = None  # the client whose events are read

    # --- threads -----------------------------------------------------------------------------------------------------

    def start(self) -> None:
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._run, name="Link.sync", daemon=True),
            threading.Thread(target=self._listen, name="Link.events", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        listening = self._listening
        if listening is not None:
            listening.interrupt()
        for thread in self._threads:
            thread.join(timeout=5)
        self._threads = []
        self.connected = False

    def wake(self) -> None:
        self._wake.set()

    def wait_pass(self, timeout: float) -> bool:
        """Trigger a pass and wait until it is over."""
        with self._passed:
            before = self._passes
            self._wake.set()
            return self._passed.wait_for(lambda: self._passes > before, timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            self._wake.clear()
            again = False
            try:
                again = self._pass()
                self._said = self.alone = self.alone_code = ""
            except _Alone as alone:
                self._alone(str(alone))
            except Unreachable as error:
                self._alone(f"the library cannot be reached: {error}", code="unreachable")
            except WrongCertificate as error:
                self._alone(str(error), error=True, code="certificate")
            except HttpError as error:
                self._alone(f"the library answered {error}", error=error.status != 410, code="answered")
            except Exception as error:
                self._box.unexpected(error)
            with self._passed:
                self._passes += 1
                self._passed.notify_all()
            if again:
                continue
            # without a connection, and while something waits to be settled, the box tries again by itself
            pending = not self.connected or bool(self._data.outbox()) or bool(self._data.journal_lines(1))
            self._wake.wait(self._box.retry_seconds if pending else IDLE_SECONDS)

    def _alone(self, reason: str, error: bool = False, code: str = "") -> None:
        self.connected = False
        self._checked = False
        self.alone = reason
        self.alone_code = code
        if reason != self._said:  # said once, not with every attempt
            self._logger.log(40 if error else 20, "working without the library: %s", reason)
            self._said = reason

    def _listen(self) -> None:
        """Keep a connection open on which the library announces changes (section 4.9)."""
        while not self._stop.is_set():
            client = self.client
            if client is not None and self.connected and self._box.fit == FITS:
                self._listening = client
                with contextlib.suppress(Unreachable, WrongCertificate, HttpError):
                    for event, _ in client.events(f"{V}/events"):
                        if self._stop.is_set():
                            return
                        # "connected" comes first: what changed while nobody listened is caught up by a pass
                        self._logger.log(10 if event == "connected" else 20, "event of the library: %s", event)
                        if event == "versions":
                            self._checked = False
                        self._wake.set()
                self._wake.set()  # the connection broke: the pass finds out whether the library is gone
                self._stop.wait(self._box.retry_seconds)
            else:
                self._stop.wait(0.2)

    # --- identity and access (sections 4.7, 4.14) --------------------------------------------------------------------

    def _read(self, name: str) -> str:
        try:
            with open(os.path.join(self._identity, name), encoding="utf-8") as infile:
                return infile.read().strip()
        except FileNotFoundError:
            return ""

    def _connect(self) -> Client:
        url = self._read("library")
        if not url:
            raise _Alone("no library is configured")
        if not self._read("key"):
            self._handshake(url)
        headers = {
            "Authorization": f"Box {self._data.box_id}:{self._read('key')}",
            "X-Box-Version": self._box.version,
            "X-Box-Interface": str(INTERFACE),
        }
        with open(os.path.join(self._identity, "library.crt"), encoding="utf-8") as infile:
            return Client(url, certificate=infile.read(), headers=headers)

    def _handshake(self, url: str) -> None:
        """Prove the bootstrap password to the library and get a key of its own; check and store its certificate."""
        password = self._read("bootstrap")
        if not password:
            raise _Alone("this box has no key for the library and no bootstrap password")
        box_id, nonce = self._data.box_id, handshake.new_nonce()
        first = Client(url)
        nonce_library = first.post("/api/sync/boxes", {"box": box_id, "nonce": nonce})["nonce"]
        certificate = first.peer_certificate or ""
        proof = handshake.proof(password, "box", box_id, nonce, nonce_library, certificate)
        try:
            answer = Client(url, certificate=certificate).post(
                "/api/sync/boxes", {"box": box_id, "proof": proof, "name": socket.gethostname()}
            )
        except HttpError as error:
            if error.status == 401:
                raise WrongCertificate(
                    "the handshake was rejected: wrong bootstrap password, or not the library"
                ) from None
            raise
        expected = handshake.proof(password, "library", box_id, nonce, nonce_library, certificate)
        if not handshake.same(str(answer.get("proof")), expected):
            raise WrongCertificate("the handshake failed: the other side does not know the bootstrap password")
        os.makedirs(self._identity, mode=0o700, exist_ok=True)
        write_atomic(os.path.join(self._identity, "library.crt"), certificate)
        key = os.path.join(self._identity, "key")
        write_atomic(key, answer["key"] + "\n")
        os.chmod(key, 0o600)
        os.remove(os.path.join(self._identity, "bootstrap"))  # this box does not need the password any more
        self._logger.info(
            "handshake with the library done: this box has its own key and the certificate of the library"
        )

    def _ask_interface(self) -> list[int]:
        """Which versions of the interface the library serves. Repeats the handshake if the library refuses the key
        or shows another certificate - which works only on a box that was given the bootstrap password again."""
        try:
            self.client = self._connect()
            return list(self.client.get("/api/sync/interface")["serves"])
        except (WrongCertificate, HttpError) as error:
            if isinstance(error, HttpError) and error.status != 401:
                raise
            if not self._read("bootstrap"):
                if isinstance(error, HttpError):
                    raise WrongCertificate("the library rejects the key of this box") from None
                raise
            self._handshake(self._read("library"))
            self.client = self._connect()
            return list(self.client.get("/api/sync/interface")["serves"])

    # --- one pass ----------------------------------------------------------------------------------------------------

    def _pass(self) -> bool:
        """One pass; returns whether another one should follow at once."""
        serves = self._ask_interface()
        client = self.client
        assert client is not None  # noqa: S101
        if not self.connected:
            self._logger.info("connected to the library")
        self.connected = True
        if max(serves) < INTERFACE:
            # only a person can repair this: the box stops working (section 4.15)
            self._box.set_fit(f"library-too-old: this box speaks version {INTERFACE}, the library serves {serves}")
            return False
        if min(serves) > INTERFACE:
            # the box repairs this itself: it works alone and fetches the newest version
            self._box.set_fit(f"box-too-old: this box speaks version {INTERFACE}, the library serves {serves}")
            self._look_for_versions()
            return False
        self._box.set_fit(FITS)
        self._look_for_versions()
        self._box.discard_defective()
        done = self._push(client)
        again = self._pull(client)
        if again is None:
            return True
        # an entry leaves the outbox only now: the copy has what the library made of it
        self._data.confirm(done)
        again = self._settle(client) or again
        self._deliver_journal(client)
        self._report(client)
        return again

    def _look_for_versions(self) -> None:
        if not self._checked:
            self._checked = True
            self._box.look_for_versions()

    def _push(self, client: Client) -> set[int]:
        """Send the outbox in order; returns the numbers that the library answered (sections 4.4, 4.5)."""
        done: set[int] = set()
        outbox = self._data.outbox()
        for start in range(0, len(outbox), BATCH):
            entries = self._box.files.described(outbox[start : start + BATCH])
            results = client.post(f"{V}/boxes/{self._data.box_id}/changes", {"entries": entries})["results"]
            self._answered(entries, results, done)
        return done

    def _answered(self, entries: list[dict[str, Any]], results: list[dict[str, Any]], done: set[int]) -> None:
        for entry, result in zip(entries, results, strict=True):
            kind = result["result"]
            if kind == "waiting":
                continue  # depends on an album whose files are not all in the library yet
            done.add(entry["n"])
            if kind == "applied" and entry["op"] == "create":
                self._box.learn(entry["key"], result["id"], result.get("value"))
            elif kind == "conflict" and entry["kind"] in COPY:
                clock = entry["clock"]
                self._logger.warning(
                    "conflict: this box changed %s, the library wins. The box wanted %s, the library has %s. Changed "
                    "by %s at %s (clock %s)",
                    result["what"], result["wanted"], result["library"], entry["by"], clock["time"],
                    "synchronized" if clock["synchronized"] else "not synchronized",
                )  # fmt: skip
                self._data.journal("conflict", what=result["what"], wanted=result["wanted"], library=result["library"])
            elif kind == "conflict":
                self._logger.info(
                    "%s was changed in the library, its value %s stays", result["what"], result["library"]
                )
            elif kind == "rejected":
                self._logger.error("the library rejected the change %s (%s): %s", entry["n"], result["reason"], entry)
                self._data.journal("rejected", entry=entry, reason=result["reason"])

    def _pull(self, client: Client) -> bool | None:
        """Compare the checksums of the copy with the library and fetch every file that differs, whole (4.3).
        Returns whether another pass should follow, None if this one could not finish."""
        for name, info in client.get(f"{V}/catalog")["files"].items():
            if name not in CATALOG_FILES or self._data.checksum(name) == info["sha256"]:
                continue
            data = client.request("GET", f"{V}/catalog/{name}").body
            if sha256_bytes(data) != info["sha256"]:
                return None  # the file changed in between: once more
            for target in self._data.replace_copy(name, data).removed:
                self._box.target_removed(target)
            self._logger.info("catalog file %s fetched", name)
        state = client.request("GET", f"{V}/boxes/{self._data.box_id}/state").body
        again = False
        if sha256_bytes(state) != self._data.checksum(STATE):
            for kind, key, value, revision in self._data.replace_copy(STATE, state).lost:
                self._logger.warning("the library lost the %s %s of this box: sent again", kind, key)
                self._data.add(kind, key, value, base=revision)
                again = True
        return again

    def _settle(self, client: Client) -> bool:
        """Ask for the final id of every provisional id that still occurs here, and send the files the library lacks
        (sections 4.6, 4.8). The question for the id doubles as the question for completeness."""
        again = False
        for provisional in self._data.provisional_ids():
            try:
                answer = client.get(f"{V}/boxes/{self._data.box_id}/ids/target/{provisional}")
            except HttpError as error:
                if error.status == 404:
                    continue
                raise
            if "id" in answer:
                self._box.learn(provisional, answer["id"], answer.get("value"))
                again = True
            elif answer.get("missing"):
                self._box.files.send(client, provisional, answer["missing"])
                again = True
        again = self._box.files.balance(client) or again
        return self._box.images.balance(client) or again

    def statistics(self) -> dict[str, Any]:
        """What the library counted from the journal of this box (section 7): asked when the page of the box shows
        it. The library keeps the journal, the box deletes what was confirmed."""
        client = self.client
        if client is None or not self.connected:
            raise Unreachable("the library cannot be reached")
        return dict(client.get(f"{V}/boxes/{self._data.box_id}/statistics"))

    def _deliver_journal(self, client: Client) -> None:
        """Send the journal in batches; the library confirms up to which number it has the lines (section 7)."""
        while lines := self._data.journal_lines():
            confirmed = client.post(f"{V}/boxes/{self._data.box_id}/journal", {"lines": lines})["confirmed"]
            self._data.journal_confirmed(confirmed)
            if confirmed < lines[-1]["n"]:
                break

    def _report(self, client: Client) -> None:
        """Tell the library what runs here, and what the box did to its versions since the last report."""
        report = self._box.status_report()
        if report == self._reported:
            return
        answer = client.put(f"{V}/boxes/{self._data.box_id}/status", report)
        self._box.reported(answer)
        self._reported = {**report, "history": [], "left": [], "defective": []}
