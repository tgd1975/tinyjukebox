"""Playing entries of Spotify: Soloist and its events, the pipe, SpotifyPlayer, PlaySpotify, the engine and the box as a
speaker - with a Soloist that answers its commands with events, as its documents describe them."""

import logging
import os
import subprocess
import threading
import time
from types import SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import CREATED, TAG_UNMAPPED, deliver_rfid, noop, wait_for
from tinyjukebox.albumplayer import spotifyplayer
from tinyjukebox.albumplayer.simpleplayer import AudioOutput
from tinyjukebox.albumplayer.spotifyplayer import SpotifyPlayer, SpotifyState, Track
from tinyjukebox.data.activity import ActivityAlbum
from tinyjukebox.data.target import TargetSpotify
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, BUTTON_RELEASED, JOURNAL, Button
from tinyjukebox.rgbled import Color
from tinyjukebox.spotify.account import EXPIRED, Unavailable
from tinyjukebox.spotify.engine import SpotifyEngine
from tinyjukebox.spotify.soloist import Soloist, parse_event
from tinyjukebox.spotify.sound import PipeReader, is_silent
from tinyjukebox.stm.playspotify import PlaySpotify, States
from tinyjukebox.stm.stmoperation import States as StmStates
from tinyjukebox.stm.stmoperation import STMOperation

URI = "spotify:playlist:abc"
TRACKS = [f"spotify:track:t{i}" for i in range(6)]


def item(uri, length=200.0):
    return {"uri": uri, "entity_type": "track",
            "decorations": {"identity": {"name": uri.rsplit(":", 1)[1].upper()},
                            "playback": {"duration_ms": int(length * 1000)}}}  # fmt: skip


class FakeSoloist:
    """Soloist with an entry of ``tracks``: it answers ``soloist ctl`` with the events Soloist sends."""

    def __init__(self, tracks=TRACKS, logged_in=True, autoplay_after=None, signs_in_after=None):
        self.tracks = list(tracks)
        self.logged_in = logged_in
        self.signs_in_after = signs_in_after  # like the real one: "not logged in" first, "logged in" after this
        self.autoplay_after = autoplay_after  # Spotify adds a track after the last one
        self.index: int | None = None
        self.commands: list[str] = []
        self.listener = None
        self.running = True

    def emit(self, **event):
        if self.listener is not None:
            self.listener.event(event)

    def _entries(self):
        index = self.index or 0
        upcoming = [{"uid": str(i), "source": "context", "item": item(uri)}
                    for i, uri in enumerate(self.tracks) if i > index]  # fmt: skip
        if self.autoplay_after:
            upcoming.append({"uid": "a", "source": "autoplay", "item": item(self.autoplay_after)})
        previous = [{"uid": str(i), "source": "context", "item": item(self.tracks[i])}
                    for i in range(index - 1, -1, -1)]  # fmt: skip
        return upcoming, previous

    def _go(self, index):
        self.index = index
        upcoming, previous = self._entries()
        self.emit(type="queue_changed", upcoming=upcoming[:10], previous=previous[:10])
        uri = self.tracks[index] if index < len(self.tracks) else self.autoplay_after
        self.emit(type="track_changed", item=item(uri))
        self.emit(type="position_sync", position={"position_ms": 0, "timestamp_ms": time.time() * 1000, "speed": 1.0})

    def connect(self, listener):
        self.listener = listener
        if self.signs_in_after is not None:
            self.emit(type="auth_state", logged_in=False, is_active=False, device_name="box")
            threading.Timer(self.signs_in_after, self.emit, kwargs={"type": "auth_state", "logged_in": True}).start()
            return
        self.emit(type="auth_state", logged_in=self.logged_in, is_active=False, device_name="box")

    def ctl(self, *args):
        self.commands.append(" ".join(args))
        command = args[0]
        if command == "play" and len(args) == 2:
            self._go(0)
            self.emit(type="playback_changed", status="playing")
        elif (
            command == "next"
            and self.index is not None
            and self.index + 1 < len(self.tracks) + bool(self.autoplay_after)
        ):
            self._go(self.index + 1)
        elif command == "prev" and self.index:
            self._go(self.index - 1)
        elif command == "seek":
            position = {"position_ms": int(args[1]), "timestamp_ms": time.time() * 1000, "speed": 1.0}
            self.emit(type="position_sync", position=position)
        elif command == "pause":
            self.emit(type="playback_changed", status="paused")
        elif command == "play":
            self.emit(type="playback_changed", status="playing")
        return ""

    def json(self, *args):
        upcoming, previous = self._entries()
        return {"upcoming": upcoming, "previous": previous}


