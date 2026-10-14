"""Command line interface (CLI) - text front end of BoxControl."""

import json
import os
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

import tinyjukebox.core.ui.tools
from tests.support import (
    ONE_SECOND_MP3,
    TAG_ALBUM,
    TAG_UNMAPPED,
    LoopbackPubSub,
    make_album,
    make_volumes,
    wait_for,
)
from tinyjukebox.backup import verify
from tinyjukebox.cli import CLI
from tinyjukebox.control import BoxControl
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.volumelimits import VolumeLimits
from tinyjukebox.devices.bluetooth.virtual import VirtualHeadset
from tinyjukebox.headset import BluetoothVolumeControl, Headset
from tinyjukebox.images import Images
from tinyjukebox.restore import Place, Restore

CONFIG = {"playlist-filename": "playlist.m3u"}
TINY_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class FakeAlbum:
    def __init__(self, track_id=1):
        self.calls = []
        self.track_id = track_id

    def start(self):
        self.calls.append("start")

    def stop(self):
        self.calls.append("stop")

    def cmd_next_handler(self):
        self.calls.append("next")

    def cmd_prev_handler(self):
        self.calls.append("prev")

    def cmd_restart_album(self):
        self.calls.append("restart")

    def cmd_pause(self):
        self.calls.append("pause")

    def cmd_play(self):
        self.calls.append("play")

    def get_info(self):
        track: dict[str, Any]
        if self.track_id is None:
            track = {"track_id": None, "filename": None, "pos": 0, "length": 0}
        else:
            track = {"track_id": self.track_id, "filename": "short/2.mp3", "pos": 0.5, "length": 0.98}
        return {"target_id": "1", "album_folder": "short", "playlist": "playlist.m3u", "state": "play",
                "tracks": ["short/1.mp3", "short/2.mp3"], "track": track}  # fmt: skip


class FakeSTMOperation:
    def __init__(self):
        self.current_uid = None
        self.album = None
        self.rechecks = 0

    def get_current_uid(self):
        return self.current_uid

    def get_album(self):
        return self.album

    def get_active_state(self):
        return "play_album"

    def recheck_tag(self):
        self.rechecks += 1

    def test_radio(self, station):
        if self.current_uid is not None:
            raise RuntimeError("a tag is on the box")
        self.tested = station

    def stop_radio_test(self):
        self.tested = None


@pytest.fixture
def answers(monkeypatch):
    """Answers for the yes/no questions of the cli; more() is replaced by print."""
    answers: list[bool] = []
    monkeypatch.setattr(tinyjukebox.core.ui.tools, "get_yes_no", lambda: answers.pop(0))
    monkeypatch.setattr(tinyjukebox.core.ui.tools, "more", print)
    return answers


@pytest.fixture
def stmoperation():
    return FakeSTMOperation()


@pytest.fixture
def images(tmp_path):
    return Images(str(tmp_path / "images"))


@pytest.fixture
def control(library, logger, pubsub, stmoperation, images):
    return BoxControl(CONFIG, pubsub, make_volumes(logger), stmoperation, library.tags, library.targets,
                      library.activities, logger, images=images,
                      data_files=[library.tags_file, library.targets_file, library.activities_file])  # fmt: skip


class RecordingCLI(CLI):
    """CLI that keeps the commands it registers (name -> handler)."""

    def __init__(self, control: BoxControl) -> None:
        self.commands: dict[str, Callable[[str], None]] = {}
        super().__init__(self.commands.__setitem__, control)


@pytest.fixture
def cli(control, answers):
    return RecordingCLI(control)


def run(cli, capsys, command, args=""):
    cli.commands[command](args)
    return capsys.readouterr().out


