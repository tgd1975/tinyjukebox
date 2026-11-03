"""The token for the releases, set on the page of the library (docs/developer/library.md, section 8.2), and how many
versions its archive keeps (section 6.13)."""

import base64
import http.server
import json
import os
import shutil
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support import wait_for
from tests.sync.card import Card
from tinyjukebox import __version__
from tinyjukebox.sync.util import read_json

REPOSITORY = "somebody/box"
SCRIPTS = Path(__file__).parents[2] / "scripts"


class GitServer(http.server.ThreadingHTTPServer):
    """A git server without a release: it knows the token "good", and "blind" as one of another repository."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.asked: list[tuple[str, str]] = []  # token and path of every request


class _Handler(http.server.BaseHTTPRequestHandler):
    server: GitServer

    def do_GET(self) -> None:
        token = self.headers.get("Authorization", "").removeprefix("Bearer ")
        self.server.asked.append((token, self.path))
        listing = self.path == f"/repos/{REPOSITORY}/releases?per_page=1"
        status = 401 if token not in ("good", "blind") else 200 if token == "good" and listing else 404
        self.send_response(status)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *args: object) -> None:
        pass


@pytest.fixture
def git_server() -> Iterator[GitServer]:
    server = GitServer()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    server.shutdown()
    server.server_close()


def test_the_token_for_the_releases_is_set_on_the_page(bed, git_server, tmp_path):
    bed.releases.update(
        {
            "api": f"http://127.0.0.1:{git_server.server_port}",
            "repository": REPOSITORY,
            "token-file": str(tmp_path / "no-file"),
        }
    )
    bed.restart_library()
    admin = bed.admin()

    def releases():
        return admin.get("/api/library/overview").json["releases"]

    # without a token the library does not ask, and says what is missing
    assert (releases()["repository"], releases()["token"]) == (REPOSITORY, "missing")
    assert "token" in admin.post("/api/library/releases/check").json["error"]
    assert git_server.asked == []

    # a token that the git server does not know, or that may not read this repository, is not taken over
    refused = {"wrong": "does not know this token", "blind": f"may not read the releases of {REPOSITORY}"}
    for token, reason in refused.items():
        answer = admin.put("/api/library/releases/token", {"token": token})
        assert answer.status == 400, answer.content
        assert reason in answer.json["error"]
    assert admin.put("/api/library/releases/token", {"token": " "}).status == 400
    assert releases()["token"] == "missing"

    # a token that fits counts at once: the library looks for a release with it
    git_server.asked.clear()
    answer = admin.put("/api/library/releases/token", {"token": "good"})
    assert (answer.status, answer.json) == (200, {"unchanged": True})
    assert git_server.asked == [
        ("good", f"/repos/{REPOSITORY}/releases?per_page=1"),
        ("good", f"/repos/{REPOSITORY}/releases/latest"),
    ]
    assert releases()["token"] == "page"
    assert "good" not in admin.get("/api/library/overview").content.decode()  # the page never gets it back

    # ... and it is kept
    assert read_json(str(bed.folder / "library" / "data" / "settings.json"))["releases-token"] == "good"
    bed.restart_library()
    admin = bed.admin()
    assert releases()["token"] == "page"
    assert admin.post("/api/library/releases/check").json == {"unchanged": True}
    assert git_server.asked[-1] == ("good", f"/repos/{REPOSITORY}/releases/latest")


@pytest.mark.skipif(any(shutil.which(tool) is None for tool in ("curl", "openssl")), reason="the script needs both")
def test_the_script_sends_its_token_only_to_the_accepted_key_of_the_library(bed, git_server, tmp_path):
    """scripts/check_release.sh: the library has a certificate of its own. The token for scripts goes only to the
    public key a person accepted the first time, or to the pin that is given (scripts/trust.sh)."""
    bed.releases.update(
        {"api": f"http://127.0.0.1:{git_server.server_port}", "repository": REPOSITORY,
         "token-file": str(tmp_path / "no-file")}
    )  # fmt: skip
    bed.restart_library()
    admin = bed.admin()
    assert admin.put("/api/library/releases/token", {"token": "good"}).status == 200
    token = tmp_path / "library-token"
    token.write_text(admin.post("/api/library/tokens").json["token"] + "\n")
    url = f"https://127.0.0.1:{bed.library.api.port}"
    pin = admin.get("/api/library/overview").json["install"]["pin"]  # the pin of the line in the tab "Boxes"
    home = tmp_path / "home"
    pins = home / ".config" / "tinyjukebox" / "pins"
    environment = {key: value for key, value in os.environ.items() if key != "XDG_CONFIG_HOME"}

    def check(*arguments: str) -> subprocess.CompletedProcess[str]:
        # without a terminal, as in a job: nobody could accept a key there
        return subprocess.run(
            [str(SCRIPTS / "check_release.sh"), *arguments, url, str(token)], env={**environment, "HOME": str(home)},
            capture_output=True, text=True, check=False, timeout=60, start_new_session=True,
        )  # fmt: skip

    git_server.asked.clear()
    done = check()
    assert done.returncode == 1
    assert "is not known yet" in done.stderr
    done = check("--pin", "sha256//" + base64.b64encode(bytes(32)).decode())
    assert done.returncode == 90  # the key of the library is another one: curl sends nothing
    assert git_server.asked == []
    done = check("--pin", pin)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout) == {"unchanged": True}
    assert git_server.asked[-1] == ("good", f"/repos/{REPOSITORY}/releases/latest")  # the library looked
    # a key accepted before is kept for the server ...
    pins.parent.mkdir(parents=True)
    pins.write_text(f"127.0.0.1:{bed.library.api.port} {pin}\n")
    assert check().returncode == 0
    # ... and a server that shows another one later is refused, with the reason
    pins.write_text(f"127.0.0.1:{bed.library.api.port} sha256//{base64.b64encode(bytes(32)).decode()}\n")
    done = check()
    assert done.returncode == 90
    assert "shows another public key than the one given, or accepted before" in done.stderr


def test_only_an_admin_sets_the_token_for_the_releases(bed):
    client = bed.admin()
    assert client.put("/api/library/releases/token", {"token": "good"}, token="").status == 401
    # without a repository in the config there is nothing a token could read
    answer = client.put("/api/library/releases/token", {"token": "good"})
    assert answer.status == 400
    assert "no repository" in answer.json["error"]


def test_a_public_repository_is_asked_without_a_token(bed, empty_card, archive):
    """No token on the page, no token file: the library asks without credentials and fetches the slot for its boxes."""
    from tests.sync.gitserver import GitServer as PublicServer

    git = PublicServer()
    try:
        git.publish(archive("1.0.0"))
        bed.releases.update({"api": git.url.removesuffix("/repos/owner/box"), "repository": "owner/box"})
        bed.platform = "test"
        bed.restart_library()
        assert wait_for(lambda: "1.0.0" in bed.library.store.versions())  # it looks as soon as it runs
        assert git.credentials == {""}
        answer = bed.admin().get("/api/library/overview")
        assert "releases" in answer.json, (answer.status, answer.json)
        assert answer.json["releases"]["token"] == "none"
        assert bed.library.store.versions()["1.0.0"]["state"] == "use"
    finally:
        git.stop()


# --- how many versions the archive keeps (section 6.13) --------------------------------------------------------------


def in_the_archive(bed, version, tmp_path):
    """A version of the box software in the archive of the library, as a release would put it there."""
    files = {}
    for kind, name in (("description", "slot.json"), ("archive", "slot.tar.gz"), ("signature", "slot.tar.gz.sig")):
        files[kind] = str(tmp_path / f"{version}-{name}")
        Path(files[kind]).write_text("{}")
    description = {"version": version, "interface": 1, "platform": "x86_64-debian-trixie", "size": 2, "sha256": "-",
                   "system": {"setup": 1, "slot-script": 1}}  # fmt: skip
    bed.library.store.add_version(description, files, "release")


def test_the_archive_keeps_a_number_of_versions_and_what_a_box_needs(bed, tmp_path, monkeypatch):
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("a", "1.0.0", "confirmed")  # an old version that still lies in a slot of the box
    card.slot("b", __version__, "probation")
    card.activate("b")
    for version in (__version__, *(f"1.0.{n}" for n in range(8))):
        in_the_archive(bed, version, tmp_path)
    bed.library.store.set_version("1.0.6", "dontuse", "admin", "does not work")
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()

    def archived():
        overview = admin.get("/api/library/overview").json
        return {entry["version"] for entry in overview["versions"] if entry["archived"]}, overview["releases"]["keep"]

    assert archived() == ({__version__, *(f"1.0.{n}" for n in range(8))}, 5)  # five, as long as nothing else is set
    answer = admin.put("/api/library/releases/keep", {"versions": 4})
    assert (answer.status, answer.json) == (200, {"keep": 4})
    # what a box runs or holds in a slot stays, and the newest version in the state use; of the others the newest
    # one, a version in the state dontuse first
    assert archived() == ({__version__, "1.0.0", "1.0.5", "1.0.7"}, 4)
    assert len(admin.get("/api/library/overview").json["versions"]) == 9  # the entries and their states stay
    # ... and it is kept
    bed.restart_library()
    admin = bed.admin()
    assert archived() == ({__version__, "1.0.0", "1.0.5", "1.0.7"}, 4)


def test_the_number_of_versions_the_archive_keeps_is_at_least_one_and_set_by_an_admin(bed):
    admin = bed.admin()
    for wrong in (0, -1, "5", None, True, 2.5):
        answer = admin.put("/api/library/releases/keep", {"versions": wrong})
        assert answer.status == 400, wrong
    assert admin.put("/api/library/releases/keep", {"versions": 3}, token="").status == 401
    assert admin.get("/api/library/overview").json["releases"]["keep"] == 5


def test_an_answer_that_is_no_release_ends_nothing(bed, tmp_path, logger):
    """A proxy that answers with a page of its own, a field that is missing: nothing changes, and the thread of the
    releases asks again at its next time instead of ending."""
    from tinyjukebox.library.releases import Releases

    releases = Releases(bed.library.store, {"repository": REPOSITORY, "token": ""}, "test", str(tmp_path), logger)

    class Odd:
        remaining = None

        def release(self, which, etag):
            raise ValueError("Expecting value: line 1 column 1 (char 0)")

    releases._remote = Odd()  # type: ignore[assignment]
    assert "cannot be taken over" in releases.check()["error"]

    def broken(which, etag):
        raise RuntimeError("what nobody expected")

    releases._remote.release = broken  # type: ignore[method-assign]
    releases._check_and_go_on()  # logged; the thread would go on
