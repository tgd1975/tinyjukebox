"""SimpleTrack/SimplePlayer/AudioPipeline with real ffmpeg processes (audio output: real time null sink)."""

import logging
import sys
import threading
import time
from typing import cast

import pytest

from tests.support import ALBUMS_FOLDER, ONE_SECOND_MP3, SHORT_MP3, CallRecorder, wait_for
from tinyjukebox.albumplayer import simpleplayer
from tinyjukebox.albumplayer.simpleplayer import AudioPipeline, SimplePlayer, SimpleTrack, output_command
from tinyjukebox.data.target import TargetAlbum
from tinyjukebox.metadata import mp3_info

SHORT_LENGTH = mp3_info(SHORT_MP3)[0]


def test_pipeline_plays_file_in_real_time():
    start = time.monotonic()
    success, errors = AudioPipeline(ONE_SECOND_MP3).wait()  # a short file is too short for a robust bound
    assert success, errors
    assert 0.9 < time.monotonic() - start < 10  # real time, not as fast as possible (upper bound: slow boxes)


def test_pipeline_reports_decoder_errors(tmp_path):
    broken = tmp_path / "broken.mp3"
    broken.write_bytes(b"no mp3")
    success, errors = AudioPipeline(str(broken)).wait()
    assert not success
    assert errors


def test_pipeline_survives_lots_of_decoder_errors(monkeypatch):
    """ffmpeg logs every broken frame of a damaged file - more than a pipe holds must not block the playback."""
    script = "import sys\nfor i in range(5000): print(f'broken frame {i:04}', 'x' * 40, file=sys.stderr)\nprint('pcm')"
    monkeypatch.setattr(
        simpleplayer, "decoder_command", lambda source, start_time, stream=False: [sys.executable, "-c", script]
    )
    pipeline = AudioPipeline(SHORT_MP3, output=["sh", "-c", "cat > /dev/null"])
    result = []
    waiter = threading.Thread(target=lambda: result.append(pipeline.wait()), daemon=True)
    waiter.start()
    waiter.join(20)
    pipeline.stop()
    assert result, "playback blocked by the full error pipe of the decoder"
    success, errors = result[0]
    assert success
    assert "broken frame 4999" in errors
    assert "broken frame 0000" not in errors  # only the last lines are kept


def failing_decoder(monkeypatch):
    """A decoder that gives up on its source."""
    script = "import sys\nprint('Invalid data found when processing input', file=sys.stderr)\nsys.exit(1)"
    monkeypatch.setattr(
        simpleplayer, "decoder_command", lambda source, start_time, stream=False: [sys.executable, "-c", script]
    )


def damaged(tmp_path, start: int, end: int) -> str:
    """A copy of a track of five seconds (20 kB) whose bytes from ``start`` to ``end`` are no mp3 data any more."""
    data = (ALBUMS_FOLDER / "simple_album_b" / "c.mp3").read_bytes()
    copy = tmp_path / f"damaged-{start}-{end}.mp3"
    copy.write_bytes(data[:start] + (bytes(range(256)) * 100)[: end - start] + data[end:])
    return str(copy)


def test_pipeline_tells_a_damaged_file_from_a_failing_output(tmp_path, monkeypatch):
    sink = ["sh", "-c", "cat > /dev/null"]
    played = AudioPipeline(SHORT_MP3, output=sink)
    assert played.wait()[0]
    assert played.decoder_errors == ""
    no_output = AudioPipeline(SHORT_MP3, output=["sh", "-c", "exit 1"])
    assert not no_output.wait()[0]
    assert no_output.decoder_errors == ""  # the decoder ended because nobody took what it decoded
    # ffmpeg plays a damaged file to its end and ends without an error: what it logs is the sign
    for source in (damaged(tmp_path, 6000, 12000), damaged(tmp_path, 2048, 20640)):
        broken = AudioPipeline(source, output=sink)
        assert broken.wait()[0]
        assert broken.decoder_errors
    failing_decoder(monkeypatch)
    gave_up = AudioPipeline(SHORT_MP3, output=sink)
    assert not gave_up.wait()[0]
    assert gave_up.decoder_errors == "Invalid data found when processing input"