def test_commands_are_registered(cli):
    assert set(cli.commands) == {
        "list_tags", "list_albums", "list_activities", "unmapped_tags", "unmapped_albums", "sanity_checks",
        "list_folders", "unadded_folders",
        "rebuild_activity_json", "set_tag_name", "link_tag_album", "link_current_tag", "unlink_tag", "remove_tag",
        "track_prev", "track_next", "track_pause", "track_play", "restart_album", "set_volume", "get_volume",
        "get_volume_limits", "set_volume_limits", "bluetooth_info", "bluetooth_pair", "bluetooth_unpair",
        "album_info", "system_state", "add_album", "set_album_title", "set_album_cover",
        "add_new_albums", "list_radios", "add_radio", "set_radio_name", "set_radio_url", "set_radio_cover",
        "remove_radio",
        "link_tag_radio", "link_current_tag_radio", "test_radio", "stop_radio_test",
        "library_info", "set_library", "unlink_library", "system_info", "backup", "restore"}  # fmt: skip


def test_library_and_version_on_a_box_without_them(cli, capsys):
    assert "no synchronization" in run(cli, capsys, "library_info")
    assert "set up without slots" in run(cli, capsys, "system_info")
    assert "no synchronization" in run(cli, capsys, "set_library", "https://nas:8866")
    assert "wrong number of arguments" in run(cli, capsys, "set_library")


def test_lists(cli, capsys):
    out = run(cli, capsys, "list_tags")
    assert TAG_ALBUM in out
    assert TAG_UNMAPPED in out
    assert "'album'" in out
    out = run(cli, capsys, "list_albums")
    assert "[1]" in out
    assert "album-folder: short" in out
    assert "[2]" in out
    out = run(cli, capsys, "list_activities")
    assert TAG_ALBUM in out


def test_unmapped_tags_and_albums(cli, capsys):
    out = run(cli, capsys, "unmapped_tags")
    assert TAG_UNMAPPED in out
    assert TAG_ALBUM not in out
    out = run(cli, capsys, "unmapped_albums")
    assert "[2]" in out
    assert "[1]" not in out


def test_link_tag_album(cli, capsys, library, answers, stmoperation):
    answers.append("y")
    out = run(cli, capsys, "link_tag_album", f"{TAG_UNMAPPED} 2")
    assert "link result" in out
    assert library.tags.get_tag(TAG_UNMAPPED).target_id == "2"
    with open(library.tags_file, encoding="utf-8") as f:
        assert json.load(f)[TAG_UNMAPPED]["target"]["id"] == "2"
    assert stmoperation.rechecks == 0  # the tag is not on the box
    assert "album starts now" not in out


def test_link_tag_album_canceled(cli, capsys, library, answers):
    answers.append("n")
    assert "canceled" in run(cli, capsys, "link_tag_album", f"{TAG_UNMAPPED} 2")
    assert library.tags.get_tag(TAG_UNMAPPED).target_id == -1


def test_link_tag_album_errors(cli, capsys):
    assert "wrong number of arguments" in run(cli, capsys, "link_tag_album", "only-one")
    assert "unknown tag-uid" in run(cli, capsys, "link_tag_album", "0:0:0:0 2")
    assert "unknown album-id" in run(cli, capsys, "link_tag_album", f"{TAG_UNMAPPED} 42")


def test_link_tag_album_auto(cli, capsys, library, answers, stmoperation):
    assert "No unlinked tag currently present" in run(cli, capsys, "link_tag_album", "AUTO 2")
    stmoperation.current_uid = TAG_ALBUM  # linked tag -> not used for AUTO
    assert "No unlinked tag currently present" in run(cli, capsys, "link_tag_album", "AUTO 2")
    stmoperation.current_uid = TAG_UNMAPPED
    answers.append("y")
    out = run(cli, capsys, "link_tag_album", "auto 2")
    assert f"Using currently present unlinked tag: {TAG_UNMAPPED}" in out
    assert library.tags.get_tag(TAG_UNMAPPED).target_id == "2"


