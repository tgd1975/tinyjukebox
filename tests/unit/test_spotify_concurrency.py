"""Spotify and the threads of the box (docs/developer/architecture.md, sections 4.1 and 10): no handler waits for
Soloist or aplay, a Soloist that keeps failing is started again in a loop, "Connect" runs alone, and whoever ends an
aplay that hangs is not held up by it."""

import datetime
import subprocess
import sys
import threading
import time
from types import FrameType, SimpleNamespace
from typing import Any, cast

import pytest

from tests.support import make_volumes, noop, wait_for
from tests.unit.test_spotify_account import BUILT, Program, Spotify
from tests.unit.test_spotify_play import (  # noqa: F401 - fixtures
    ENTRY,
    TRACKS,
    URI,
    EngineSoloist,
    FakeEngine,
    FakeReader,
    FakeSoloist,
    Listener,
    Sound,
    item,
    make_engine,
    make_player,
    playing,
)
from tests.unit.test_spotify_play import Account as FakeAccount
from tinyjukebox import headset as headset_module
from tinyjukebox.albumplayer.simpleplayer import AudioOutput
from tinyjukebox.albumplayer.spotifyplayer import SpotifyPlayer, SpotifyState, Track
from tinyjukebox.data.target import TargetSpotify
from tinyjukebox.headset import BluetoothVolumeControl, Headset
from tinyjukebox.messages import BLUETOOTH_STATE, BUTTON_PRESSED, BluetoothDevice, BluetoothState, Button
from tinyjukebox.spotify import sound as sound_module
from tinyjukebox.spotify.account import Busy, SpotifyAccount
from tinyjukebox.spotify.engine import SpotifyEngine
from tinyjukebox.spotify.soloist import Soloist, SoloistError
from tinyjukebox.spotify.sound import PipeReader
from tinyjukebox.stm.playspotify import PlaySpotify
from tinyjukebox.stm.playspotify import States as PlayStates
from tinyjukebox.stm.stmoperation import States, STMOperation

DEVICE = BluetoothDevice("AC:80:0A:11:22:33", "WH-CH530")
CONNECTED = BluetoothState(device=DEVICE, connected=True)
GONE = BluetoothState(device=DEVICE)
QUICK = 0.5
"""Seconds within which a handler has returned - whatever hangs behind it."""


class Gate:
    """Holds whoever passes it until it is opened: a ``soloist ctl`` or an ``aplay`` that hangs."""

    def __init__(self):
        self._open = threading.Event()
        self.waiting = threading.Event()

    def passing(self):
        self.waiting.set()
        assert self._open.wait(10), "the gate was never opened"

    def open(self):
        self._open.set()


class SlowSoloist(EngineSoloist):
    """Soloist of the engine whose ``soloist ctl`` hangs until the gate is opened."""

    def __init__(self, gate):
        super().__init__()
        self.gate = gate

    def ctl(self, *args):
        self.gate.passing()
        return super().ctl(*args)


def slow_restart(reader, gate):
    """``aplay`` of ``reader`` takes until the gate is opened to end."""
    restart = reader.restart_output

    def slow():
        gate.passing()
        restart()

    reader.restart_output = slow


# --- no handler waits: the speaker ------------------------------------------------------------------------------------