class FakeReader:
    def __init__(self):
        self.muted = True
        self.last_sound = time.monotonic()
        self.restarted = 0

    def silence(self):
        self.muted = True

    def sound(self):
        self.muted = False
        self.last_sound = time.monotonic()

    def restart_output(self):
        self.restarted += 1


class FakeEngine:
    def __init__(self, soloist=None, unavailable=None):
        self.soloist = soloist or FakeSoloist()
        self.reader = FakeReader()
        self.unavailable = unavailable
        self.closed = 0
        self.opened = 0

    def open(self, listener):
        if self.unavailable is not None:
            raise Unavailable(self.unavailable, "no")
        self.opened += 1
        self.soloist.connect(listener)
        return self.soloist

    def close(self, listener):
        self.closed += 1


@pytest.fixture
def make_player(logger):
    players = []

    def make(engine=None, track=None, seconds=0.0, **kwargs):
        engine = engine or FakeEngine()
        player: Any = SpotifyPlayer(logger, URI, cast(Any, engine), wait=2.0, retry_delays=(0.05,), **kwargs)
        states: list[SpotifyState] = []
        tracks: list[tuple[int, str | None, bool]] = []
        finished: list[bool] = []
        lost: list[bool] = []
        player.on_state = states.append
        player.on_track = lambda t, s, end: tracks.append((t.index, t.uri, end))
        player.on_finished = lambda: finished.append(True)
        player.on_lost = lambda: lost.append(True)
        player.start(track or Track(), seconds)
        players.append(player)
        player.engine, player.states, player.tracks, player.finished, player.lost = (
            engine,
            states,
            tracks,
            finished,
            lost,
        )
        return player

    yield make
    for player in players:
        player.stop()


def playing(player):
    wait_for(lambda: player.state == SpotifyState.PLAYING, timeout=5)


# --- Soloist ----------------------------------------------------------------------------------------------------------


def test_events_of_trace():
    assert parse_event('1760000000000 {"type": "track_changed", "item": {}}') == {"type": "track_changed", "item": {}}
    assert parse_event("1760000000000 not json") is None
    assert parse_event('1760000000000 {"no": "type"}') is None


def test_soloist_command_line_and_ctl(logger):
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0 if command[-1] != "bad" else 3, '{"upcoming": []}', "no")

    soloist = Soloist("/s/bin/soloist", "KEY", "Kinderzimmer", "/s/data", "/s/cache", logger, device="tinyjukebox",
                      run=run)  # fmt: skip
    assert soloist.command() == ["/s/bin/soloist", "-n", "Kinderzimmer", "-k", "KEY", "-D", "/s/data", "-C", "/s/cache",
                                 "-z", "100", "-i", "100", "-w", "127.0.0.1:0", "-d", "tinyjukebox"]  # fmt: skip
    assert soloist.json("queue", "80", "--json") == {"upcoming": []}
    assert calls[-1] == ["/s/bin/soloist", "ctl", "-D", "/s/data", "queue", "80", "--json"]
    with pytest.raises(Exception, match="no"):
        soloist.ctl("bad")


# --- the pipe ---------------------------------------------------------------------------------------------------------


class FakeAplay:
    def __init__(self, fail=False):
        self.stdin = self
        self.data = b""
        self.fail = fail
        self.ended = False

    def write(self, chunk):
        if self.fail:
            raise BrokenPipeError
        self.data += chunk

    def close(self):
        pass

    def poll(self):
        return 0 if self.ended else None

    def terminate(self):
        self.ended = True

    def wait(self, timeout=None):
        return 0


