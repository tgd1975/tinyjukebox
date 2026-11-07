"""The app on a phone, seen from the library (docs/developer/app.md): pairing with a one-time
password, the kind ``app``, what a phone may do, its volume limits. ``Phone`` is a stand-in that speaks like the
app; the app itself is tested in ``app/`` against a real library."""

import urllib.parse
from typing import Any

import pytest

from tests.support import TAG_ALBUM, TAG_UNMAPPED
from tinyjukebox.sync import INTERFACE, SOFTWARE, handshake
from tinyjukebox.sync.client import Client, HttpError

V = f"/api/sync/v{INTERFACE}"
VERSION = "app-0.40.0"


class Phone:
    """What the app does towards the library: the handshake with the content of the QR code, then requests with its
    own key."""

    def __init__(self, qr: str, box_id: str = "bphone01") -> None:
        split = urllib.parse.urlsplit(qr)
        assert (split.scheme, split.path) == ("tinyjukebox", "pair")
        query = urllib.parse.parse_qs(split.query)
        self.url, self.password = query["library"][0], query["password"][0]
        self.box_id = box_id
        self.key = ""
        self.certificate = ""
        self._n = 0

    def pair(self, name: str = "Pixel 9a") -> None:
        nonce = handshake.new_nonce()
        first = Client(self.url)
        nonce_library = first.post("/api/sync/boxes", {"box": self.box_id, "nonce": nonce})["nonce"]
        certificate = first.peer_certificate or ""
        proof = handshake.proof(self.password, "box", self.box_id, nonce, nonce_library, certificate)
        answer = Client(self.url, certificate=certificate).post(
            "/api/sync/boxes", {"box": self.box_id, "proof": proof, "name": name}
        )
        expected = handshake.proof(self.password, "library", self.box_id, nonce, nonce_library, certificate)
        assert handshake.same(answer["proof"], expected)
        self.key, self.certificate = answer["key"], certificate

    @property
    def client(self) -> Client:
        headers = {
            "Authorization": f"Box {self.box_id}:{self.key}",
            "X-Box-Version": VERSION,
            "X-Box-Interface": str(INTERFACE),
        }
        return Client(self.url, certificate=self.certificate, headers=headers)

    def change(self, kind: str, key: str, value: Any, op: str = "set", by: str = SOFTWARE) -> dict[str, Any]:
        self._n += 1
        entry = {
            "n": self._n, "op": op, "kind": kind, "key": key, "value": value, "by": by, "version": VERSION,
            "clock": {"time": "2026-10-16T10:00:00Z", "synchronized": True}, "base": 0,
        }  # fmt: skip
        result: dict[str, Any] = self.client.post(f"{V}/boxes/{self.box_id}/changes", {"entries": [entry]})["results"][
            0
        ]
        return result