@pytest.fixture
def speaker_box(library, pubsub, rgbled, logger, monkeypatch):
    """STMOperation with a real SpotifyEngine (the speaker switched on, a Soloist whose commands hang until the gate
    is opened) and the real Headset - the phone plays on the box (SPOTIFY_SPEAKER)."""
    monkeypatch.setattr(headset_module, "SPEAKER_AFTER_LOSS", 30.0)  # the speaker only once the pause is handled
    gate = Gate()
    soloists: list[SlowSoloist] = []
    sounds: list[Sound] = []

    def soloist_factory(account, sound):
        soloists.append(SlowSoloist(gate))
        return soloists[-1]

    def sound_factory():
        sounds.append(Sound())
        return cast(Any, sounds[-1])

    output = AudioOutput()
    engine = SpotifyEngine(cast(Any, FakeAccount(speaker=True)), output, logger, lambda: "box", soloist_factory,
                           sound_factory)  # fmt: skip
    stm = STMOperation(pubsub, logger, rgbled, library.tags, library.targets, library.activities, cast(Any, None),
                       poll_interval=60, speaker=engine)  # fmt: skip
    control = BluetoothVolumeControl(pubsub, logger)
    headset = Headset(pubsub, logger, rgbled, output, make_volumes(logger, None, None, control), control, stm)
    headset.start()
    stm.start()
    try:
        assert wait_for(lambda: engine.running, timeout=5)  # the box waits for the Spotify app
        soloists[-1].emit(type="playback_state", status="playing", item=item(TRACKS[1]))
        stm.wait_until_idle()
        assert stm.get_active_state() == States.SPOTIFY_SPEAKER
        yield SimpleNamespace(engine=engine, stm=stm, output=output, gate=gate, soloist=soloists[-1],
                              reader=sounds[-1].reader)  # fmt: skip
    finally:
        gate.open()
        stm.stop()
        headset.stop()


def test_a_button_returns_at_once_while_soloist_ctl_hangs(speaker_box, pubsub):
    box = speaker_box
    started = time.monotonic()
    pubsub.deliver(BUTTON_PRESSED, Button.NEXT)  # in the dispatch thread of the messages
    pubsub.deliver(BUTTON_PRESSED, Button.PAUSE)
    assert time.monotonic() - started < QUICK
    assert box.gate.waiting.wait(5)  # soloist ctl hangs - in the thread of the commands
    assert box.soloist.commands == []
    box.gate.open()
    assert box.engine.wait_until_done(5)
    assert box.soloist.commands == ["next", "pause"]  # in the order of the buttons


def test_a_lost_headset_mutes_at_once_and_hands_over_to_the_speaker_after_the_pause(speaker_box, pubsub):
    box = speaker_box
    pubsub.deliver(BLUETOOTH_STATE, CONNECTED)
    assert box.engine.wait_until_done(5)
    assert (box.reader.restarted, box.reader.muted) == (1, False)  # the sound goes on on the headset
    pubsub.deliver(BUTTON_PRESSED, Button.NEXT)
    assert box.gate.waiting.wait(5)  # soloist ctl next hangs
    started = time.monotonic()
    pubsub.deliver(BLUETOOTH_STATE, GONE)
    assert time.monotonic() - started < QUICK
    assert box.reader.muted  # at once: nothing reaches aplay, whatever its output
    assert box.output.headset  # the speaker becomes the output only once the pause has been handled
    box.gate.open()
    assert wait_for(lambda: not box.output.headset, timeout=5)
    assert box.soloist.commands == ["next", "pause"]
    assert box.reader.muted


def test_a_headset_returns_at_once_while_aplay_ends_slowly(speaker_box, pubsub):
    box = speaker_box
    gate = Gate()
    slow_restart(box.reader, gate)
    started = time.monotonic()
    pubsub.deliver(BLUETOOTH_STATE, CONNECTED)
    assert time.monotonic() - started < QUICK
    assert gate.waiting.wait(5)  # aplay ends in the thread of the commands
    gate.open()
    assert box.engine.wait_until_done(5)
    assert (box.reader.restarted, box.reader.muted) == (1, False)


def test_a_headset_lost_after_it_came_keeps_the_box_silent(speaker_box, pubsub):
    """The headset comes and goes again before the engine handled its coming: the box stays muted."""
    box = speaker_box
    box.gate.open()  # soloist ctl answers at once
    gate = Gate()
    slow_restart(box.reader, gate)
    pubsub.deliver(BLUETOOTH_STATE, CONNECTED)
    assert gate.waiting.wait(5)
    pubsub.deliver(BLUETOOTH_STATE, GONE)
    gate.open()
    assert wait_for(lambda: not box.output.headset, timeout=5)
    assert box.engine.wait_until_done(5)
    assert box.reader.muted


# --- no handler waits: an entry that plays ----------------------------------------------------------------------------