def test_pipeline_stop_is_immediate():
    pipeline = AudioPipeline(ONE_SECOND_MP3)
    start = time.monotonic()
    pipeline.stop()
    assert time.monotonic() - start < 1
    assert pipeline._decoder.poll() is not None
    assert pipeline._output.poll() is not None


def test_pipeline_stop_kills_processes_ignoring_sigterm():
    # an output process that ignores SIGTERM (as ffmpeg's alsa output did) must be killed
    pipeline = AudioPipeline(SHORT_MP3, output=["sh", "-c", "trap '' TERM; cat > /dev/null; sleep 30"])
    time.sleep(0.2)  # the shell must have installed the trap
    start = time.monotonic()
    pipeline.stop(timeout=0.3)
    assert time.monotonic() - start < 2
    assert pipeline._output.poll() is not None


def test_track_length_is_read_from_mp3(logger):
    track = SimpleTrack(ONE_SECOND_MP3, logger, CallRecorder())
    assert track.track_length() == pytest.approx(0.98, abs=0.05)
    assert track.time_played() == 0
    assert not track.is_playing()


def test_track_unknown_file(logger):
    with pytest.raises(ValueError, match="unknown filename"):
        SimpleTrack("does_not_exist.mp3", logger, CallRecorder())


def test_track_plays_to_the_end(logger):
    hook = CallRecorder()
    track = SimpleTrack(SHORT_MP3, logger, hook)
    track.play()
    assert track.is_playing()
    assert hook.event.wait(3)
    assert hook.count == 1
    assert not track.is_playing()
    assert track.end_of_track_reached()
    assert track.time_played() == track.track_length()


def test_track_reports_a_damaged_file(logger, tmp_path):
    hook = CallRecorder()
    failed: list[str] = []
    track = SimpleTrack(damaged(tmp_path, 6000, 12000), logger, hook)
    track.failed_hook = failed.append
    track.play()
    assert hook.event.wait(10)  # the track ends as every track: the player goes on with the next one
    assert len(failed) == 1
    assert failed[0]


def test_track_reports_a_file_that_cannot_be_decoded(logger, monkeypatch):
    failing_decoder(monkeypatch)
    hook = CallRecorder()
    failed: list[str] = []
    track = SimpleTrack(SHORT_MP3, logger, hook)
    track.failed_hook = failed.append
    track.play()
    assert hook.event.wait(3)  # the track ends as every track: the player goes on with the next one
    assert failed == ["Invalid data found when processing input"]


def test_player_passes_on_the_track_that_failed(player, monkeypatch):
    failing_decoder(monkeypatch)
    failed = []
    player.set_track_failed_hook(lambda track, error: failed.append((track, error)))
    player.set_track(1)
    player.play()
    assert player.hooks["track"].event.wait(3)
    assert failed == [(1, "Invalid data found when processing input")]


def test_track_stop_keeps_position_and_resume(logger):
    hook = CallRecorder()
    track = SimpleTrack(ONE_SECOND_MP3, logger, hook)
    track.play()
    time.sleep(0.4)
    track.stop()
    position = track.time_played()
    assert 0.3 < position < 0.8
    assert not track.is_playing()
    time.sleep(0.1)
    assert track.time_played() == position  # paused
    assert hook.count == 0  # stopping is no end of track

    track.play()
    assert track.time_played() >= position
    assert track._pipeline is not None
    decoder = cast(list[str], track._pipeline._decoder.args)  # resumed, not restarted: ffmpeg seeks to the position
    assert float(decoder[decoder.index("-ss") + 1]) == pytest.approx(position)
    assert hook.event.wait(10)
    assert hook.count == 1


def test_track_start_time(logger):
    track = SimpleTrack(ONE_SECOND_MP3, logger, CallRecorder(), start_time=0.5)
    assert track.time_played() == 0.5
    track.set_start_time(100)
    assert track.time_played() == track.track_length()