def test_pipe_reader(tmp_path, logger):
    output = AudioOutput()
    aplays: list[FakeAplay] = []

    def popen(*args: Any, **kwargs: Any) -> Any:
        aplays.append(FakeAplay())
        return aplays[-1]

    reader = PipeReader(str(tmp_path / "pipe"), output, logger, popen=popen)
    reader.open()
    try:
        with open(reader.pipe, "wb", buffering=0) as writer:
            writer.write(b"\x01\x02" * 100)  # muted: dropped
            time.sleep(0.2)
            assert not aplays
            reader.sound()
            writer.write(b"\x03\x04" * 100)
            wait_for(lambda: aplays and len(aplays[0].data) == 200, timeout=3)
            reader.restart_output()
            writer.write(b"\x05" * 10)
            wait_for(lambda: len(aplays) == 2, timeout=3)
    finally:
        reader.close()
    assert not os.path.exists(reader.pipe)
    assert is_silent(b"\x00" * 10)
    assert not is_silent(b"\x00\x01")


def test_pipe_reader_never_goes_on_to_the_speaker_when_the_headset_fails(tmp_path, logger):
    output = AudioOutput()
    output.use_headset(lambda: "bluealsa:DEV=00:11:22:33:44:55,PROFILE=a2dp")
    lost = []
    reader = PipeReader(str(tmp_path / "pipe"), output, logger, popen=lambda *a, **k: cast(Any, FakeAplay(fail=True)))
    reader.on_output_lost = lambda: lost.append(True)
    reader.open()
    try:
        reader.sound()
        with open(reader.pipe, "wb", buffering=0) as writer:
            writer.write(b"\x01" * 100)
            wait_for(lambda: lost, timeout=3)
    finally:
        reader.close()
    assert reader.muted


# --- SpotifyPlayer ----------------------------------------------------------------------------------------------------


def test_plays_from_the_beginning(make_player):
    player = make_player()
    playing(player)
    soloist = player.engine.soloist
    assert soloist.commands[:4] == ["shuffle off", "repeat off", "volume 100", f"play {URI}"]
    assert not player.engine.reader.muted
    track, seconds = player.position()
    assert (track.index, track.uri, track.title, track.length) == (0, TRACKS[0], "T0", 200.0)
    assert seconds < 5


def test_continues_at_the_saved_track(make_player):
    player = make_player(track=Track(3, TRACKS[3]), seconds=42.0)
    playing(player)
    soloist = player.engine.soloist
    assert soloist.commands.count("next") == 3
    assert "seek 42000" in soloist.commands
    track, seconds = player.position()
    assert (track.index, track.uri) == (3, TRACKS[3])
    assert 41 < seconds < 50
    assert player.tracks == []  # the tracks passed on the way are no tracks played


def test_finds_a_track_that_moved_in_the_playlist(make_player):
    soloist = FakeSoloist(tracks=[*TRACKS[:2], "spotify:track:new", *TRACKS[2:]])
    player = make_player(FakeEngine(soloist), track=Track(3, TRACKS[3]), seconds=10.0)  # now at place 4
    playing(player)
    track, _ = player.position()
    assert (track.index, track.uri) == (4, TRACKS[3])


def test_a_track_that_is_gone_starts_the_entry_from_the_beginning(make_player):
    player = make_player(track=Track(2, "spotify:track:gone"), seconds=10.0)
    playing(player)
    track, _ = player.position()
    assert (track.index, track.uri) == (0, TRACKS[0])
    assert "seek 10000" not in player.engine.soloist.commands


def test_buttons(make_player):
    player = make_player()
    playing(player)
    player.next()
    wait_for(lambda: player.position()[0].index == 1, timeout=3)
    assert player.tracks == [(0, TRACKS[0], False)]
    player.pause()
    wait_for(lambda: player.state == SpotifyState.PAUSED, timeout=3)
    assert player.engine.reader.muted
    player.play()
    playing(player)
    player.prev()
    wait_for(lambda: player.position()[0].index == 0, timeout=3)
    player.restart()
    wait_for(lambda: player.engine.soloist.commands.count(f"play {URI}") == 2, timeout=3)