def test_link_current_tag(cli, capsys, library, answers, stmoperation):
    assert "No unlinked tag currently present" in run(cli, capsys, "link_current_tag")
    stmoperation.current_uid = TAG_UNMAPPED
    out = run(cli, capsys, "link_current_tag")  # without album id: list unmapped albums
    assert "list of all unmapped albums" in out
    assert "[2]" in out
    assert "wrong number of arguments" in run(cli, capsys, "link_current_tag", "1 2")
    answers.append("y")
    run(cli, capsys, "link_current_tag", "2")
    assert library.tags.get_tag(TAG_UNMAPPED).target_id == "2"


def test_linking_tag_on_the_box_starts_album(cli, capsys, library, answers, stmoperation):
    """The tag on the box is linked - the album starts right away (no need to remove and replace the tag)."""
    stmoperation.current_uid = TAG_UNMAPPED
    answers.append("y")
    out = run(cli, capsys, "link_current_tag", "2")
    assert "the tag is on the box - the album starts now." in out
    assert stmoperation.rechecks == 1
    stmoperation.current_uid = TAG_ALBUM  # relinking a linked tag on the box
    answers.append("y")
    out = run(cli, capsys, "link_tag_album", f"{TAG_ALBUM} 2")
    assert "album starts now" in out
    assert stmoperation.rechecks == 2
    assert library.tags.get_tag(TAG_ALBUM).target_id == "2"


def test_set_tag_name(cli, capsys, library):
    assert "updated name to 'blauer Hase'" in run(cli, capsys, "set_tag_name", f"{TAG_ALBUM} blauer Hase")
    assert library.tags.get_tag(TAG_ALBUM).tag_name == "blauer Hase"
    assert "unknown uid" in run(cli, capsys, "set_tag_name", "0:0:0:0 name")
    assert "wrong number of arguments" in run(cli, capsys, "set_tag_name", TAG_ALBUM)


def test_remove_tag(cli, capsys, library, answers):
    answers.append("y")
    assert "removed tag uid" in run(cli, capsys, "remove_tag", TAG_ALBUM)
    assert TAG_ALBUM not in library.tags.tags
    assert TAG_ALBUM not in library.activities.activities
    answers.append("y")
    run(cli, capsys, "remove_tag", TAG_UNMAPPED)  # unmapped tag has no activity
    assert TAG_UNMAPPED not in library.tags.tags
    assert "unknown tag-uid" in run(cli, capsys, "remove_tag", "0:0:0:0")
    assert "wrong number of arguments" in run(cli, capsys, "remove_tag", "")


def test_remove_tag_canceled(cli, capsys, library, answers):
    answers.append("n")
    assert "canceled" in run(cli, capsys, "remove_tag", TAG_ALBUM)
    assert TAG_ALBUM in library.tags.tags


def test_volume(cli, capsys):
    assert "volume of the speaker is set to: 50" in run(cli, capsys, "get_volume")
    assert "volume of the speaker set to 70" in run(cli, capsys, "set_volume", "70")
    assert "volume of the speaker set to 80" in run(cli, capsys, "set_volume", "95")  # capped by max-volume
    assert "must be 0-100" in run(cli, capsys, "set_volume", "101")
    assert "valid number" in run(cli, capsys, "set_volume", "loud")
    assert "wrong number of arguments" in run(cli, capsys, "set_volume", "")


def test_album_commands_without_album(cli, capsys):
    for command in ["track_next", "track_prev", "track_pause", "track_play", "restart_album", "album_info"]:
        assert "no album is active" in run(cli, capsys, command)


def test_album_commands(cli, capsys, stmoperation):
    album = FakeAlbum()
    stmoperation.album = album
    assert "playing next track" in run(cli, capsys, "track_next")
    assert "playing previous track" in run(cli, capsys, "track_prev")
    assert "restarting album" in run(cli, capsys, "restart_album")
    assert "paused" in run(cli, capsys, "track_pause")
    assert "playing" in run(cli, capsys, "track_play")
    assert album.calls == ["next", "prev", "restart", "pause", "play"]
    out = run(cli, capsys, "album_info")
    assert "album_folder: short" in out
    assert "track: [1] 'short/2.mp3' @ 0.5s / 1.0s" in out
    assert " - short/1.mp3" in out


