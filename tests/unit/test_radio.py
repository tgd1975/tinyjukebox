"""Internet radio: radio stations in targets.json, playlist resolution, RadioPlayer, PlayRadio and STMOperation."""

import functools
import http.server
import io
import json
import logging
import shutil
import threading

import pytest

from tests.support import CREATED, SHORT_MP3, TAG_UNMAPPED, FakeRadioPlayer, deliver_rfid, noop, wait_for
from tinyjukebox.albumplayer.radioplayer import RadioPlayer, RadioState, resolve_stream_url
from tinyjukebox.data.activities import Activities
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.sanitycheck import SanityCheck
from tinyjukebox.data.target import TargetAlbum, TargetRadio, check_stream_url
from tinyjukebox.data.targets import Targets
from tinyjukebox.messages import BUTTON_PRESSED, PLAYBACK, Button, PlaybackStatus
from tinyjukebox.rgbled import WAITING, Color
from tinyjukebox.stm import base
from tinyjukebox.stm.playradio import PlayRadio
from tinyjukebox.stm.stmoperation import States, STMOperation

RADIO = {"target-id": "7", "target-type": "radio", "name": "FM4", "url": "https://example.org/fm4", "created": CREATED}


# --- data -------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["http://example.org", "https://example.org:8000/a.m3u?x=1", " http://1.2.3.4/s "])
def test_valid_stream_urls(url):
    assert check_stream_url(url) == url.strip()


@pytest.mark.parametrize(
    "url",
    [None, "", "example.org/stream", "ftp://example.org", "file:///etc/passwd", "http://", "http://a b", "http://[x"],
)
def test_invalid_stream_urls(url):
    with pytest.raises(ValueError, match="url"):
        check_stream_url(url)


def test_radio_station(logger):
    changes = []
    radio = TargetRadio(dict(RADIO), logger, lambda: changes.append(1))
    assert radio.target_type == TargetType.radio
    assert radio.to_dict() == RADIO
    assert radio.sanity_check() == (True, "")
    radio.update(name=" Radio FM4 ")
    radio.update(url="http://example.org/other")
    radio.update(name="Radio FM4")  # unchanged - not written
    assert (radio.name, radio.url, len(changes)) == ("Radio FM4", "http://example.org/other", 2)
    with pytest.raises(ValueError, match="url"):
        radio.update(name="x", url="nope")
    assert radio.name == "Radio FM4"  # nothing changed


def test_broken_radio_station_fails_the_sanity_check(logger):
    radio = TargetRadio(dict(RADIO, url="nope", created="yesterday"), logger, noop)
    passed, errors = radio.sanity_check()
    assert not passed
    assert "created" in errors
    assert "url" in errors


def test_targets_with_albums_and_radio_stations(library, logger):
    radio = library.targets.add_target({"target-type": "radio", "name": "FM4", "url": RADIO["url"]})
    assert isinstance(radio, TargetRadio)
    assert radio.target_id == "3"
    reloaded = Targets(library.targets_file, library.albums_folder, logger)
    assert [r.to_dict() for r in reloaded.radios()] == [radio.to_dict()]
    assert all(isinstance(a, TargetAlbum) for a in reloaded.albums())
    assert len(reloaded.albums()) == 2
    with pytest.raises(ValueError, match="url"):
        library.targets.add_target({"target-type": "radio", "name": "FM4", "url": "nope"})


@pytest.mark.parametrize(
    "entry",
    [
        {k: v for k, v in RADIO.items() if k != "url"},
        dict(RADIO, **{"target-type": "album"}),
    ],
)
def test_a_broken_radio_entry_is_set_aside(tmp_path, logger, entry):
    """The other targets stay usable; the entry is left out and written back as it was (library.md, section 4.21)."""
    filename = tmp_path / "targets.json"
    filename.write_text(json.dumps({"7": entry, "8": dict(RADIO, **{"target-id": "8"})}))
    targets = Targets(str(filename), str(tmp_path), logger)
    assert list(targets.targets) == ["8"]
    targets.add_target({"target-type": "radio", "name": "FM4", "url": RADIO["url"]})
    assert json.loads(filename.read_text())["7"] == entry
    assert "9" in targets.targets  # the id of the entry set aside is not given again