def test_the_phone_has_no_influence(make_player, monkeypatch):
    monkeypatch.setattr(spotifyplayer, "OWN_WINDOW", 0.2)
    player = make_player()
    playing(player)
    soloist = player.engine.soloist
    time.sleep(0.3)  # what follows is no answer to a command of the box
    soloist.emit(type="playback_changed", status="paused")
    soloist.emit(type="options_changed", options={"shuffle": True, "repeat": "context"})
    soloist.emit(type="volume_changed", volume=30)
    wait_for(
        lambda: soloist.commands[-3:] == ["play", "shuffle off", "repeat off"] or "volume 100" in soloist.commands[4:],
        timeout=3,
    )
    assert soloist.commands[-4:] == ["play", "shuffle off", "repeat off", "volume 100"]
    time.sleep(0.3)
    soloist.emit(type="track_changed", item=item("spotify:track:other"))  # something else started on the box
    wait_for(lambda: soloist.commands.count(f"play {URI}") == 2, timeout=3)
    wait_for(lambda: player.position()[0].uri == TRACKS[0], timeout=3)
    soloist.emit(type="device_changed", is_active=False, device_name="phone")  # music started on the phone
    wait_for(lambda: player.state == SpotifyState.ELSEWHERE, timeout=3)
    player.play()
    playing(player)
    assert "activate" in soloist.commands


def test_end_of_the_entry_before_what_spotify_adds(make_player):
    soloist = FakeSoloist(tracks=TRACKS[:2], autoplay_after="spotify:track:added")
    player = make_player(FakeEngine(soloist))
    playing(player)
    player.next()
    wait_for(lambda: player.position()[0].index == 1, timeout=3)
    player.next()  # the track Spotify added starts: the entry is finished
    wait_for(lambda: player.finished, timeout=3)
    assert player.state == SpotifyState.FINISHED
    assert soloist.commands[-1] == "pause"
    assert player.engine.reader.muted


def test_without_login(make_player):
    player = make_player(FakeEngine(FakeSoloist(logged_in=False)))
    wait_for(lambda: player.state == SpotifyState.ERROR, timeout=5)
    assert (player.error, player.lost) == ("not_connected", [True])


def test_soloist_signs_in_after_a_moment(make_player):
    """The real Soloist says "not logged in" first and "logged in" 0.7 s later: the login is there."""
    player = make_player(FakeEngine(FakeSoloist(signs_in_after=0.7)))
    playing(player)
    assert player.lost == []


def test_logged_out_for_a_moment_while_playing(make_player):
    soloist = FakeSoloist()
    player = make_player(FakeEngine(soloist))
    playing(player)
    soloist.emit(type="auth_state", logged_in=False)
    time.sleep(1.0)
    soloist.emit(type="auth_state", logged_in=True)
    time.sleep(1.5)  # past the wait of 2 s since "not logged in"
    assert (player.state, player.lost) == (SpotifyState.PLAYING, [])


def test_logged_out_for_good_while_playing(make_player):
    soloist = FakeSoloist()
    player = make_player(FakeEngine(soloist))
    playing(player)
    soloist.emit(type="auth_state", logged_in=False)
    wait_for(lambda: player.state == SpotifyState.ERROR, timeout=5)
    assert (player.error, player.lost) == ("not_connected", [True])


def test_cannot_play(make_player):
    player = make_player(FakeEngine(unavailable="no_key"))
    wait_for(lambda: player.state == SpotifyState.ERROR, timeout=3)
    assert player.error == "no_key"


def test_soloist_ends(make_player):
    player = make_player()
    playing(player)
    player.exited(1)
    wait_for(lambda: SpotifyState.RETRYING in player.states, timeout=3)
    wait_for(lambda: player.engine.opened == 2 and player.state == SpotifyState.PLAYING, timeout=5)
    player.exited(EXPIRED)
    wait_for(lambda: player.state == SpotifyState.ERROR, timeout=3)
    assert player.error == "expired"


def test_no_sound_while_playing(make_player):
    player = make_player(stall=0.3)
    playing(player)
    player.engine.reader.last_sound = time.monotonic() - 10
    wait_for(lambda: player.engine.soloist.commands.count(f"play {URI}") == 2, timeout=5)


# --- PlaySpotify ------------------------------------------------------------------------------------------------------


class FakePlayer:
    def __init__(self):
        self.state = SpotifyState.STOPPED
        self.error = None
        self.calls = []
        self.track, self.seconds = Track(), 0.0
        self.on_state = self.on_track = self.on_finished = noop
        self.on_lost = self.on_output_lost = noop

    def start(self, track, seconds):
        self.calls.append(("start", track, seconds))
        self.track, self.seconds = track if track.uri else Track(0, TRACKS[0]), seconds
        self.state = SpotifyState.PLAYING
        self.on_state(self.state)

    def position(self):
        return self.track, self.seconds

    def __getattr__(self, name):
        if name in ("pause", "play", "next", "prev", "restart", "output_changed", "stop"):
            return lambda: self.calls.append(name)
        raise AttributeError(name)