def test_album_info_of_running_album(cli, capsys, stmoperation):
    """Scenario of the former debug scripts: album_info with a running album prints folder, track and tracks."""
    stmoperation.album = FakeAlbum()
    out = run(cli, capsys, "album_info")
    assert "[1] album_folder: short playlist: playlist.m3u state: play" in out
    assert "track: [1] 'short/2.mp3' @ 0.5s / 1.0s" in out
    assert "tracks:\n - short/1.mp3\n - short/2.mp3\n" in out


def test_album_info_without_current_track(cli, capsys, stmoperation):
    stmoperation.album = FakeAlbum(track_id=None)
    out = run(cli, capsys, "album_info")
    assert "album_folder: short" in out
    assert "track:" not in out
    assert " - short/2.mp3" in out


def test_system_state(cli, capsys):
    assert "system state is: play_album" in run(cli, capsys, "system_state")


def test_sanity_checks(cli, capsys):
    assert "success: False" in run(cli, capsys, "sanity_checks")  # TAG_UNKNOWN_ALBUM links to album 99


def test_rebuild_activity_json(cli, capsys, library, answers):
    library.activities.get_activity(TAG_ALBUM).update(2, 0.5, False)
    answers.append("y")
    assert "finished rebuilding" in run(cli, capsys, "rebuild_activity_json")
    assert library.activities.get_activity(TAG_ALBUM).track_id == 0


def test_add_album(cli, capsys, library, answers):
    make_album(library.albums_folder, "new", [("1.mp3", ONE_SECOND_MP3)])
    assert "title must be provided" in run(cli, capsys, "add_album", "new")
    answers.append("y")
    assert "added album with id '3'" in run(cli, capsys, "add_album", "new Das neue Album")
    assert library.targets.get_target("3").album_folder == "new"
    assert library.targets.get_target("3").title == "Das neue Album"
    answers.append("y")
    assert "adding album failed" in run(cli, capsys, "add_album", "does_not_exist Titel")
    assert "folder must be provided" in run(cli, capsys, "add_album", "")
    # a folder with blanks is given in quotes
    make_album(library.albums_folder, "with blanks", [("1.mp3", ONE_SECOND_MP3)])
    answers.append("y")
    assert "added album with id '4'" in run(cli, capsys, "add_album", '"with blanks" Mit Leerzeichen')
    assert library.targets.get_target("4").album_folder == "with blanks"


def test_restore(cli, capsys, control, library, tmp_path, logger):
    assert "no restore here" in run(cli, capsys, "restore", "x.tar.gz")
    folder = os.path.dirname(library.tags_file)
    (tmp_path / "sync").mkdir()
    control.restore = Restore("box", [Place("json", folder)], str(tmp_path / "sync"), logger)
    filename, _ = control.backup().save(str(tmp_path))
    assert "expected: RESTORE FILE" in run(cli, capsys, "restore", "")
    assert "not put back" in run(cli, capsys, "restore", str(tmp_path / "missing.tar.gz"))
    said = run(cli, capsys, "restore", filename)
    assert "without the albums" in said
    assert "RESTORE CONFIRM" in said
    assert os.path.isdir(folder + ".new")
    assert "stays as it is" in run(cli, capsys, "restore", "cancel")
    assert not os.path.exists(folder + ".new")
    run(cli, capsys, "restore", filename)
    assert "starts again" in run(cli, capsys, "restore", "confirm")
    assert control.restore.confirmed