def paired(bed, box_id: str = "bphone01") -> Phone:
    admin = bed.admin()
    answer = admin.post("/api/library/pairings", {"url": bed.url}).json
    assert answer["seconds"] == 600
    assert answer["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg"')  # a document of its own: an image
    phone = Phone(answer["content"], box_id)
    phone.pair()
    return phone


def test_a_phone_is_paired_with_the_one_time_password_and_is_an_app(bed):
    phone = paired(bed)
    overview = bed.admin().get("/api/library/overview").json
    assert [(box["id"], box["kind"], box["name"]) for box in overview["boxes"]] == [("bphone01", "app", "Pixel 9a")]
    assert phone.client.get("/api/sync/interface")["serves"] == [INTERFACE]
    # a phone named itself app-<version>: the catalog comes as it is, with the covers of the albums
    assert "files" in phone.client.get(f"{V}/catalog")


def test_the_one_time_password_is_used_up_and_a_new_one_makes_the_old_one_invalid(bed):
    admin = bed.admin()
    first = Phone(admin.post("/api/library/pairings", {"url": bed.url}).json["content"], "bphone01")
    second = Phone(admin.post("/api/library/pairings", {"url": bed.url}).json["content"], "bphone02")
    with pytest.raises(HttpError) as error:
        first.pair()
    assert error.value.status == 401
    second.pair()
    with pytest.raises(HttpError) as error:
        Phone(f"tinyjukebox:pair?library={urllib.parse.quote(bed.url)}&password={second.password}", "bphone03").pair()
    assert error.value.status == 401
    assert [box["kind"] for box in bed.library.store.boxes()] == ["app"]


def test_only_an_admin_pairs_and_only_an_address_of_https(bed):
    admin = bed.admin()
    assert admin.post("/api/library/pairings", {"url": "http://nas:8443"}).status == 400
    assert admin.post("/api/library/pairings", {"url": bed.url}, token="").status == 401


def test_a_box_is_paired_with_the_bootstrap_password_as_before(bed):
    box = bed.box("box1")
    assert box.synced()
    paired(bed).client.get("/api/sync/interface")  # its first contact
    boxes = {box["id"]: box for box in bed.admin().get("/api/library/overview").json["boxes"]}
    assert {key: entry["kind"] for key, entry in boxes.items()} == {box.sync.data.box_id: "box", "bphone01": "app"}
    # a phone has no page: the library knows where it asked from, and no port
    assert (boxes["bphone01"]["address"], boxes["bphone01"]["page"]) == ("127.0.0.1", None)


def test_a_phone_changes_positions_and_reports_unknown_tags_and_nothing_else(bed):
    box = bed.box("box1")
    assert box.synced()
    phone = paired(bed)
    album = next(key for key, target in bed.catalog("target").items() if target.get("album-folder") == "short")
    position = {"tag-uid": TAG_ALBUM, "target-id": album, "track-id": 1, "track-time": 12.5, "target-type": "album",
                "album-finished": False}  # fmt: skip
    assert phone.change("position", TAG_ALBUM, position)["result"] == "applied"
    assert bed.library.store.box_state("bphone01")["positions"][TAG_ALBUM]["value"]["track-id"] == 1
    # an unknown tag: created by the software, not linked
    new = "04:AA:BB:CC:DD:EE:01"
    unlinked = {
        "tag": {"uid": new, "name": ""},
        "target": {"type": "album", "id": -1},
        "created": "2026-10-16T10:00:00.000000Z",
    }
    assert phone.change("tag", new, unlinked)["result"] == "applied"
    bed.library.settle()
    assert new in bed.catalog("tag")
    # a link, a name given by a person, a target: rejected
    linked = {"created": "2026-10-16 10:00:00", "tag": {"uid": TAG_UNMAPPED}, "target": {"type": "album", "id": album}}
    assert phone.change("tag", TAG_UNMAPPED, linked)["result"] == "rejected"
    assert phone.change("tag", new, unlinked, by="web:anna")["result"] == "rejected"
    assert phone.change("target", "bphone01-1", {"target-type": "radio"}, op="create")["result"] == "rejected"
    assert phone.change("setting", "name", "mine")["result"] == "rejected"
    assert bed.catalog("tag")[TAG_UNMAPPED]["target"]["id"] == -1


def test_a_phone_sends_no_files_and_is_offered_no_versions(bed, tmp_path):
    box = bed.box("box1")
    assert box.synced()
    phone = paired(bed)
    album = next(key for key, target in bed.catalog("target").items() if target.get("album-folder") == "short")
    file = tmp_path / "upload"
    file.write_bytes(b"\xff\xd8 not much")
    for path in (f"{V}/albums/{album}/files/01.mp3", f"{V}/images/album-12345678.jpg"):
        with pytest.raises(HttpError) as error:
            phone.client.request("PUT", path, upload=str(file))
        assert error.value.status == 403
    # reading is as for a box
    manifest = phone.client.get(f"{V}/albums/{album}/manifest")
    name = manifest["order"][0]
    assert phone.client.request("GET", f"{V}/albums/{album}/files/{urllib.parse.quote(name)}").body
    assert phone.client.get("/api/sync/versions") == {"versions": []}
    with pytest.raises(HttpError) as error:
        phone.client.get("/api/sync/versions/0.1.0")
    assert error.value.status == 403


def test_the_volume_limits_of_a_phone_are_set_on_the_page(bed):
    box = bed.box("box1")
    assert box.synced()
    phone = paired(bed)
    admin = bed.admin()
    volume = {"speaker": {"max-volume": 60}, "headphones": {"max-volume": 40}}
    assert admin.patch("/api/library/boxes/bphone01", {"volume": volume}).status == 204
    settings = phone.client.get(f"{V}/boxes/bphone01/state")["settings"]
    assert settings["volume.speaker"]["value"] == {"max-volume": 60}
    assert settings["volume.headphones"]["value"] == {"max-volume": 40}
    overview = admin.get("/api/library/overview").json
    phone_row = next(entry for entry in overview["boxes"] if entry["id"] == "bphone01")
    assert phone_row["settings"]["volume.speaker"] == {"max-volume": 60}
    # a box sets its limits itself, on its own page; and a limit is 0 to 100
    assert admin.patch(f"/api/library/boxes/{box.sync.data.box_id}", {"volume": volume}).status == 400
    assert admin.patch("/api/library/boxes/bphone01", {"volume": {"speaker": {"max-volume": 101}}}).status == 400


def test_the_versions_name_the_boxes_only(bed):
    box = bed.box("box1")
    assert box.synced()
    phone = paired(bed)
    phone.client.put(f"{V}/boxes/bphone01/status", {"version": VERSION, "interface": INTERFACE, "fit": "ok"})
    for version in bed.library.versions():
        assert "bphone01" not in version["boxes"]


def test_a_qr_code_of_an_address_of_the_library(bed):
    admin = bed.admin()
    answer = admin.get(f"/api/library/qr?text={urllib.parse.quote(bed.url + '/app.apk')}")
    assert answer.status == 200
    assert answer.json["svg"].startswith("<svg")
    assert admin.get("/api/library/qr?text=javascript:alert(1)").status == 400


def test_the_library_hands_out_the_app_of_the_version_that_add_a_box_installs(bed, tmp_path):
    from pathlib import Path

    from tests.sync.test_install import anonymous, in_the_archive
    from tinyjukebox.library.releases import app_name

    client = anonymous(bed)
    assert client.get("/app.apk").status == 404  # no app yet
    in_the_archive(bed, "1.0.0", tmp_path)
    assert client.get("/app.apk").status == 404  # a version from before the app
    in_the_archive(bed, "1.1.0", tmp_path)
    files = dict(bed.library.store.versions()["1.1.0"]["files"])
    apk = tmp_path / app_name("1.1.0")
    apk.write_bytes(b"PK the app of 1.1.0")
    folder = Path(bed.library.store.version_file("1.1.0", "archive")).parent
    paths = {kind: str(folder / name) for kind, name in files.items()}
    bed.library.store.add_version(
        bed.library.store.versions()["1.1.0"]["description"], {**paths, "app": str(apk)}, "release"
    )
    answer = client.get("/app.apk")
    assert (answer.status, answer.content) == (200, b"PK the app of 1.1.0")
    assert answer.headers["Content-Type"] == "application/vnd.android.package-archive"
    assert app_name("1.1.0") in answer.headers["Content-Disposition"]
    assert bed.admin().get("/api/library/overview").json["app"]["version"] == "1.1.0"
    phone = paired(bed)
    assert phone.client.get("/api/sync/interface")["app"] == "1.1.0"


def test_the_client_id_of_spotify_reaches_every_phone(bed):
    phone = paired(bed)
    admin = bed.admin()
    client = "0123456789abcdef0123456789abcdef"
    assert admin.put("/api/library/spotify-client", {"client": "not a client id"}).status == 400
    assert admin.put("/api/library/spotify-client", {"client": client.upper()}).json == {"client": client}
    assert phone.client.get(f"{V}/boxes/bphone01/state")["settings"]["spotify-client"]["value"] == client
    assert admin.get("/api/library/overview").json["app"]["spotify_client"] == client
    later = paired(bed, "bphone02")  # a phone paired later gets it with its pairing
    assert later.client.get(f"{V}/boxes/bphone02/state")["settings"]["spotify-client"]["value"] == client
    assert admin.put("/api/library/spotify-client", {"client": ""}).json == {"client": None}
    assert phone.client.get(f"{V}/boxes/bphone01/state")["settings"]["spotify-client"]["value"] is None


def test_the_one_time_password_of_a_phone_never_takes_over_a_box(bed):
    """A phone that pairs with the id of a box: refused - the box keeps its key and stays a box."""
    box = bed.box("box1")
    assert box.synced()
    admin = bed.admin()
    answer = admin.post("/api/library/pairings", {"url": bed.url}).json
    phone = Phone(answer["content"], box.sync.data.box_id)
    with pytest.raises(HttpError) as refused:
        phone.pair()
    assert refused.value.status == 401
    assert {b["id"]: b["kind"] for b in bed.library.store.boxes()}[box.sync.data.box_id] == "box"
    assert box.synced()  # its key still counts