def test_play_spotify_never_waits_for_aplay_or_soloist(logger, rgbled, pubsub):
    engine = FakeEngine()
    player = SpotifyPlayer(logger, URI, cast(Any, engine), wait=2.0, retry_delays=(0.05,))
    entry = TargetSpotify(dict(ENTRY), logger, noop)
    play = PlaySpotify(logger, rgbled, entry, None, lambda log, uri: player, pubsub, test=True)
    play.start()
    gate = Gate()
    try:
        playing(player)
        slow_restart(engine.reader, gate)
        started = time.monotonic()
        play.cmd_output_changed()  # the headset
        pubsub.deliver(BUTTON_PRESSED, Button.NEXT)
        assert time.monotonic() - started < QUICK
        assert gate.waiting.wait(5)  # aplay ends in the thread of the player
        pubsub.deliver(BUTTON_PRESSED, Button.PAUSE)
        assert wait_for(lambda: play.get_state() == PlayStates.PAUSED, timeout=QUICK)  # the machine is not held up
        gate.open()
        assert wait_for(lambda: engine.reader.restarted == 1 and player.state == SpotifyState.PAUSED, timeout=5)
        assert player.position()[0].index == 1
    finally:
        gate.open()
        play.stop()


# --- Soloist ends: a loop, not a recursion ----------------------------------------------------------------------------


def depth():
    """How deep the stack of the calling thread is."""
    frame: FrameType | None = sys._getframe()
    count = 0
    while frame is not None:
        frame, count = frame.f_back, count + 1
    return count


class FailingEngine(FakeEngine):
    """Soloist does not start the first ``failures`` times - or, with ``exits``, it starts and ends at once."""

    def __init__(self, failures, exits=False):
        super().__init__()
        self.failures = failures
        self.exits = exits
        self.depths: list[int] = []

    def open(self, listener):
        self.depths.append(depth())
        if len(self.depths) > self.failures:
            return super().open(listener)
        if not self.exits:
            raise OSError("no network")
        listener.exited(1)
        return self.soloist


@pytest.mark.parametrize("exits", [False, True], ids=["does not start", "ends at once"])
def test_a_soloist_that_keeps_failing_is_started_again_in_a_loop(logger, exits):
    """A tag of Spotify left on a box without the internet for hours: every try fails, and the player tries again,
    with the stack as deep as the first time - a recursion ended in a RecursionError after about 500 tries."""
    engine = FailingEngine(300, exits)
    player = SpotifyPlayer(logger, URI, cast(Any, engine), wait=2.0, retry_delays=(0.0,))
    player.start(Track(), 0.0)
    try:
        assert wait_for(lambda: player.state == SpotifyState.PLAYING, timeout=30)
    finally:
        player.stop()
    assert len(engine.depths) == 301
    assert max(engine.depths) == min(engine.depths)


class EndingSoloist(FakeSoloist):
    """Soloist that ends when the box starts the entry the second time (a restart)."""

    def ctl(self, *args):
        if args == ("play", URI) and self.commands.count(f"play {URI}") == 1:
            self.commands.append(" ".join(args))
            cast(Any, self.listener).exited(1)
            return ""
        return super().ctl(*args)


def test_a_soloist_that_ends_while_the_entry_restarts_is_started_again(make_player):  # noqa: F811 - the fixture
    player = make_player(FakeEngine(EndingSoloist()))
    playing(player)
    player.restart()
    assert wait_for(lambda: player.engine.opened == 2 and player.state == SpotifyState.PLAYING, timeout=5)
    assert SpotifyState.RETRYING in player.states
    assert player.error is None  # before: ERROR "unexpected"


class EndsWhileSkipping(FakeSoloist):
    """Soloist that ends at the first ``next`` - on the way to the saved track."""

    def ctl(self, *args):
        if args == ("next",) and "next" not in self.commands:
            self.commands.append("next")
            cast(Any, self.listener).exited(1)
            return ""
        return super().ctl(*args)