def test_set_album_title_and_cover(cli, capsys, library, tmp_path):
    assert "updated title to 'Kurz und gut'" in run(cli, capsys, "set_album_title", "1 Kurz und gut")
    assert library.targets.get_target("1").title == "Kurz und gut"
    assert "Kurz und gut" in run(cli, capsys, "list_albums")
    assert "wrong number of arguments" in run(cli, capsys, "set_album_title", "1")
    assert "changing album failed" in run(cli, capsys, "set_album_title", "99 Titel")
    image = tmp_path / "bild.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    assert "has a new cover" in run(cli, capsys, "set_album_cover", f"1 {image}")
    cover = library.targets.get_target("1").cover
    assert cover.startswith("album-")
    assert os.listdir(tmp_path / "images") == [cover]  # in the images folder, not in the album folder
    assert "cover.png" not in os.listdir(os.path.join(library.albums_folder, "short"))
    assert "cannot read" in run(cli, capsys, "set_album_cover", f"1 {tmp_path / 'fehlt.png'}")
    image.write_bytes(b"kein bild")
    assert "only jpeg, png and webp" in run(cli, capsys, "set_album_cover", f"1 {image}")
    assert "wrong number of arguments" in run(cli, capsys, "set_album_cover", "1")


def test_folder_lists(cli, capsys, library):
    """As list_folders and unadded_folders of the old box."""
    make_album(library.albums_folder, "new", [("1.mp3", ONE_SECOND_MP3)])
    assert run(cli, capsys, "list_folders").strip().splitlines() == [
        "list of all album folders:", "new", "short", "simple_album_b"
    ]  # fmt: skip
    assert run(cli, capsys, "unadded_folders").strip().splitlines() == ["list of all unadded folders:", "new"]


def test_add_new_albums(cli, capsys, library, answers):
    assert "no new albums found" in run(cli, capsys, "add_new_albums")
    make_album(library.albums_folder, "new_a", [("1.mp3", ONE_SECOND_MP3)])
    make_album(library.albums_folder, "new_b", [("1.mp3", ONE_SECOND_MP3)])
    answers.append("y")
    out = run(cli, capsys, "add_new_albums")
    assert "found 2 new albums" in out
    assert " - new_a  ->  title: new_a" in out
    folders = sorted(t.album_folder for t in library.targets.targets.values())
    assert folders == ["new_a", "new_b", "short", "simple_album_b"]
    assert sorted(t.title for t in library.targets.targets.values() if t.title) == ["new_a", "new_b"]


# --- radio stations --------------------------------------------------------------------------------------------------

URL = "https://example.org/fm4.m3u"


def test_add_list_and_change_radio_stations(cli, capsys, library):
    out = run(cli, capsys, "add_radio", f"{URL} Radio FM4")
    assert "added radio station [3] 'Radio FM4'" in out
    assert "[3] 'Radio FM4' url: https://example.org/fm4.m3u tags: -" in run(cli, capsys, "list_radios")
    assert "updated name to 'FM4'" in run(cli, capsys, "set_radio_name", "3 FM4")
    assert "updated url to 'http://example.org/x'" in run(cli, capsys, "set_radio_url", "3 http://example.org/x")
    radio = library.targets.get_target("3")
    assert (radio.name, radio.url) == ("FM4", "http://example.org/x")


@pytest.mark.parametrize(
    ("command", "args", "message"),
    [
        ("add_radio", URL, "wrong number of arguments"),
        ("add_radio", "nope FM4", "adding radio station failed: url"),
        ("set_radio_name", "3", "wrong number of arguments"),
        ("set_radio_name", "99 FM4", "unknown radio station '99'"),
        ("set_radio_url", "1 http://example.org", "unknown radio station '1'"),  # an album
        ("remove_radio", "", "wrong number of arguments"),
        ("remove_radio", "99", "unknown radio station '99'"),
        ("link_tag_radio", TAG_UNMAPPED, "wrong number of arguments"),
        ("link_tag_radio", f"{TAG_UNMAPPED} 1", "unknown radio station '1'"),
        ("link_tag_radio", "9:9:9:9 1", "unknown tag '9:9:9:9'"),
    ],
)
def test_radio_command_errors(cli, capsys, command, args, message):
    assert message in run(cli, capsys, command, args)


