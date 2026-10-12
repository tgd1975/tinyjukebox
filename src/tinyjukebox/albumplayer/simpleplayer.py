"""Player implementation with external processes: ffmpeg decodes, aplay (alsa-utils) plays."""

import collections
import logging
import subprocess
import threading
from collections.abc import Callable
from typing import IO

from tinyjukebox.albumplayer.aalbumplayer import AAlbumPlayer, ATrack, TrackEndHook
from tinyjukebox.albumplayer.clock import SYSTEM_CLOCK, Clock
from tinyjukebox.albumplayer.player_interface import Hook
from tinyjukebox.data.target import TargetAlbum

__all__ = [
    "STREAM_TIMEOUT",
    "AudioOutput",
    "AudioPipeline",
    "SimplePlayer",
    "SimpleTrack",
    "bluealsa_device",
    "decoder_command",
    "output_command",
]


STREAM_TIMEOUT = 15.0
"""Seconds without data from a stream until ffmpeg gives up (the radio player then connects again)."""


def decoder_command(source: str, start_time: float = 0, stream: bool = False) -> list[str]:
    """ffmpeg decodes the source to raw 16 bit stereo pcm (44.1 kHz) on stdout.

    ``stream``: ``source`` is the url of an internet radio stream - no start time; ffmpeg reconnects after short
    interruptions and gives up after :data:`STREAM_TIMEOUT` seconds without data.
    """
    if stream:
        position = [
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "10",
            "-rw_timeout",
            str(round(STREAM_TIMEOUT * 1_000_000)),  # µs
        ]
    else:
        position = ["-ss", str(start_time)]
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        *position,
        "-i",
        source,
        "-vn",
        "-f",
        "s16le",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-",
    ]


def output_command(null_output: bool = False, buffer_time: float | None = None, device: str | None = None) -> list[str]:
    """aplay plays raw pcm from stdin on ``device`` (``None``: the default device - the speaker) in cd format (16 bit
    little endian, 44100 Hz, stereo).

    ffmpeg's own alsa output ('-f alsa') can hang in ALSA and ignore SIGTERM - piping into aplay avoids that.
    ``null_output`` (``--no-audio``): ffmpeg consumes the audio in real time and discards it - no sound card needed.
    ``buffer_time``: alsa buffer of aplay in ms (``None``: aplay's default, usually 500 ms) - the sound starts once the
    buffer is filled; a smaller buffer starts faster but tolerates shorter stalls of the decoder.
    """
    if null_output:
        return [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-i",
            "-",
            "-af",
            "arealtime",  # not '-re': since ffmpeg 6.1 it reads the first 0.5 s at once (readrate_initial_burst)
            "-f",
            "null",
            "-",
        ]
    command = ["aplay", "-q", "-t", "raw", "-f", "cd"]
    if device is not None:
        command += ["-D", device]
    if buffer_time is not None:
        command.append(f"--buffer-time={round(buffer_time * 1000)}")  # µs
    return command


def bluealsa_device(address: str, volume: float | None = None) -> str:
    """The alsa device of BlueALSA for the headset with the bluetooth ``address`` - always stereo (A2DP).

    ``volume`` (%): set (and unmuted) when the device is opened, i.e. before the first sound - BlueALSA remembers
    the volume a headset had the last time, and a volume set via the mixer may arrive a moment after the playback
    has started."""
    device = f"bluealsa:DEV={address},PROFILE=a2dp"
    return device if volume is None else f"{device},VOL={round(volume)}+"