def test_a_field_of_a_newer_version_is_kept(tmp_path, logger):
    filename = tmp_path / "targets.json"
    filename.write_text(json.dumps({"7": dict(RADIO, **{"target-id": "7"}, genre="news")}))
    targets = Targets(str(filename), str(tmp_path), logger)
    station = targets.targets["7"]
    assert isinstance(station, TargetRadio)
    station.update(name="Other name")
    assert json.loads(filename.read_text())["7"]["genre"] == "news"


def test_radio_tags_get_no_activity_and_are_checked(library, logger):
    radio = library.targets.add_target({"target-type": "radio", "name": "FM4", "url": RADIO["url"]})
    library.tags.assign_target(TAG_UNMAPPED, TargetType.radio, radio.target_id)
    library.activities.rebuild_activities()
    assert TAG_UNMAPPED not in library.activities.activities
    check = SanityCheck(logger, library.targets, library.tags, library.activities)
    assert check._cross_check_tags_and_targets() is False  # only the tag linked to the missing album 99
    library.tags.assign_target(TAG_UNMAPPED, TargetType.radio, "1")  # an album id
    reloaded = Activities(library.activities_file, library.tags, logger)
    assert TAG_UNMAPPED not in reloaded.activities


# --- playlists --------------------------------------------------------------------------------------------------------


class Opener:
    """Stands in for urllib.request.urlopen - records the requested urls."""

    def __init__(self, content):
        self.content = content
        self.requested = []

    def __call__(self, url, timeout):
        self.requested.append(url)
        return io.BytesIO(self.content.encode())


@pytest.mark.parametrize(
    ("url", "content", "expected"),
    [
        ("http://r.org/live.mp3", "", "http://r.org/live.mp3"),  # no playlist - not loaded
        ("http://r.org/live.m3u", "#EXTM3U\n#EXTINF:-1,FM4\nhttp://s.org/fm4\n", "http://s.org/fm4"),
        ("http://r.org/a/live.m3u", "﻿stream.mp3\r\n", "http://r.org/a/stream.mp3"),  # relative entry
        (
            "http://r.org/live.PLS",
            "[playlist]\nNumberOfEntries=1\nFile1=https://s.org/x\nTitle1=X\n",
            "https://s.org/x",
        ),
        ("http://r.org/live.m3u", "#EXTM3U\n#EXT-X-TARGETDURATION:6\nseg1.ts\n", "http://r.org/live.m3u"),  # HLS
    ],
)
def test_resolve_stream_url(url, content, expected):
    opener = Opener(content)
    assert resolve_stream_url(url, opener=opener) == expected
    assert opener.requested == ([] if url.endswith(".mp3") else [url])


def test_playlist_without_stream():
    with pytest.raises(ValueError, match="no http"):
        resolve_stream_url("http://r.org/live.m3u", opener=Opener("#EXTM3U\nftp://s.org/x\n"))


# --- RadioPlayer ------------------------------------------------------------------------------------------------------


class FakePipeline:
    """Plays until it is stopped or ``end`` is called; ``heard()``: its sound is heard."""

    def __init__(self, source, heard):
        self.source = source
        self.heard = heard
        self._ended = threading.Event()
        self._result = (True, "")
        self.stopped = False

    def end(self, success=True, errors=""):
        self._result = (success, errors)
        self._ended.set()

    def wait(self):
        self._ended.wait()
        return self._result

    def stop(self):
        self.stopped = True
        self._ended.set()


@pytest.fixture
def pipelines():
    return []


@pytest.fixture
def make_player(logger, pipelines):
    players = []

    def make(url="http://r.org/live", **kwargs):
        def factory(source, heard):
            pipeline = FakePipeline(source, heard)
            pipelines.append(pipeline)
            return pipeline

        kwargs.setdefault("pipeline_factory", factory)
        kwargs.setdefault("retry_delays", (0.01,))
        states: list[RadioState] = []
        player = RadioPlayer(logger, url, states.append, **kwargs)
        player.states = states  # type: ignore[attr-defined]
        players.append(player)
        return player

    yield make
    for player in players:
        player.stop()


def test_player_plays_and_stops(make_player, pipelines):
    played = []
    player = make_player(on_play=lambda: played.append(1))
    player.start()
    assert wait_for(lambda: played == [1])  # ffmpeg and aplay run, but nothing is heard yet
    assert pipelines[0].source == "http://r.org/live"
    assert player.state == RadioState.CONNECTING
    pipelines[0].heard()
    assert player.states == [RadioState.CONNECTING, RadioState.PLAYING]
    player.stop()
    assert pipelines[0].stopped
    assert player.state == RadioState.STOPPED
    assert player.states == [RadioState.CONNECTING, RadioState.PLAYING]  # no callbacks after stop