def test_remove_radio_station(cli, capsys, library, answers):
    run(cli, capsys, "add_radio", f"{URL} FM4")
    answers.append("n")
    assert "canceled" in run(cli, capsys, "remove_radio", "3")
    answers.append("y")
    assert "removed radio station [3]" in run(cli, capsys, "remove_radio", "3")
    assert library.targets.radios() == []


def test_linked_radio_station_cannot_be_removed(cli, capsys, answers):
    run(cli, capsys, "add_radio", f"{URL} FM4")
    answers.append("y")
    run(cli, capsys, "link_tag_radio", f"{TAG_UNMAPPED} 3")
    answers.append("y")
    out = run(cli, capsys, "remove_radio", "3")
    assert f"radio station is linked to tags - unlink them first. ({TAG_UNMAPPED})" in out


def test_link_tag_radio(cli, capsys, library, answers, stmoperation):
    run(cli, capsys, "add_radio", f"{URL} FM4")
    answers.append("n")
    assert "canceled" in run(cli, capsys, "link_tag_radio", f"{TAG_UNMAPPED} 3")
    answers.append("y")
    out = run(cli, capsys, "link_tag_radio", f"{TAG_UNMAPPED} 3")
    assert f"link result: tag {TAG_UNMAPPED} -> radio station 3" in out
    assert "starts now" not in out
    tag = library.tags.get_tag(TAG_UNMAPPED)
    assert (tag.target_type.name, tag.target_id) == ("radio", "3")
    assert f"[{TAG_UNMAPPED}] 'unmapped' radio: 3" in run(cli, capsys, "list_tags")
    assert f"tags: {TAG_UNMAPPED}" in run(cli, capsys, "list_radios")
    assert TAG_UNMAPPED not in run(cli, capsys, "unmapped_tags")


def test_link_current_tag_radio(cli, capsys, library, answers, stmoperation):
    run(cli, capsys, "add_radio", f"{URL} FM4")
    assert "No unlinked tag" in run(cli, capsys, "link_current_tag_radio", "3")
    stmoperation.current_uid = TAG_UNMAPPED
    assert "[3] 'FM4'" in run(cli, capsys, "link_current_tag_radio")
    assert "wrong number" in run(cli, capsys, "link_current_tag_radio", "3 4")
    answers.append("y")
    out = run(cli, capsys, "link_current_tag_radio", "3")
    assert "the radio station starts now" in out
    assert stmoperation.rechecks == 1
    assert library.tags.get_tag(TAG_UNMAPPED).target_id == "3"


def test_link_tag_radio_auto(cli, capsys, library, answers, stmoperation):
    run(cli, capsys, "add_radio", f"{URL} FM4")
    assert "No unlinked tag" in run(cli, capsys, "link_tag_radio", "AUTO 3")
    stmoperation.current_uid = TAG_UNMAPPED
    answers.append("y")
    assert "radio station 3" in run(cli, capsys, "link_tag_radio", "auto 3")


def test_album_info_of_running_radio_station(cli, capsys, stmoperation):
    class FakeRadio(FakeAlbum):
        def get_info(self):
            return {"target_id": "3", "target_type": "radio", "name": "FM4", "url": URL, "state": "retrying",
                    "error": "stream ended"}  # fmt: skip

    stmoperation.album = FakeRadio()
    out = run(cli, capsys, "album_info")
    assert f"[3] radio 'FM4' url: {URL} state: retrying error: stream ended" in out


def test_unlink_tag(cli, capsys, library, answers, stmoperation):
    answers.append("n")
    assert "canceled" in run(cli, capsys, "unlink_tag", TAG_ALBUM)
    assert library.tags.get_tag(TAG_ALBUM).target_id == "1"
    answers.append("y")
    out = run(cli, capsys, "unlink_tag", TAG_ALBUM)
    assert f"unlinking tag '{TAG_ALBUM}' ('album') from album 1." in out
    assert f"unlinked tag {TAG_ALBUM}." in out
    assert "playback stops" not in out
    assert str(library.tags.get_tag(TAG_ALBUM).target_id) == "-1"
    assert stmoperation.rechecks == 0
    assert "is not linked" in run(cli, capsys, "unlink_tag", TAG_ALBUM)