def test_a_soloist_that_ends_on_the_way_to_the_saved_track_goes_there_again(make_player):  # noqa: F811 - the fixture
    player = make_player(FakeEngine(EndsWhileSkipping()), track=Track(3, TRACKS[3]), seconds=20.0)
    assert wait_for(lambda: player.engine.opened == 2 and player.state == SpotifyState.PLAYING, timeout=5)
    track, seconds = player.position()
    assert (track.index, track.uri) == (3, TRACKS[3])
    assert 19 < seconds < 25
    assert player.engine.soloist.commands.count("seek 20000") == 1


def test_a_soloist_that_ends_in_a_pause_is_started_again_only_with_play(make_player):  # noqa: F811 - the fixture
    player = make_player(track=Track(2, TRACKS[2]), seconds=30.0)
    playing(player)
    player.pause()
    assert wait_for(lambda: player.state == SpotifyState.PAUSED, timeout=3)
    player.exited(1)
    assert wait_for(lambda: player.engine.closed == 1, timeout=3)
    _, seconds = player.position()
    time.sleep(0.3)
    assert player.position()[1] == seconds  # the position stays where the sound stopped
    assert (player.state, player.engine.opened, player.engine.reader.muted) == (SpotifyState.PAUSED, 1, True)
    player.play()
    assert wait_for(lambda: player.state == SpotifyState.PLAYING and player.engine.opened == 2, timeout=5)
    assert player.position()[0].index == 2


def test_a_pause_while_soloist_is_started_again_waits_for_play(logger):
    engine = FailingEngine(10**6)
    player = SpotifyPlayer(logger, URI, cast(Any, engine), wait=2.0, retry_delays=(0.05,))
    player.start(Track(), 0.0)
    try:
        assert wait_for(lambda: player.state == SpotifyState.RETRYING, timeout=3)
        player.pause()
        assert wait_for(lambda: player.state == SpotifyState.PAUSED, timeout=3)
        tries = len(engine.depths)
        time.sleep(0.3)
        assert len(engine.depths) == tries  # no more tries while the box is paused
        engine.failures = 0
        player.play()
        assert wait_for(lambda: player.state == SpotifyState.PLAYING, timeout=5)
    finally:
        player.stop()


class CountingReader(FakeReader):
    def __init__(self):
        super().__init__()
        self.sounds = 0

    def sound(self):
        super().sound()
        self.sounds += 1


class SlowSkipSoloist(FakeSoloist):
    """Soloist whose first ``next`` hangs until the gate is opened."""

    def __init__(self, gate):
        super().__init__()
        self.gate = gate

    def ctl(self, *args):
        if args == ("next",):
            self.gate.passing()
        return super().ctl(*args)


def test_a_pause_is_silent_at_once_also_on_the_way_to_the_saved_track(make_player):  # noqa: F811 - the fixture
    """The headset goes while the player skips to the saved track: silent at once, and it stays silent when the
    player has arrived - the speaker may be the output by then."""
    gate = Gate()
    engine = FakeEngine(SlowSkipSoloist(gate))
    engine.reader = CountingReader()
    player = make_player(engine, track=Track(3, TRACKS[3]), seconds=20.0)
    assert gate.waiting.wait(5)
    player.pause()
    assert engine.reader.muted
    gate.open()
    assert wait_for(lambda: player.state == SpotifyState.PAUSED, timeout=5)
    assert engine.reader.muted
    assert engine.reader.sounds == 0
    assert player.position()[0].index == 3
    player.play()
    playing(player)
    assert (engine.reader.muted, engine.reader.sounds) == (False, 1)


# --- "Connect": no other Soloist meanwhile ----------------------------------------------------------------------------


def test_no_soloist_starts_while_connect_runs(make_engine):  # noqa: F811 - the fixture
    engine = make_engine(speaker=True)
    engine.idle()
    assert wait_for(lambda: engine.running, timeout=5)
    engine.account.before_connect()  # "Connect" begins
    assert not engine.running
    assert engine.soloists[-1].stopped == 1
    engine.idle()
    assert engine.wait_until_done(5)
    assert not engine.running
    with pytest.raises(Busy):
        engine.open(Listener())
    engine.account.after_connect()  # "Connect" has ended: the speaker waits for the Spotify app again
    assert wait_for(lambda: engine.running, timeout=5)
    assert len(engine.soloists) == 2