def test_player_connects_again_after_a_failure(make_player, pipelines):
    player = make_player()
    player.start()
    assert wait_for(lambda: pipelines)
    pipelines[0].end(False, "Connection refused")
    assert wait_for(lambda: len(pipelines) == 2)
    assert player.error == "Connection refused"  # still while it connects again
    pipelines[1].heard()
    assert player.state == RadioState.PLAYING
    assert RadioState.RETRYING in player.states
    assert player.error is None  # playing again
    pipelines[1].end(True, "")
    assert wait_for(lambda: player.error == "stream ended" or len(pipelines) == 3)


def test_player_reports_playlist_errors(make_player, pipelines):
    def resolve(url):
        raise OSError("timed out")

    player = make_player(resolve=resolve, retry_delays=(60,))
    player.start()
    assert wait_for(lambda: player.state == RadioState.RETRYING)
    assert player.error == "cannot load playlist: timed out"
    assert not pipelines
    player.stop()  # ends the wait for the next attempt
    assert player.state == RadioState.STOPPED


def test_player_stopped_while_loading_the_playlist_does_not_play(make_player, pipelines):
    loading, release = threading.Event(), threading.Event()

    def resolve(url):
        loading.set()
        release.wait(5)
        return url

    player = make_player(resolve=resolve)
    player.start()
    assert loading.wait(5)
    player.stop(timeout=0)
    release.set()
    assert not wait_for(lambda: pipelines, timeout=0.3)


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture
def http_folder(tmp_path):
    """Local http server for the files in a folder - the url of the folder."""
    shutil.copy(SHORT_MP3, tmp_path / "stream.mp3")
    (tmp_path / "live.m3u").write_text("#EXTM3U\nstream.mp3\n")
    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(_QuietHandler, directory=str(tmp_path))
    )
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def test_player_plays_a_real_stream(logger, http_folder):
    """ffmpeg (null output, see conftest) plays the playlist's stream - heard once 200 ms of it fill aplay's buffer; the
    end of the stream is a failure."""
    states: list[RadioState] = []
    player = RadioPlayer(logger, f"{http_folder}/live.m3u", states.append, retry_delays=(30,), buffer_time=200)
    player.start()
    try:
        assert wait_for(lambda: player.state == RadioState.RETRYING, timeout=10)
        assert player.error == "stream ended"
        assert states[:3] == [RadioState.CONNECTING, RadioState.PLAYING, RadioState.RETRYING]
    finally:
        player.stop()


def test_the_start_of_a_real_stream_is_measured_phase_by_phase(logger, http_folder, caplog):
    """For the library (docs/developer/library.md, section 4.22): playlist, ffmpeg and aplay, the first audio, and when
    aplay's buffer (ms, as in the configuration) is full and the sound is heard."""
    caplog.set_level(logging.INFO)
    player = RadioPlayer(logger, f"{http_folder}/live.m3u", retry_delays=(30,), buffer_time=200)
    player.start()
    try:
        assert wait_for(lambda: player.state == RadioState.RETRYING, timeout=10)
    finally:
        player.stop()
    events = {
        record.diagnostic["code"]: record.diagnostic for record in caplog.records if hasattr(record, "diagnostic")
    }
    startup = events["radio.startup"]["params"]
    assert (startup["kind"], startup["attempt"], startup["buffer_ms"]) == ("playlist", 0, 200)
    assert 0 <= startup["resolve_ms"] <= startup["pipeline_ms"] <= startup["first_audio_ms"] <= startup["audible_ms"]
    assert events["radio.startup"]["text"] == "start of a stream of 127.0.0.1"  # the library never gets the address
    retry = events["radio.retry"]["params"]
    assert (retry["reason"], retry["host"], retry["delay_s"]) == ("ended", "127.0.0.1", 30)


# --- PlayRadio and STMOperation ---------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _radio_players():
    FakeRadioPlayer.players.clear()