def test_track_restart_reports_one_end_only(logger):
    """The monitor thread of a stopped pipeline must not report an end of track - only the latest playback does."""
    hook = CallRecorder()
    track = SimpleTrack(SHORT_MP3, logger, hook)
    track.play()
    old_play_id = track.play_id
    track.stop()
    track._end_of_track(old_play_id)  # outdated end while stopped
    assert hook.count == 0
    track.play()
    track._end_of_track(old_play_id)  # outdated end while the new playback runs
    assert hook.count == 0
    assert track.is_playing()
    assert hook.event.wait(3)
    assert hook.count == 1
    assert track.end_of_track_reached()
    time.sleep(0.1)  # a late report of the first pipeline would show up now
    assert hook.count == 1


class HookedPlayer(SimplePlayer):
    """SimplePlayer with recording end of track/album hooks (``hooks["track"]``, ``hooks["album"]``)."""

    def __init__(self, logger: logging.Logger, target: TargetAlbum) -> None:
        self.hooks = {"track": CallRecorder(), "album": CallRecorder()}
        super().__init__(logger, target, self.hooks["track"], self.hooks["album"])


@pytest.fixture
def player(library, logger):
    """SimplePlayer for album "1" (three short tracks)."""
    p = HookedPlayer(logger, library.targets.get_target("1"))
    yield p
    p.stop_player()


def test_player_init(player):
    assert player.album_track_count() == 3
    assert player.current_track_id() == 0
    assert player.album_length() == pytest.approx(3 * SHORT_LENGTH, abs=0.1)
    assert not player.is_playing()


def test_player_end_of_track_hooks(player):
    player.play()
    assert player.hooks["track"].event.wait(3)
    assert player.hooks["album"].count == 0
    assert player.current_track_end_reached()
    player.pause()
    player.set_track(2)
    player.play()
    assert player.hooks["album"].event.wait(3)
    assert player.album_end_reached()


def test_player_navigation(player):
    assert player.to_next() is True
    assert player.current_track_id() == 1
    player.to_prev()
    player.to_prev()
    assert player.current_track_id() == 0  # stays at the first track
    player.set_track(2, 0.2)
    assert player.current_track_time() == 0.2
    player.rewind_track()
    assert player.current_track_time() == 0
    assert player.to_next() is False  # last track
    assert player.hooks["album"].count == 1
    player.to_first()
    assert player.current_track_id() == 0


def test_player_pause_and_play(player):
    player.play()
    assert player.is_playing()
    time.sleep(0.15)
    player.pause()
    assert not player.is_playing()
    position = player.current_track_time()
    assert position > 0.1
    time.sleep(0.1)
    assert player.current_track_time() == position
    assert player.hooks["track"].count == 0


def test_player_set_track_while_playing(player):
    player.play()
    player.set_track(1)
    assert player.is_playing()
    assert player.current_track_id() == 1
    assert wait_for(lambda: player.current_track_time() > 0.1)


def test_player_pause_play_does_not_skip_a_track(player):
    """Pause and play again: only the end of the new playback counts - no track is skipped."""
    player.play()
    old_play_id = player.get_track(0).play_id
    player.pause()
    player.play()
    player._on_track_end(0, old_play_id)
    assert player.hooks["track"].count == 0
    assert player.current_track_id() == 0
    assert player.hooks["track"].event.wait(3)  # the real end of the track
    assert player.hooks["track"].count == 1
    assert player.current_track_id() == 0  # the state machine selects the next track, not the player


def test_output_command_buffer_time():
    """The original function - conftest replaces simpleplayer.output_command with a null sink."""
    assert output_command() == ["aplay", "-q", "-t", "raw", "-f", "cd"]
    assert output_command(buffer_time=200)[-1] == "--buffer-time=200000"  # µs
    assert "--buffer-time" not in " ".join(output_command(null_output=True, buffer_time=200))


def test_buffer_time_reaches_the_track(logger, monkeypatch):
    calls = []

    def output(null_output=False, buffer_time=None, device=None):
        calls.append((null_output, buffer_time, device))
        return ["true"]

    monkeypatch.setattr(simpleplayer, "output_command", output)
    track = simpleplayer.SimpleTrack(SHORT_MP3, logger, lambda play_id: None, buffer_time=150)
    assert track._output.current() == (["true"], False)  # looked up when a playback starts
    assert calls == [(False, 150, None)]