class AudioOutput:
    """Where the players play: the speaker (default alsa device) or the bluetooth headset - looked up whenever a
    playback starts, so that a switch applies to the next playback.

    ``headset``: a failing output means the headset has gone (the players pause instead of skipping the track).
    """

    def __init__(self, null_output: bool = False, buffer_time: float | None = None) -> None:
        self._null_output = null_output
        self._buffer_time = buffer_time
        self._lock = threading.Lock()
        self._device: Callable[[], str] | None = None
        self._headset = False

    def use_speaker(self) -> None:
        with self._lock:
            self._device, self._headset = None, False

    def use_headset(self, device: Callable[[], str] | None) -> None:
        """``device``: returns the alsa device of the headset - asked for every playback, it may carry the current
        volume (``None``: the default device - the virtual headset)."""
        with self._lock:
            self._device, self._headset = device, True

    @property
    def headset(self) -> bool:
        return self._headset

    def current(self) -> tuple[list[str], bool]:
        """(output command, headset)"""
        with self._lock:
            device, headset = self._device, self._headset
        return output_command(self._null_output, self._buffer_time, device() if device is not None else None), headset


DECODER_ERROR_LINES = 10  # the last lines of the decoder's error output are kept for the log


def _read_tail(stream: IO[bytes], tail: collections.deque[bytes]) -> None:
    with stream:
        for line in stream:
            tail.append(line)