ENTRY = {"target-id": "9", "target-type": "spotify", "name": "Hörspiel", "uri": URI, "created": CREATED}


@pytest.fixture
def make_play(logger, rgbled, pubsub):
    made = []

    def make(activity=None, **kwargs):
        player = FakePlayer()
        entry = TargetSpotify(dict(ENTRY), logger, noop)
        play: Any = PlaySpotify(logger, rgbled, entry, activity, lambda log, uri: cast(Any, player), pubsub,
                                update_schedule=0.05, **kwargs)  # fmt: skip
        play.fake = player
        play.start()
        made.append(play)
        play.wait_until_idle()
        return play

    yield make
    for play in made:
        play.stop()


def activity_of(position=None):
    data = {"tag-uid": "1:2:3:4", "target-id": "9", "track-id": 0, "track-time": 0, "album-finished": False,
            "target-type": "spotify", **(position or {})}  # fmt: skip
    return ActivityAlbum(data, noop, logging.getLogger("t"))


def test_play_spotify_starts_at_the_position_and_saves_it(make_play, rgbled):
    activity = activity_of({"track-id": 2, "track-time": 31.5, "track-uri": TRACKS[2]})
    play = make_play(activity)
    assert play.fake.calls[0] == ("start", Track(2, TRACKS[2]), 31.5)
    assert rgbled.last() == ("color", Color.BLUE)
    play.fake.track, play.fake.seconds = Track(3, TRACKS[3]), 12.0
    wait_for(lambda: activity.track_id == 3, timeout=3)
    assert (activity.track_uri, activity.track_time, activity.finished) == (TRACKS[3], 12.0, False)
    assert play.get_info()["track"] == {"track_id": 3, "uri": TRACKS[3], "title": None, "pos": 12.0, "length": None}


def test_a_finished_entry_starts_from_the_beginning(make_play):
    play = make_play(activity_of({"track-id": 4, "track-time": 3, "album-finished": True}))
    assert play.fake.calls[0] == ("start", Track(), 0.0)


def test_play_spotify_buttons(make_play, pubsub):
    play = make_play(activity_of())
    pubsub.deliver(BUTTON_PRESSED, Button.NEXT)
    pubsub.deliver(BUTTON_PRESSED, Button.PAUSE)
    play.wait_until_idle()
    assert play.get_state() == States.PAUSED
    assert "next" in play.fake.calls
    assert "pause" in play.fake.calls
    pubsub.deliver(BUTTON_PRESSED, Button.NEXT)  # in a pause: continues, as an album
    play.wait_until_idle()
    assert play.get_state() == States.PLAY
    assert play.fake.calls[-1] == "play"
    pubsub.deliver(BUTTON_RELEASED, Button.NEXT)
    pubsub.deliver(BUTTON_HELD, Button.PREV)
    play.wait_until_idle()
    assert "restart" in play.fake.calls


def test_play_spotify_finished(make_play, rgbled):
    activity = activity_of()
    play = make_play(activity)
    play.fake.on_finished()
    play.wait_until_idle()
    assert play.get_state() == States.FINISHED
    assert activity.finished
    assert rgbled.last() == ("blink", Color.BLUE)
    play.cmd_prev_handler()  # "back" after the end: from the beginning
    play.wait_until_idle()
    assert play.get_state() == States.PLAY
    assert "restart" in play.fake.calls


def test_play_spotify_headset(make_play):
    play = make_play(activity_of())
    play.cmd_output_changed()  # decided in the worker
    play.wait_until_idle()
    assert "output_changed" in play.fake.calls
    then: list[str] = []
    play.cmd_output_lost(then=lambda: then.append(play.get_state().name))
    play.wait_until_idle()
    assert play.get_state() == States.PAUSED
    assert then == ["PAUSED"]  # the speaker may become the output only once the playback has paused
    play.cmd_output_changed()  # the headset came back within the window: continues
    play.wait_until_idle()
    assert play.get_state() == States.PLAY


