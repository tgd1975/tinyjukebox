"""Entries of Spotify in the catalog: links, name and cover from Spotify, the REST interface and the console."""

import io
import json
import logging

import pytest

from tests.support import CREATED, TAG_UNMAPPED
from tests.unit.test_web import JPEG, _fast_server, certificate, make_box, quote  # noqa: F401 - fixtures
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.target import TargetSpotify, check_spotify_link, spotify_link
from tinyjukebox.data.targets import Targets
from tinyjukebox.spotify import oembed
from tinyjukebox.spotify.oembed import Found
from tinyjukebox.stm.stmoperation import States

ENTRY = {
    "target-id": "9",
    "target-type": "spotify",
    "name": "Die drei ???",
    "uri": "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M",
    "created": CREATED,
}
LINK = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"


@pytest.mark.parametrize(
    ("link", "uri"),
    [
        (LINK, "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"),
        ("https://open.spotify.com/intl-de/album/4aawyAB9vmqN3uQ7FjRGTy", "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"),
        ("https://open.spotify.com/embed/show/38bS44xjbVVZ3No3ByF1dJ", "spotify:show:38bS44xjbVVZ3No3ByF1dJ"),
        (" spotify:audiobook:7iHfbu1YPACw6oZPAFJtqe ", "spotify:audiobook:7iHfbu1YPACw6oZPAFJtqe"),
        ("http://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl", "spotify:track:11dFghVXANMlKmJXsNCbNl"),
    ],
)
def test_links(link, uri):
    assert check_spotify_link(link) == uri


@pytest.mark.parametrize(
    "link",
    [None, "", "https://example.org/album/abc", "https://open.spotify.com/user/abc", "spotify:album",
     "spotify:album:a/b", "https://open.spotify.com/album/abc/def", "ftp://open.spotify.com/album/abc"],
)  # fmt: skip
def test_invalid_links(link):
    with pytest.raises(ValueError, match=r"link|Spotify|no album"):
        check_spotify_link(link)


def test_entry(logger):
    changes = []
    entry = TargetSpotify(dict(ENTRY), logger, lambda: changes.append(1))
    assert entry.target_type == TargetType.spotify
    assert entry.kind == "playlist"
    assert entry.to_dict() == ENTRY
    assert entry.sanity_check() == (True, "")
    entry.update(name=" Die drei ??? – Folge 1 ")
    entry.update(uri="https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy")
    entry.update(name="Die drei ??? – Folge 1")  # unchanged - not written
    assert (entry.name, entry.uri, entry.kind, len(changes)) == (
        "Die drei ??? – Folge 1", "spotify:album:4aawyAB9vmqN3uQ7FjRGTy", "album", 2)  # fmt: skip
    with pytest.raises(ValueError, match=r"open\.spotify\.com"):
        entry.update(name="x", uri="nope")
    assert entry.name == "Die drei ??? – Folge 1"


def test_broken_entry_fails_the_sanity_check(logger):
    passed, errors = TargetSpotify(dict(ENTRY, uri="nope"), logger, lambda: None).sanity_check()
    assert not passed
    assert "Spotify" in errors


def test_targets_keep_entries(library, logger):
    entry = library.targets.add_target({"target-type": "spotify", "name": "x", "uri": LINK})
    assert isinstance(entry, TargetSpotify)
    assert entry.uri == "spotify:playlist:37i9dQZF1DXcBWIGoYBM5M"
    reloaded = Targets(library.targets_file, library.albums_folder, logger)
    assert [e.to_dict() for e in reloaded.spotify_entries()] == [entry.to_dict()]


# --- oEmbed -----------------------------------------------------------------------------------------------------------


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class opener:  # noqa: N801 - used like a function
    """An opener of urllib that answers with ``answers``, one after the other, and keeps what was asked."""

    def __init__(self, answers):
        self.answers = answers
        self.asked = []

    def __call__(self, url, timeout):
        self.asked.append(url)
        answer = self.answers[len(self.asked) - 1]
        if isinstance(answer, Exception):
            raise answer
        return Response(answer)


def answer(title="Global Warming", width=300, height=300):
    return json.dumps({"title": title, "thumbnail_url": "https://i.scdn.co/image/x", "thumbnail_width": width,
                       "thumbnail_height": height}).encode()  # fmt: skip