class AudioPipeline:
    """``ffmpeg | aplay`` - started by the constructor, stopped by :meth:`stop`; :meth:`wait` blocks until the
    playback has ended (``stream``: see :func:`decoder_command`)."""

    def __init__(
        self, source: str, start_time: float = 0, output: list[str] | None = None, stream: bool = False
    ) -> None:
        self._decoder = subprocess.Popen(
            decoder_command(source, start_time, stream), stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        assert self._decoder.stdout is not None  # noqa: S101 - stdout=PIPE
        assert self._decoder.stderr is not None  # noqa: S101 - stderr=PIPE
        try:
            self._output = subprocess.Popen(
                output or output_command(),
                stdin=self._decoder.stdout,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
        except OSError:
            # e.g. aplay not installed - do not leave the decoder running
            self._decoder.kill()
            self._decoder.communicate()
            raise
        # close our copy - the decoder gets SIGPIPE if the output process exits
        self._decoder.stdout.close()
        # ffmpeg logs every broken frame of a damaged file: its error output is read all the time - a full pipe would
        # block the decoder and thus the playback forever. The thread ends when the decoder has ended.
        self._decoder_errors: collections.deque[bytes] = collections.deque(maxlen=DECODER_ERROR_LINES)
        self._decoder_reader = threading.Thread(
            target=_read_tail,
            args=(self._decoder.stderr, self._decoder_errors),
            name="AudioPipeline.decoder_errors",
            daemon=True,
        )
        self._decoder_reader.start()
        # the stderr pipe of the output is read and closed by wait() - or by stop() if nobody waits
        self._pipes_lock = threading.Lock()
        self._pipes_owned_by_wait = False
        self._pipes_closed = False

    @property
    def output_failed(self) -> bool:
        """True if the output process has ended with an error (after :meth:`wait`)."""
        return self._output.returncode not in (0, None)

    @property
    def decoder_errors(self) -> str:
        """What the decoder said about a source that it could not decode as it is - its last lines; empty for a
        source without a fault (after :meth:`wait`).

        ffmpeg plays on through the broken parts of a damaged file and ends without an error: what it logs is the only
        sign, and it logs nothing for a sound file. If the output failed, the decoder ended because nobody took what
        it decoded - that says nothing about the source."""
        if self._output.returncode != 0 or self._decoder.returncode is None:
            return ""
        said = b"".join(self._decoder_errors).decode("utf-8", errors="replace").strip()
        if not said and self._decoder.returncode != 0:
            said = f"the decoder ended with status {self._decoder.returncode}"
        return said

    def wait(self) -> tuple[bool, str]:
        """Wait for the end of the playback - returns (success, error messages)."""
        with self._pipes_lock:
            if self._pipes_closed:
                return False, "stopped"
            self._pipes_owned_by_wait = True
        _, output_err = self._output.communicate()
        self._decoder.wait()
        self._decoder_reader.join()
        decoder_err = b"".join(self._decoder_errors)
        errors = [e.decode("utf-8", errors="replace").strip() for e in (decoder_err, output_err) if e]
        success = self._decoder.returncode == 0 and self._output.returncode == 0
        return success, "; ".join(e for e in errors if e)

    def stop(self, timeout: float = 2.0) -> None:
        """Terminate both processes; a process that ignores SIGTERM for ``timeout`` seconds is killed."""
        # stop the output first -> sound stops immediately
        for process in (self._output, self._decoder):
            if process.poll() is None:
                process.terminate()
        for process in (self._output, self._decoder):
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        with self._pipes_lock:
            if not self._pipes_owned_by_wait and not self._pipes_closed:
                self._pipes_closed = True
                if self._output.stderr is not None:
                    self._output.stderr.close()


class SimpleTrack(ATrack):
    """Plays one mp3 file with an :class:`AudioPipeline`; a monitor thread waits for the end of the playback."""

    def __init__(
        self,
        filename: str | None,
        logger: logging.Logger,
        end_of_track_hook: TrackEndHook,
        start_time: float = 0,
        clock: Clock = SYSTEM_CLOCK,
        null_output: bool = False,
        on_play: Callable[[], None] | None = None,
        buffer_time: float | None = None,
        output: AudioOutput | None = None,
    ) -> None:
        """``output``: where to play (default: the speaker - ``null_output``/``buffer_time`` apply only then)."""
        super().__init__(filename, logger, end_of_track_hook, start_time, clock)
        self._output = output or AudioOutput(null_output, buffer_time)
        self._pipeline: AudioPipeline | None = None
        self._on_play = on_play

    def _play(self, play_id: int) -> None:
        command, headset = self._output.current()
        pipeline = AudioPipeline(self.filename, self._start_time, command)
        self._pipeline = pipeline
        if self._on_play is not None:
            self._on_play()
        threading.Thread(
            target=self._monitor_playback, args=(pipeline, play_id, headset), name="SimpleTrack.monitor", daemon=True
        ).start()

    def _monitor_playback(self, pipeline: AudioPipeline, play_id: int, headset: bool) -> None:
        success, errors = pipeline.wait()
        if pipeline is not self._pipeline:
            return  # stopped via stop() or replaced by a new pipeline
        self._pipeline = None
        if not success and headset and pipeline.output_failed:
            # the headset has gone - the track has not been played to its end
            self._logger.error("headset output failed for %s: %s", self.filename, errors)
            self._output_lost(play_id)
            return
        if not success:
            self._logger.error("playback failed for %s: %s", self.filename, errors)
        damage = pipeline.decoder_errors
        if damage:
            if success:
                self._logger.warning("%s is damaged: %s", self.filename, damage)
            if self.failed_hook is not None:
                self.failed_hook(damage)
        self._end_of_track(play_id)

    def _stop(self) -> None:
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is not None:
            pipeline.stop()


class SimplePlayer(AAlbumPlayer):
    """Album player with ffmpeg and aplay (``null_output``: no sound card, see :func:`output_command`).

    ``on_play`` is called whenever ffmpeg has been started for a track.
    """

    def __init__(
        self,
        logger: logging.Logger,
        album: TargetAlbum,
        end_of_track_hook: Hook,
        end_of_album_hook: Hook,
        null_output: bool = False,
        clock: Clock = SYSTEM_CLOCK,
        on_play: Callable[[], None] | None = None,
        buffer_time: float | None = None,
        output: AudioOutput | None = None,
    ) -> None:
        """``output``: where to play (default: the speaker - ``null_output``/``buffer_time`` apply only then)."""
        self._output = output or AudioOutput(null_output, buffer_time)
        self._clock = clock
        self._on_play = on_play
        super().__init__(logger, album, end_of_track_hook, end_of_album_hook)

    def _create_track(self, filename: str, end_of_track_hook: TrackEndHook) -> SimpleTrack:
        return SimpleTrack(
            filename, self._logger, end_of_track_hook, clock=self._clock, on_play=self._on_play, output=self._output
        )