def test_play_spotify_headset_back_before_the_pause_was_handled(make_play):
    """Lost and back again at once: the change is handled after the loss, as it came - the playback continues."""
    play = make_play(activity_of())
    play.cmd_output_lost()
    play.cmd_output_changed()
    play.wait_until_idle()
    assert play.get_state() == States.PLAY
    assert play.fake.calls[-2:] == ["pause", "play"]


def test_play_spotify_journal(make_play, pubsub):
    play = make_play(activity_of())
    play.fake.on_track(Track(0, TRACKS[0], "T0", 200.0), 199.0, True)
    notes = [message for topic, message in pubsub.published if topic == JOURNAL]
    assert notes[-1].event == "track"
    assert dict(notes[-1].fields) | {} == {"album": "9", "track": 0, "seconds": 199.0, "to_end": True, "uri": TRACKS[0],
                                           "title": "T0"}  # fmt: skip


def test_play_spotify_that_cannot_play(logger, rgbled, pubsub):
    entry = TargetSpotify(dict(ENTRY), logger, noop)

    def check():
        raise Unavailable("no_key", "no key")

    play = PlaySpotify(logger, rgbled, entry, None, lambda log, uri: cast(Any, FakePlayer()), pubsub, check=check)
    with pytest.raises(Unavailable):
        play.start()


def test_a_test_writes_nothing(make_play, pubsub):
    play = make_play(None, test=True)
    play.fake.on_track(Track(0, TRACKS[0]), 3.0, False)
    assert not [message for topic, message in pubsub.published if topic == JOURNAL]


# --- the engine and the speaker ---------------------------------------------------------------------------------------


class Account:
    def __init__(self, speaker=False, connected=True):
        self.speaker = speaker
        self.connected = connected
        self.cache_dir = "/nonexistent/cache"
        self.folder = "/nonexistent"
        self.on_speaker = self.before_connect = self.after_connect = noop
        self.expired_called = False

    def usable(self):
        return "/bin/soloist"

    def expired(self):
        self.expired_called = True


class EngineSoloist(FakeSoloist):
    def __init__(self):
        super().__init__()
        self.started = self.stopped = 0
        self.on_event = self.on_exit = noop

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1
        self.running = False

    def emit(self, **event):
        self.on_event(event)


class Sound:
    env = None
    device = None

    def __init__(self):
        self.reader = FakeReader()
        self.started = self.stopped = 0

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


@pytest.fixture
def make_engine(logger):
    def make(**account):
        soloists, sounds = [], []

        def soloist_factory(account, sound):
            soloists.append(EngineSoloist())
            return soloists[-1]

        def sound_factory():
            sounds.append(Sound())
            return cast(Any, sounds[-1])

        engine: Any = SpotifyEngine(cast(Any, Account(**account)), AudioOutput(), logger, lambda: "box",
                                    soloist_factory, sound_factory, speaker_pause_limit=0.2)  # fmt: skip
        engine.soloists, engine.sounds = soloists, sounds
        return engine

    return make


class Listener:
    def __init__(self):
        self.events, self.codes = [], []

    def event(self, event):
        self.events.append(event)

    def exited(self, code):
        self.codes.append(code)

    def suspended(self):
        self.codes.append("suspended")

    def output_lost(self):
        self.codes.append("lost")


def test_engine_runs_soloist_for_an_entry(make_engine):
    engine = make_engine()
    listener = Listener()
    soloist = engine.open(listener)
    assert soloist.started == 1
    assert engine.running
    soloist.emit(type="track_changed", item=item(TRACKS[0]))
    assert listener.events[-1]["type"] == "track_changed"
    engine.close(listener)
    assert soloist.stopped == 1
    assert not engine.running


def test_engine_without_login(make_engine):
    with pytest.raises(Unavailable):
        make_engine(connected=False).open(Listener())