def test_name_and_cover_from_spotify():
    open_url = opener([answer(), JPEG])
    found = oembed.look_up("spotify:album:4aawyAB9vmqN3uQ7FjRGTy", logging.getLogger("t"), open_url)
    assert found == Found("Global Warming", JPEG)
    assert open_url.asked[0] == oembed.OEMBED + "https%3A%2F%2Fopen.spotify.com%2Falbum%2F4aawyAB9vmqN3uQ7FjRGTy"


def test_a_podcast_gets_no_name_and_only_a_square_cover():
    log = logging.getLogger("t")
    assert oembed.look_up("spotify:show:abc", log, opener([answer("Newest episode"), JPEG])) == Found(None, JPEG)
    assert oembed.look_up("spotify:show:abc", log, opener([answer("Newest episode", 640, 360)])) == Found()


def test_nothing_from_spotify():
    log = logging.getLogger("t")
    assert oembed.look_up("spotify:audiobook:abc", log, opener([OSError("timeout")])) == Found()
    assert oembed.look_up("spotify:album:abc", log, opener([b"<html>"])) == Found()
    assert oembed.look_up("spotify:album:abc", log, opener([answer(), b"not an image"])) == Found("Global Warming")
    assert oembed.look_up("spotify:album:abc", log, opener([answer(), OSError("x")])) == Found("Global Warming")


# --- REST -------------------------------------------------------------------------------------------------------------


@pytest.fixture
def spotify(make_box):  # noqa: F811 - the fixture
    """A box whose Spotify answers with a name and a cover - except for podcasts."""
    box = make_box()
    box.control.spotify_lookup = lambda uri: Found(None, JPEG) if ":show:" in uri else Found("Global Warming", JPEG)
    return box


def test_entries_crud(spotify, library):
    admin, user = spotify.admin(), spotify.user()
    assert admin.get("/api/spotify").json == []
    assert user.post("/api/spotify", {"link": LINK}).status == 403
    assert admin.post("/api/spotify", {"link": "https://example.org"}).status == 400
    looked_up = admin.post("/api/spotify-lookup", {"link": LINK}).json
    assert looked_up == {"uri": ENTRY["uri"], "kind": "playlist", "link": spotify_link(ENTRY["uri"]),
                         "name": "Global Warming"}  # fmt: skip
    assert user.post("/api/spotify-lookup", {"link": LINK}).status == 403
    created = admin.post("/api/spotify", {"link": LINK})
    assert created.status == 201
    entry = created.json
    entry_id = entry["id"]
    assert int(entry_id) > 2  # albums, stations and entries share the ids
    assert (entry["name"], entry["uri"], entry["kind"], entry["tags"]) == (
        "Global Warming", ENTRY["uri"], "playlist", [])  # fmt: skip
    assert entry["cover"].startswith("spotify-")
    assert user.get(f"/api/spotify/{entry_id}/cover").content == JPEG
    assert user.get("/api/spotify").json == [entry]
    assert admin.get("/api/radios").json == []
    assert admin.get("/api/spotify/1").status == 404  # an album

    named = admin.post("/api/spotify", {"link": "spotify:album:abc", "name": " Mine "}).json
    assert named["name"] == "Mine"
    podcast = admin.post("/api/spotify", {"link": "https://open.spotify.com/show/abc"}).json
    assert podcast["name"] == "https://open.spotify.com/show/abc"  # Spotify names its newest episode: not taken

    changed = admin.patch(f"/api/spotify/{entry_id}", {"name": "Hörspiel", "link": "spotify:album:xyz"}).json
    assert (changed["name"], changed["uri"], changed["kind"]) == ("Hörspiel", "spotify:album:xyz", "album")
    assert admin.patch(f"/api/spotify/{entry_id}", {"link": "nope"}).status == 400
    with open(library.targets_file) as f:
        stored = json.load(f)[entry_id]
    assert stored | {"created": None, "cover": None} == {"target-id": entry_id, "target-type": "spotify",
                                                         "name": "Hörspiel", "uri": "spotify:album:xyz",
                                                         "created": None, "cover": None}  # fmt: skip
    new_cover = admin.request("PUT", f"/api/spotify/{entry_id}/cover", raw=JPEG).json["cover"]
    assert new_cover != entry["cover"]
    assert entry["cover"] not in spotify.control.images.names()  # the old cover is gone

    assert admin.delete(f"/api/spotify/{entry_id}").status == 204
    assert admin.delete(f"/api/spotify/{entry_id}").status == 404