def test_unlink_current_radio_tag(cli, capsys, library, answers, stmoperation):
    run(cli, capsys, "add_radio", f"{URL} FM4")
    library.tags.assign_target(TAG_UNMAPPED, TargetType.radio, "3")
    stmoperation.current_uid = TAG_UNMAPPED
    answers.append("y")
    out = run(cli, capsys, "unlink_tag", "current")
    assert "from radio station 3." in out
    assert "the tag is on the box - the playback stops." in out
    assert stmoperation.rechecks == 1
    assert "tags: -" in run(cli, capsys, "list_radios")


@pytest.mark.parametrize(("args", "message"), [("", "wrong number"), ("9:9:9:9", "unknown tag"), ("CURRENT", "no tag")])
def test_unlink_tag_errors(cli, capsys, args, message):
    assert message in run(cli, capsys, "unlink_tag", args)


def test_test_radio(cli, capsys, stmoperation):
    run(cli, capsys, "add_radio", f"{URL} FM4")
    assert "testing 'FM4' for a minute" in run(cli, capsys, "test_radio", "3")
    assert (stmoperation.tested.name, stmoperation.tested.url) == ("FM4", URL)
    assert "testing 'http://example.org/x'" in run(cli, capsys, "test_radio", "http://example.org/x")
    assert stmoperation.tested.url == "http://example.org/x"
    assert "no radio test running" in run(cli, capsys, "stop_radio_test")  # state of the fake: play_album
    stmoperation.get_active_state = lambda: SimpleNamespace(value="test_radio")
    assert "radio test stopped" in run(cli, capsys, "stop_radio_test")
    assert stmoperation.tested is None


@pytest.mark.parametrize(
    ("args", "message"),
    [("", "wrong number"), ("99", "unknown radio station '99'"), ("ftp://x.org", "url must start with http")],
)
def test_test_radio_errors(cli, capsys, args, message):
    assert message in run(cli, capsys, "test_radio", args)


def test_test_radio_with_tag_on_the_box(cli, capsys, stmoperation):
    stmoperation.current_uid = TAG_ALBUM
    assert "radio test not possible: a tag is on the box" in run(cli, capsys, "test_radio", URL)


# --- volume limits ----------------------------------------------------------------------------------------------------


def test_volume_limits(cli, capsys):
    out = run(cli, capsys, "get_volume_limits")
    assert "speaker: start 50, maximum 80" in out
    assert "headphones: start 40, maximum 75" in out
    assert "headphones: start 30, maximum 60" in run(cli, capsys, "set_volume_limits", "HEADPHONES 30 60")
    assert "speaker: start 20, maximum 40" in run(cli, capsys, "set_volume_limits", "speaker 20 40")
    assert "volume of the speaker is set to: 40" in run(cli, capsys, "get_volume")  # lowered with the maximum
    assert "is required" in run(cli, capsys, "set_volume_limits", "speaker 50 40")
    assert "unknown volume 'subwoofer'" in run(cli, capsys, "set_volume_limits", "subwoofer 20 40")
    assert "valid numbers" in run(cli, capsys, "set_volume_limits", "speaker loud 40")
    assert "wrong number of arguments" in run(cli, capsys, "set_volume_limits", "speaker 20")
    out = run(cli, capsys, "get_volume_limits")
    assert "speaker: start 20, maximum 40" in out
    assert "headphones: start 30, maximum 60" in out


# --- bluetooth --------------------------------------------------------------------------------------------------------


def test_bluetooth_commands_without_bluetooth(cli, capsys):
    assert "bluetooth is not available" in run(cli, capsys, "bluetooth_info")
    assert "bluetooth is not available" in run(cli, capsys, "bluetooth_pair")
    assert "no headset is paired" in run(cli, capsys, "bluetooth_unpair")