def test_speaker(make_engine):
    engine = make_engine(speaker=True)
    plays: list[int] = []
    ends: list[int] = []
    paused: list[bool] = []
    engine.on_speaker_plays, engine.on_speaker_ends = lambda: plays.append(1), lambda: ends.append(1)
    engine.on_speaker_paused = paused.append
    engine.idle()
    wait_for(lambda: engine.running, timeout=3)
    soloist = engine.soloists[-1]
    soloist.emit(type="playback_state", status="playing", item=item(TRACKS[1]), is_active=True)
    assert plays == [1]
    assert engine.speaker_info == {"title": "T1", "uri": TRACKS[1], "state": "playing"}
    engine.listen()
    assert not engine.reader.muted
    engine.button(Button.NEXT)  # in the thread of the commands
    assert engine.wait_until_done(3)
    assert soloist.commands[-1] == "next"
    playback = engine.playback()
    assert playback.get_info()["target_type"] == "speaker"
    soloist.emit(type="playback_changed", status="paused")
    assert paused == [True]
    wait_for(lambda: ends == [1], timeout=3)  # paused for long: the box is free again
    # a tag that plays an entry keeps Soloist, any other tag ends it
    engine.taken(True)
    assert engine.running
    engine.taken(False)
    assert not engine.running


def test_speaker_switched_off(make_engine):
    engine = make_engine(speaker=False)
    engine.idle()
    time.sleep(0.2)
    assert not engine.running


def test_engine_soloist_expired(make_engine):
    engine = make_engine()
    listener = Listener()
    engine.open(listener)
    engine.soloists[-1].on_exit(EXPIRED)
    assert engine.account.expired_called
    assert listener.codes == [EXPIRED]


# --- STMOperation with a speaker --------------------------------------------------------------------------------------


class FakeSpeaker:
    def __init__(self):
        self.calls = []
        self.on_speaker_plays = self.on_speaker_ends = noop
        self.on_speaker_paused = noop

    def idle(self):
        self.calls.append("idle")

    def taken(self, spotify):
        self.calls.append(("taken", spotify))

    def listen(self):
        self.calls.append("listen")

    def silence(self):
        self.calls.append("silence")

    def button(self, button):
        self.calls.append(("button", button))

    def playback(self):
        speaker = self

        class Playback:
            def start(self):
                speaker.listen()

            def stop(self):
                speaker.silence()

            def get_info(self):
                return {"target_type": "speaker", "name": "T1", "uri": TRACKS[1], "state": "playing"}

        return Playback()


def test_the_box_as_a_speaker(library, pubsub, rgbled, logger):
    speaker = FakeSpeaker()
    stm = STMOperation(pubsub, logger, rgbled, library.tags, library.targets, library.activities,
                       lambda target, activity: (_ for _ in ()).throw(AssertionError("no album")), poll_interval=60,
                       speaker=cast(Any, speaker))  # fmt: skip
    stm.start()
    try:
        stm.wait_until_idle()
        assert speaker.calls == ["idle"]
        speaker.on_speaker_plays()
        stm.wait_until_idle()
        assert stm.get_active_state() == StmStates.SPOTIFY_SPEAKER
        album = stm.get_album()
        assert album is not None
        assert album.get_info()["target_type"] == "speaker"
        assert rgbled.last() == ("color", Color.BLUE)
        speaker.on_speaker_paused(True)
        assert rgbled.last() == ("alternate", Color.WHITE, Color.BLUE)
        pubsub.deliver(BUTTON_PRESSED, Button.NEXT)
        assert speaker.calls[-1] == ("button", Button.NEXT)
        speaker.on_speaker_ends()
        stm.wait_until_idle()
        assert stm.get_active_state() == StmStates.WAIT_FOR_TAG
        assert speaker.calls[-2:] == ["silence", "idle"]
        speaker.on_speaker_plays()
        stm.wait_until_idle()
        deliver_rfid(pubsub, TAG_UNMAPPED)  # a tag wins
        stm.wait_until_idle()
        assert stm.get_active_state() == StmStates.UNMAPPED_TAG
        assert ("taken", False) in speaker.calls
        assert "silence" in speaker.calls[-3:]
        speaker.on_speaker_plays()  # ignored while a tag is on the box
        stm.wait_until_idle()
        assert stm.get_active_state() == StmStates.UNMAPPED_TAG
    finally:
        stm.stop()