def test_play_radio(logger, rgbled):
    station = TargetRadio(dict(RADIO), logger, noop)
    playback = PlayRadio(logger, rgbled, station, FakeRadioPlayer)
    playback.start()
    assert rgbled.last() == ("color", Color.BLUE)
    player = FakeRadioPlayer.players[0]
    player._set(RadioState.CONNECTING)
    assert rgbled.last() == ("sequence", WAITING)  # until the sound is heard: it plays as soon as it is there
    player._set(RadioState.RETRYING)
    assert rgbled.last() == ("color", Color.AQUA)
    player._set(RadioState.PLAYING)
    playback.cmd_next_handler()
    playback.cmd_prev_handler()
    playback.cmd_restart_album()
    playback.wait_until_idle()
    assert len(FakeRadioPlayer.players) == 1  # next and prev do nothing while it plays
    station.update(url="http://example.org/new")
    assert playback.get_info() == {"target_id": "7", "target_type": "radio", "name": "FM4",
                                   "url": RADIO["url"], "state": "playing", "error": None}  # fmt: skip
    playback.stop()
    assert playback.get_info()["state"] == "stopped"


@pytest.mark.parametrize("with_radio", [True, False])
def test_stmoperation_plays_radio_stations(library, pubsub, rgbled, logger, with_radio):
    radio = library.targets.add_target({"target-type": "radio", "name": "FM4", "url": RADIO["url"]})
    library.tags.assign_target(TAG_UNMAPPED, TargetType.radio, radio.target_id)

    def album_factory(target, activity):
        raise AssertionError("no album")

    def radio_factory(station):
        return PlayRadio(logger, rgbled, station, FakeRadioPlayer)

    stm = STMOperation(pubsub, logger, rgbled, library.tags, library.targets, library.activities, album_factory,
                       poll_interval=60, radio_factory=radio_factory if with_radio else None)  # fmt: skip
    stm.start()
    try:
        deliver_rfid(pubsub, TAG_UNMAPPED)
        stm.wait_until_idle()
        if with_radio:
            assert stm.get_active_state() == States.PLAY_ALBUM
            playback = stm.get_album()
            assert playback is not None
            assert playback.get_info()["name"] == "FM4"
        else:
            assert stm.get_active_state() == States.BROKEN_ALBUM
    finally:
        stm.stop()


def no_album(target, activity):
    raise AssertionError("no album expected")


def radio_test_stm(library, pubsub, rgbled, logger, duration=60.0, **kwargs):
    def radio_factory(station):
        return PlayRadio(logger, rgbled, station, FakeRadioPlayer)

    return STMOperation(pubsub, logger, rgbled, library.tags, library.targets, library.activities, no_album,
                        poll_interval=60, radio_factory=radio_factory, radio_test_duration=duration,
                        **kwargs)  # fmt: skip


def test_radio_test_after_a_long_time_without_tag(library, pubsub, rgbled, logger):
    stm = radio_test_stm(library, pubsub, rgbled, logger, idle_blink_after=0.05)
    stm.start()
    try:
        assert wait_for(lambda: stm.get_active_state() == States.IDLE_LONG)
        stm.test_radio(TargetRadio(dict(RADIO, **{"target-id": "test"}), logger, noop))
        stm.wait_until_idle()
        assert stm.get_active_state() == States.TEST_RADIO
        stm.stop_radio_test()
        stm.wait_until_idle()
        assert stm.get_active_state() == States.WAIT_FOR_TAG
    finally:
        stm.stop()


def test_radio_test_ends_by_itself(library, pubsub, rgbled, logger):
    stm = radio_test_stm(library, pubsub, rgbled, logger, duration=0.1)
    stm.start()
    try:
        station = TargetRadio(dict(RADIO, **{"target-id": "test"}), logger, noop)
        stm.test_radio(station)
        stm.wait_until_idle()
        assert stm.get_active_state() == States.TEST_RADIO
        assert wait_for(lambda: stm.get_active_state() == States.WAIT_FOR_TAG)
        assert stm.get_album() is None
        # no test while a tag is on the box
        deliver_rfid(pubsub, TAG_UNMAPPED)
        stm.wait_until_idle()
        with pytest.raises(RuntimeError, match="tag is on the box"):
            stm.test_radio(station)
    finally:
        stm.stop()


def test_queued_radio_test_does_not_start_after_a_tag(library, pubsub, rgbled, logger):
    """A test that is still queued when a tag arrives ends right away - the tag wins."""
    stm = radio_test_stm(library, pubsub, rgbled, logger)
    stm.start()
    try:
        stm._current_uid = TAG_UNMAPPED  # the tag arrived between the check and the processing of the test event
        stm._test_station = TargetRadio(dict(RADIO), logger, noop)
        stm._test_radio_state.on_entry()  # returns TEST_END
        assert stm._test_radio_state.get_album() is None
    finally:
        stm._current_uid = None
        stm.stop()