class AnsweringSoloist(EngineSoloist):
    """Soloist of the engine that reports its login as soon as it runs."""

    def start(self):
        super().start()
        self.emit(type="auth_state", logged_in=True, is_active=False)


def test_a_playback_waits_while_connect_runs(logger):
    """The playback waits without counting a failure: with a second retry delay of a minute it plays again at once
    when "Connect" has ended."""
    soloists: list[AnsweringSoloist] = []

    def soloist_factory(account, sound):
        soloists.append(AnsweringSoloist())
        return soloists[-1]

    account = FakeAccount()
    engine = SpotifyEngine(cast(Any, account), AudioOutput(), logger, lambda: "box", soloist_factory,
                           lambda: cast(Any, Sound()))  # fmt: skip
    player = SpotifyPlayer(logger, URI, engine, wait=2.0, retry_delays=(0.05, 60.0), busy_wait=0.05)
    player.start(Track(), 0.0)
    try:
        playing(player)
        account.before_connect()
        assert wait_for(lambda: player.state == SpotifyState.RETRYING, timeout=5)
        time.sleep(0.3)  # it tries every 0.05 s - and starts no Soloist
        assert len(soloists) == 1
        assert not engine.running
        account.after_connect()
        assert wait_for(lambda: player.state == SpotifyState.PLAYING, timeout=5)
        assert len(soloists) == 2
    finally:
        player.stop()


class Pairing:
    """``soloist --pair`` until the test lets it end (``chosen``)."""

    def __init__(self):
        self.chosen = threading.Event()
        self.returncode = None
        self.pid = 4711

    def communicate(self, timeout=None):
        if not self.chosen.wait(timeout):
            raise subprocess.TimeoutExpired("soloist", timeout)
        self.returncode = 0 if self.returncode is None else self.returncode
        return b"", b""

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15
        self.chosen.set()

    kill = terminate


def test_connect_runs_alone_and_the_speaker_comes_back_afterwards(tmp_path, logger):
    """The box is connected again (to another account) while the speaker waits for the Spotify app."""
    pairings = [Pairing(), Pairing()]
    pairing_started = threading.Event()

    def popen(command, **kwargs):
        pairing_started.set()
        return pairings.pop(0)

    account = SpotifyAccount(str(tmp_path / "spotify"), logger, lambda: "box", machine="aarch64", run=Program(),
                             opener=Spotify(), popen=popen, clock=lambda: BUILT + datetime.timedelta(days=10),
                             connect_seconds=30)  # fmt: skip
    account.set_key("abc")
    account.wait_until_fetched(5)
    account.set_speaker(True)
    pairings[0].chosen.set()
    account.connect()
    account.wait_until_connected(5)
    assert account.info()["connected"]
    connecting_at_start: list[bool] = []

    def soloist_factory(_, sound):
        connecting_at_start.append(account.info()["connecting"])
        return EngineSoloist()

    engine = SpotifyEngine(account, AudioOutput(), logger, lambda: "box", soloist_factory, lambda: cast(Any, Sound()))
    try:
        engine.idle()
        assert wait_for(lambda: engine.running, timeout=5)  # the speaker waits for the Spotify app
        pairing = pairings[0]
        pairing_started.clear()
        account.connect()
        assert pairing_started.wait(5)  # soloist --pair runs
        assert not engine.running
        engine.idle()
        assert engine.wait_until_done(5)
        assert not engine.running
        pairing.chosen.set()  # the box was chosen in the Spotify app
        account.wait_until_connected(5)
        assert wait_for(lambda: engine.running, timeout=5)  # the speaker waits for the Spotify app again
        assert connecting_at_start == [False, False]
    finally:
        engine.stop()
        account.stop()


# --- an aplay or a Soloist that hangs ---------------------------------------------------------------------------------