@pytest.fixture
def headset_cli(library, logger, stmoperation, answers, tmp_path, rgbled):
    """The command line of a box with the virtual headset (messages are delivered at once)."""
    from tinyjukebox.albumplayer.simpleplayer import AudioOutput

    pubsub = LoopbackPubSub(logger)
    volume_control = BluetoothVolumeControl(pubsub, logger)
    volumes = make_volumes(logger, None, None, volume_control, VolumeLimits(str(tmp_path / "volume.json"), logger))
    headset = Headset(pubsub, logger, rgbled, AudioOutput(), volumes, volume_control, stmoperation)
    virtual = VirtualHeadset(pubsub, logger, search_time=0.05)
    control = BoxControl(CONFIG, pubsub, volumes, stmoperation, library.tags, library.targets, library.activities,
                         logger, headset=headset, virtual_headset=virtual)  # fmt: skip
    headset.start()
    virtual.start()
    yield SimpleNamespace(cli=RecordingCLI(control), control=control, virtual=virtual)
    virtual.stop()
    headset.stop()


def test_bluetooth_commands(headset_cli, capsys, answers):
    cli = headset_cli.cli
    assert "no headset is paired (volume: start 40, maximum 75)" in run(cli, capsys, "bluetooth_info")
    assert "no headset is paired" in run(cli, capsys, "bluetooth_unpair")
    assert "searching for a headset" in run(cli, capsys, "bluetooth_pair")
    assert "a search is running already" in run(cli, capsys, "bluetooth_pair")
    assert "searching for a headset in pairing mode ..." in run(cli, capsys, "bluetooth_info")
    assert wait_for(lambda: headset_cli.virtual.state.volume == 40)  # connected, the box has set the start value
    out = run(cli, capsys, "bluetooth_info")
    assert "last search: paired" in out
    assert ("[00:00:00:00:00:01] 'Virtual headset' connected: True, battery: 80 %, volume: 40 "
            "(start 40, maximum 75)") in out  # fmt: skip
    assert "volume of the headphones is set to: 40" in run(cli, capsys, "get_volume")
    assert "volume of the headphones set to 75" in run(cli, capsys, "set_volume", "100")
    run(cli, capsys, "set_volume_limits", "headphones 20 30")
    assert "volume: 30 (start 20, maximum 30)" in run(cli, capsys, "bluetooth_info")  # lowered at once
    headset_cli.virtual.switch(False)
    assert "connected: False, battery: -, volume: -" in run(cli, capsys, "bluetooth_info")
    assert "volume of the speaker is set to: 50" in run(cli, capsys, "get_volume")
    answers.append("n")
    assert "canceled" in run(cli, capsys, "bluetooth_unpair")
    answers.append("y")
    out = run(cli, capsys, "bluetooth_unpair")
    assert "removing the pairing of 'Virtual headset'" in out
    assert "pairing removed" in out
    assert "no headset is paired" in run(cli, capsys, "bluetooth_info")


def test_backup_into_a_folder(cli, capsys, tmp_path):
    folder = tmp_path / "my backups"
    folder.mkdir()
    out = run(cli, capsys, "backup", f" {folder} ")
    (plain,) = os.listdir(folder)
    assert out == f"backup written to {folder / plain}: 3 files, {verify(str(folder / plain))['bytes']} bytes.\n"
    assert verify(str(folder / plain))["albums"] is False
    run(cli, capsys, "backup", f"{folder} ALBUMS")
    (full,) = set(os.listdir(folder)) - {plain}
    about = verify(str(folder / full))
    assert (about["albums"], about["files"] > 3) == (True, True)


def test_backup_needs_a_folder(cli, capsys, tmp_path):
    assert run(cli, capsys, "backup") == "expected: BACKUP FOLDER [ALBUMS]\n"
    assert run(cli, capsys, "backup", "albums") == "'albums' is no folder.\n"
    assert run(cli, capsys, "backup", str(tmp_path / "missing")) == f"'{tmp_path / 'missing'}' is no folder.\n"