# --- pause ------------------------------------------------------------------------------------------------------------


@pytest.fixture
def radio(logger, rgbled, pubsub):
    playback = PlayRadio(logger, rgbled, TargetRadio(dict(RADIO), logger, noop), FakeRadioPlayer, pubsub)
    playback.start()
    yield playback
    if playback.get_info()["state"] != "stopped":
        playback.stop()


def radio_state(playback):
    playback.wait_until_idle()
    return playback.get_info()["state"]


@pytest.mark.parametrize("button", [Button.PLAY, Button.NEXT, Button.PREV])
def test_pause_ends_the_stream_and_buttons_connect_again(radio, pubsub, rgbled, button):
    first = FakeRadioPlayer.players[-1]
    pubsub.deliver(BUTTON_PRESSED, Button.PAUSE)
    assert radio_state(radio) == "paused"
    assert first.state.value == "stopped"
    assert radio.get_info()["error"] is None
    assert rgbled.last() == ("alternate", Color.WHITE, Color.BLUE)
    pubsub.deliver(BUTTON_PRESSED, Button.PAUSE)  # paused already
    pubsub.deliver(BUTTON_PRESSED, Button.VOLUME_UP)
    assert radio_state(radio) == "paused"
    pubsub.deliver(BUTTON_PRESSED, button)
    assert radio_state(radio) == "playing"
    assert len(FakeRadioPlayer.players) == 2  # live again: a new connection
    assert FakeRadioPlayer.players[-1].started
    assert rgbled.last() == ("color", Color.BLUE)
    radio.stop()
    assert pubsub.handlers(BUTTON_PRESSED) == []
    assert pubsub.messages(PLAYBACK) == [
        PlaybackStatus.PLAYING, PlaybackStatus.PAUSED, PlaybackStatus.PLAYING, PlaybackStatus.STOPPED,
    ]  # fmt: skip


def test_commands_pause_and_play(radio):
    radio.cmd_pause()
    assert radio_state(radio) == "paused"
    radio.cmd_play()
    assert radio_state(radio) == "playing"
    radio.cmd_pause()
    radio.cmd_next_handler()
    assert radio_state(radio) == "playing"
    radio.cmd_pause()
    radio.cmd_prev_handler()
    assert radio_state(radio) == "playing"
    radio.cmd_pause()
    radio.cmd_restart_album()  # still nothing for radio stations
    assert radio_state(radio) == "paused"


def test_radio_pauses_without_headset_and_continues_with_it(radio):
    radio.cmd_output_lost()
    assert radio_state(radio) == "paused"
    radio.cmd_output_changed()  # the headset is back in time
    assert radio_state(radio) == "playing"
    radio.cmd_output_lost()
    radio.wait_until_idle()
    radio.pause.since -= base.RESUME_WINDOW + 1
    radio.cmd_output_changed()  # too late
    assert radio_state(radio) == "paused"
    radio.cmd_play()
    radio.cmd_pause()
    radio.cmd_output_lost()
    radio.cmd_output_changed()  # paused by the user before - stays paused
    assert radio_state(radio) == "paused"


def test_radio_connects_again_on_a_new_output(radio):
    first = FakeRadioPlayer.players[-1]
    radio.cmd_output_changed()  # the headset has connected while the station plays
    assert radio_state(radio) == "playing"
    assert first.state.value == "stopped"
    assert len(FakeRadioPlayer.players) == 2


def test_radio_without_pubsub_ignores_buttons(logger, rgbled):
    playback = PlayRadio(logger, rgbled, TargetRadio(dict(RADIO), logger, noop), FakeRadioPlayer)
    assert playback.get_info()["state"] == "connecting"  # not started yet
    playback.start()
    playback.cmd_pause()
    assert radio_state(playback) == "paused"
    playback.stop()


def test_radio_that_cannot_start(logger, rgbled):
    def broken(logger, url, on_state):
        raise OSError("no ffmpeg")

    playback = PlayRadio(logger, rgbled, TargetRadio(dict(RADIO), logger, noop), broken)
    with pytest.raises(OSError, match="no ffmpeg"):
        playback.start()