def test_tag_linked_to_an_entry(spotify):
    admin = spotify.admin()
    entry_id = admin.post("/api/spotify", {"link": LINK}).json["id"]
    path = f"/api/tags/{quote(TAG_UNMAPPED)}/spotify"
    tag = admin.put(path, {"spotify_id": entry_id}).json
    assert (tag["spotify_id"], tag["spotify_exists"], tag["album_id"], tag["radio_id"]) == (entry_id, True, None, None)
    assert admin.put(path, {"spotify_id": "1"}).status == 404  # an album
    assert admin.get(f"/api/spotify/{entry_id}").json["tags"] == [{"uid": TAG_UNMAPPED, "name": "unmapped"}]
    assert TAG_UNMAPPED not in [t["uid"] for t in admin.get("/api/tags?unmapped=1").json]
    assert admin.delete(f"/api/spotify/{entry_id}").status == 409  # linked
    created = admin.post("/api/tags", {"uid": "9:9:9:9", "spotify_id": entry_id}).json
    assert created["spotify_id"] == entry_id
    assert admin.post("/api/tags", {"uid": "9:9:9:8", "spotify_id": entry_id, "album_id": "1"}).status == 400
    assert admin.delete(path).json["spotify_id"] is None


def test_a_tag_of_an_entry_cannot_play_without_a_player(spotify):
    admin = spotify.admin()
    entry_id = admin.post("/api/spotify", {"link": LINK}).json["id"]
    admin.put(f"/api/tags/{quote(TAG_UNMAPPED)}/spotify", {"spotify_id": entry_id})
    spotify.place_tag(TAG_UNMAPPED)
    assert spotify.stmoperation.get_active_state() == States.BROKEN_ALBUM


# --- console ----------------------------------------------------------------------------------------------------------


def test_console(spotify, capsys, monkeypatch, tmp_path):
    from tinyjukebox import cli as cli_module

    monkeypatch.setattr(cli_module, "_confirm", lambda: True)
    cli = cli_module.CLI(lambda *args: None, spotify.control)
    cli.cmd_add_spotify(LINK)
    assert "added entry of Spotify" in capsys.readouterr().out
    entry_id = spotify.control.list_spotify()[0]["id"]
    cli.cmd_list_spotify("")
    assert "'Global Warming' playlist" in capsys.readouterr().out
    cli.cmd_set_spotify_name(f"{entry_id} New name")
    assert "updated name to 'New name'" in capsys.readouterr().out
    cli.cmd_set_spotify_link(f"{entry_id} spotify:album:abc")
    assert "updated link" in capsys.readouterr().out
    image = tmp_path / "cover.jpg"
    image.write_bytes(JPEG)
    cli.cmd_set_spotify_cover(f"{entry_id} {image}")
    assert "has a new cover" in capsys.readouterr().out
    cli.cmd_link_tag_spotify(f"{TAG_UNMAPPED} {entry_id}")
    assert "-> entry of Spotify" in capsys.readouterr().out
    cli.cmd_list_tags("")
    assert f"spotify: {entry_id}" in capsys.readouterr().out
    cli.cmd_remove_spotify(entry_id)
    assert "linked to tags" in capsys.readouterr().out
    cli.cmd_unlink_tag(TAG_UNMAPPED)
    assert f"entry of Spotify {entry_id}" in capsys.readouterr().out
    cli.cmd_remove_spotify(entry_id)
    assert "removed entry of Spotify" in capsys.readouterr().out


def test_the_position_of_a_track_of_spotify_is_no_change_of_the_status():
    from tinyjukebox.web import server

    status = {"state": "play_album", "spotify": {"id": "9", "track": {"index": 2, "position": 31.0}}}
    assert server._without_position(status)["spotify"] == {"id": "9", "track": {"index": 2}}