@pytest.mark.parametrize("blocked", [False, True])
def test_a_new_tag_and_a_box_out_of_service_end_the_speaker(library, pubsub, rgbled, logger, blocked):
    """Not only a known tag: a tag the box has never seen, and any tag on a box that is out of service, end the
    playback of the phone and Soloist."""
    speaker = FakeSpeaker()
    files = SimpleNamespace(subscribe=noop, unsubscribe=noop)
    sync = SimpleNamespace(blocked=blocked, lookup=noop, attached=False, files=files)
    stm = STMOperation(pubsub, logger, rgbled, library.tags, library.targets, library.activities,
                       lambda target, activity: (_ for _ in ()).throw(AssertionError("no album")), poll_interval=60,
                       sync=cast(Any, sync), speaker=cast(Any, speaker))  # fmt: skip
    stm.start()
    try:
        stm.wait_until_idle()
        speaker.on_speaker_plays()
        stm.wait_until_idle()
        assert stm.get_active_state() == StmStates.SPOTIFY_SPEAKER
        deliver_rfid(pubsub, "04:AB:CD:EF:01:02:03")  # never seen before
        stm.wait_until_idle()
        assert stm.get_active_state() == (StmStates.BLOCKED if blocked else StmStates.UNMAPPED_TAG)
        assert ("taken", False) in speaker.calls
        assert "silence" in speaker.calls
    finally:
        stm.stop()


def test_test_of_an_entry(library, pubsub, rgbled, logger):
    speaker = FakeSpeaker()
    tested = []

    def spotify_factory(entry, activity):
        tested.append((entry.uri, activity))
        return FakePlayback()

    stm = STMOperation(pubsub, logger, rgbled, library.tags, library.targets, library.activities, cast(Any, None),
                       poll_interval=60, spotify_factory=spotify_factory, speaker=cast(Any, speaker))  # fmt: skip
    stm.start()
    try:
        stm.wait_until_idle()
        stm.test_radio(TargetSpotify(dict(ENTRY, **{"target-id": "test"}), logger, noop))
        stm.wait_until_idle()
        assert stm.get_active_state() == StmStates.TEST_RADIO
        assert tested == [(URI, None)]
        assert ("taken", True) in speaker.calls
    finally:
        stm.stop()


class FakePlayback:
    def start(self):
        pass

    def stop(self):
        pass

    def get_info(self):
        return {"target_id": "test", "target_type": "spotify"}


# --- Soloist as a process ---------------------------------------------------------------------------------------------

FAKE_SOLOIST = """#!/usr/bin/env python3
import json, sys, time
args = sys.argv[1:]
if args[:1] == ["ctl"]:
    command = args[3:]
    if command[:1] == ["trace"]:
        print("1760000000000 " + json.dumps({"type": "auth_state", "logged_in": True}), flush=True)
        print("1760000000001 " + json.dumps({"type": "track_changed", "item": {"uri": "spotify:track:a"}}), flush=True)
        time.sleep(30)
    elif command[:1] == ["queue"]:
        print(json.dumps({"upcoming": [], "previous": []}))
    elif command[:1] == ["fail"]:
        print("not logged in", file=sys.stderr)
        sys.exit(3)
    sys.exit(0)
print("soloist runs", file=sys.stderr, flush=True)
if "--exit" in open(sys.argv[0] + ".mode").read():
    sys.exit(7)
time.sleep(30)
"""


@pytest.fixture
def fake_soloist(tmp_path):
    program = tmp_path / "soloist"
    program.write_text(FAKE_SOLOIST)
    program.chmod(0o755)
    (tmp_path / "soloist.mode").write_text("")
    return program


def test_soloist_process(fake_soloist, tmp_path, logger):
    soloist = Soloist(str(fake_soloist), "KEY", "box", str(tmp_path / "data"), str(tmp_path / "cache"), logger)
    events: list[dict[str, Any]] = []
    soloist.on_event = events.append
    soloist.start()
    try:
        assert soloist.running
        wait_for(lambda: len(events) >= 2, timeout=10)
        assert [event["type"] for event in events[:2]] == ["auth_state", "track_changed"]
        assert soloist.json("queue", "80", "--json") == {"upcoming": [], "previous": []}
        with pytest.raises(Exception, match="not logged in"):
            soloist.ctl("fail")
    finally:
        soloist.stop()
    assert not soloist.running


def test_soloist_that_ends_by_itself(fake_soloist, tmp_path, logger):
    (tmp_path / "soloist.mode").write_text("--exit")
    soloist = Soloist(str(fake_soloist), "KEY", "box", str(tmp_path / "data"), str(tmp_path / "cache"), logger)
    codes: list[int] = []
    soloist.on_exit = codes.append
    soloist.start()
    try:
        wait_for(lambda: codes == [7], timeout=10)
    finally:
        soloist.stop()