HUNG_APLAY = [sys.executable, "-c", "import time; time.sleep(60)"]
"""An aplay that hangs in its driver: it never takes its input."""


def test_ending_an_aplay_that_hangs_is_not_held_up_by_its_full_pipe(tmp_path, logger):
    aplays: list[subprocess.Popen[bytes]] = []

    def popen(command, **kwargs):
        aplays.append(subprocess.Popen(HUNG_APLAY, **kwargs))
        return aplays[-1]

    reader = PipeReader(str(tmp_path / "pipe"), AudioOutput(), logger, popen=popen)
    reader.open()
    writing = threading.Event()

    def write():
        with open(reader.pipe, "wb", buffering=0) as writer:
            writing.set()
            while True:
                try:
                    writer.write(b"\x01" * 4096)
                except OSError:
                    return

    writer = threading.Thread(target=write, name="test.writer", daemon=True)
    try:
        reader.sound()
        writer.start()
        assert writing.wait(5)
        assert wait_for(lambda: aplays, timeout=5)
        time.sleep(0.5)  # the pipe to aplay is full: the reader waits in its write
        restart = threading.Thread(target=reader.restart_output, name="test.restart", daemon=True)
        started = time.monotonic()
        restart.start()
        restart.join(10)
        assert not restart.is_alive()
        assert time.monotonic() - started < 3
        assert aplays[0].poll() is not None
    finally:
        reader.close()
        for aplay in aplays:
            if aplay.poll() is None:
                aplay.kill()
                aplay.wait()
        writer.join(5)
    assert all(aplay.returncode is not None for aplay in aplays)


class Unending:
    """A process that ends neither on SIGTERM nor on SIGKILL - it hangs in a driver."""

    def __init__(self):
        self.calls: list[str] = []
        self.stdin = self
        self.stdout = self.stderr = None
        self.pid = 4711
        self.args = ["aplay"]
        self._released = threading.Event()
        self.written = 0

    def poll(self):
        return 0 if self._released.is_set() else None

    def terminate(self):
        self.calls.append("terminate")

    def kill(self):
        self.calls.append("kill")

    def wait(self, timeout=None):
        if timeout is None:  # the thread that watches it waits for its end
            self._released.wait()
            return 0
        raise subprocess.TimeoutExpired("aplay", timeout)

    def write(self, chunk):
        self.written += len(chunk)
        return len(chunk)

    def close(self):
        self.calls.append("close")

    def release(self):
        self._released.set()


def test_an_aplay_that_never_ends_is_left_behind(tmp_path, logger, monkeypatch, caplog):
    monkeypatch.setattr(sound_module, "END_TIMEOUT", 0.05)
    aplay = Unending()
    reader = PipeReader(str(tmp_path / "pipe"), AudioOutput(), logger, popen=lambda *a, **k: cast(Any, aplay))
    reader.open()
    try:
        reader.sound()
        with open(reader.pipe, "wb", buffering=0) as writer:
            writer.write(b"\x01" * 100)
            assert wait_for(lambda: aplay.written, timeout=3)
        started = time.monotonic()
        reader.restart_output()
        assert time.monotonic() - started < 1
    finally:
        reader.close()
        aplay.release()
    assert aplay.calls[:3] == ["terminate", "kill", "close"]  # its input is closed only after the signals
    assert "aplay does not end" in caplog.text


def test_a_soloist_that_never_ends_is_left_behind(logger, caplog):
    process = Unending()
    soloist = Soloist("/s/bin/soloist", "KEY", "box", "/s/data", "/s/cache", logger,
                      popen=lambda *a, **k: cast(Any, process))  # fmt: skip
    soloist.start()
    try:
        started = time.monotonic()
        soloist.stop(timeout=0.05)
        assert time.monotonic() - started < 3
    finally:
        process.release()
    assert "kill" in process.calls
    assert "Soloist does not end" in caplog.text
    with pytest.raises(SoloistError, match="stopped"):
        soloist.ctl("pause")  # no command reaches the data directory any more
