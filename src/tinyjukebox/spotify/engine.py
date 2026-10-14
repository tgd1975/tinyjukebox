"""The one Soloist of the box, with its sound (docs/developer/architecture.md, sections 2.5 and 4.1).

It runs while an entry of Spotify plays because of a tag or a test, and - with the speaker switched on - while no tag
is on the box: then the Spotify app of the account of the box can play on it. A tag that plays no Spotify ends it, so
that the app does not offer a box that would not listen. Only one Soloist runs at a time: they share the data
directory with the login.
"""

import logging
import os
import shutil
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from tinyjukebox.albumplayer.simpleplayer import AudioOutput
from tinyjukebox.core.logging import get_child
from tinyjukebox.messages import Button
from tinyjukebox.spotify.account import EXPIRED, SpotifyAccount, Unavailable
from tinyjukebox.spotify.soloist import Soloist
from tinyjukebox.spotify.sound import PipeReader, PipeWire, SoundServer

__all__ = ["SPEAKER_PAUSE_LIMIT", "Listener", "SpotifyEngine"]

SPEAKER_PAUSE_LIMIT = 600.0
"""Seconds that what the phone played may be paused before the box is free again (LED green)."""
CACHE_LIMIT = 300 * 2**20
"""Bytes of the cache of Soloist after which the speaker ends Soloist while nothing plays, to empty it."""


class Listener(Protocol):
    """The playback of an entry that uses Soloist (:class:`~tinyjukebox.albumplayer.spotifyplayer.SpotifyPlayer`)."""

    def event(self, event: dict[str, Any]) -> None: ...

    def exited(self, code: int) -> None: ...

    def output_lost(self) -> None: ...


SoloistFactory = Callable[[SpotifyAccount, SoundServer], Soloist]
SoundFactory = Callable[[], SoundServer]


class SpotifyEngine:
    """Starts and ends Soloist and its sound for whoever needs it: the playback of an entry (:meth:`open`,
    :meth:`close`) or the speaker (:meth:`idle`, :meth:`taken`)."""

    def __init__(
        self,
        account: SpotifyAccount,
        output: AudioOutput,
        logger: logging.Logger,
        device_name: Callable[[], str],
        soloist_factory: SoloistFactory | None = None,
        sound_factory: SoundFactory | None = None,
        clock: Callable[[], float] = time.monotonic,
        speaker_pause_limit: float = SPEAKER_PAUSE_LIMIT,
    ) -> None:
        self.account = account
        self._logger = get_child(logger, type(self).__name__)
        self._device_name = device_name
        self._soloist_factory = soloist_factory or self._create_soloist
        self._sound_factory = sound_factory or (lambda: PipeWire(os.path.join(account.folder, "sound"), output, logger))
        self._clock = clock
        self._speaker_pause_limit = speaker_pause_limit
        self._lock = threading.RLock()
        self._soloist: Soloist | None = None
        self._sound: SoundServer | None = None
        self._listener: Listener | None = None
        self._box_idle = False
        self._speaker_playing = False
        self._speaker_paused_at: float | None = None
        self._timer: threading.Timer | None = None
        self.on_speaker_plays: Callable[[], None] = lambda: None
        """Something plays from the Spotify app while no tag is on the box (STMOperation: SPOTIFY_SPEAKER)."""
        self.on_speaker_ends: Callable[[], None] = lambda: None
        """What the Spotify app played has ended, was paused for long or was taken to another device."""
        self.on_speaker_paused: Callable[[bool], None] = lambda paused: None
        """What the Spotify app plays on the box was paused (True) or continues (False) - the LED."""
        self.speaker_info: dict[str, Any] | None = None
        """What the phone plays on the box: title and state - for the tab "Now"."""
        account.on_speaker = self._speaker_switched
        account.before_connect = self.stop

    # --- Soloist -----------------------------------------------------------------------------------------------------

    def _create_soloist(self, account: SpotifyAccount, sound: SoundServer) -> Soloist:
        return Soloist(account.usable(), str(account.key), self._device_name(), account.data_dir, account.cache_dir,
                       self._logger, sound.env, sound.device)  # fmt: skip

    @property
    def running(self) -> bool:
        return self._soloist is not None

    @property
    def reader(self) -> PipeReader | None:
        sound = self._sound
        return sound.reader if sound is not None else None

    def _start(self) -> Soloist:
        """Soloist and its sound - started if they do not run. :raises Unavailable, OSError:"""
        with self._lock:
            if self._soloist is not None and self._soloist.running:
                return self._soloist
            self._stop()
            self.account.usable()
            if not self.account.connected:
                raise Unavailable("not_connected", "the box is not connected to an account of Spotify.")
            sound = self._sound_factory()
            sound.start()
            try:
                soloist = self._soloist_factory(self.account, sound)
                soloist.on_event = self._event
                soloist.on_exit = self._exited
                sound.reader.on_output_lost = self._output_lost
                soloist.start()
            except Exception:
                sound.stop()
                raise
            self._sound, self._soloist = sound, soloist
            return soloist

    def _stop(self) -> None:
        with self._lock:
            soloist, sound = self._soloist, self._sound
            self._soloist = self._sound = None
            self._speaker_playing = False
            self.speaker_info = None
            self._cancel_timer()
        if soloist is not None:
            soloist.stop()
        if sound is not None:
            sound.stop()
        if soloist is not None:
            # Soloist does not keep to the size of its cache (open report no. 8); it shares the card with the albums
            shutil.rmtree(self.account.cache_dir, ignore_errors=True)

    def stop(self) -> None:
        """End Soloist, whoever uses it (before "Connect", at the end of the box)."""
        listener = self._listener
        self._listener = None
        if listener is not None:
            listener.exited(-1)
        self._stop()

    def _exited(self, code: int) -> None:
        """Soloist ended by itself."""
        with self._lock:
            self._soloist = None
            sound, self._sound = self._sound, None
        if sound is not None:
            sound.stop()
        if code == EXPIRED:
            self.account.expired()
        listener = self._listener
        if listener is not None:
            listener.exited(code)
        elif self._box_idle:
            self._speaker_ended()

    def _output_lost(self) -> None:
        """``aplay`` failed on the headset: whatever plays pauses."""
        listener = self._listener
        if listener is not None:
            listener.output_lost()
        else:
            self.button(Button.PAUSE)

    # --- the playback of an entry ------------------------------------------------------------------------------------

    def open(self, listener: Listener) -> Soloist:
        """Soloist for the playback of an entry - started if it does not run (blocks).
        :raises Unavailable, OSError:"""
        with self._lock:
            self._listener = listener
            self._end_speaker_playback()
            try:
                return self._start()
            except Exception:
                self._listener = None
                raise

    def close(self, listener: Listener) -> None:
        """The playback of an entry has ended. Soloist keeps running for the speaker, if it is wanted."""
        with self._lock:
            if self._listener is not listener:
                return
            self._listener = None
            keep = self._box_idle and self.account.speaker
        if not keep:
            self._stop()

    # --- the speaker -------------------------------------------------------------------------------------------------

    def idle(self) -> None:
        """No tag is on the box (WAIT_FOR_TAG, IDLE_LONG): with the speaker switched on, Soloist runs."""
        with self._lock:
            self._box_idle = True
            wanted = self.account.speaker and self._listener is None and self._soloist is None
        if wanted:
            threading.Thread(target=self._start_speaker, name="Spotify.speaker", daemon=True).start()

    def taken(self, spotify: bool) -> None:
        """A tag was placed or a test started; ``spotify``: it plays an entry of Spotify, which uses the running
        Soloist. Anything else ends Soloist."""
        with self._lock:
            self._box_idle = False
            self._end_speaker_playback()
            stop = not spotify and self._listener is None
        if stop:
            self._stop()

    def _start_speaker(self) -> None:
        try:
            self._start()
        except (Unavailable, OSError) as e:
            self._logger.info("Spotify speaker not available: %s", e)
            return
        with self._lock:
            if not self._box_idle or self._listener is not None:
                return
        self._logger.info("Spotify speaker: the box waits for the Spotify app")
        if self._cache_too_large():
            self._logger.info("Spotify speaker: the cache of Soloist is emptied")
            self._stop()
            self._start_speaker_later()

    def _start_speaker_later(self) -> None:
        threading.Thread(target=self._start_speaker, name="Spotify.speaker", daemon=True).start()

    def _cache_too_large(self) -> bool:
        total = 0
        for root, _, names in os.walk(self.account.cache_dir):
            for name in names:
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    continue
        return total > CACHE_LIMIT

    def _speaker_switched(self, enabled: bool) -> None:
        if enabled and self._box_idle:
            self.idle()
        elif not enabled and self._listener is None:
            self._end_speaker_playback()
            self._stop()
            if self._box_idle:
                self.on_speaker_ends()

    def playback(self) -> "SpeakerPlayback":
        """What the phone plays on the box, as the playback that STMOperation shows and steers."""
        return SpeakerPlayback(self)

    def listen(self) -> None:
        """SPOTIFY_SPEAKER: what the phone plays is heard."""
        reader = self.reader
        if reader is not None:
            reader.sound()

    def silence(self) -> None:
        """SPOTIFY_SPEAKER is left: what the phone played is paused and not heard any more."""
        reader = self.reader
        if reader is not None:
            reader.silence()
        self._end_speaker_playback()

    def button(self, button: Button) -> None:
        """A button of the box while the Spotify app plays on it: play/pause, "next" and "back" steer what the phone
        started; the volume is the box's anyway."""
        command = {Button.PLAY: "play", Button.PAUSE: "pause", Button.NEXT: "next", Button.PREV: "prev"}.get(button)
        soloist = self._soloist
        if command is None or soloist is None or self._listener is not None:
            return
        try:
            soloist.ctl(command)
        except Exception as e:  # Soloist has ended meanwhile
            self._logger.info("Spotify speaker: %s: %s", command, e)

    def _end_speaker_playback(self) -> None:
        with self._lock:
            playing, self._speaker_playing = self._speaker_playing, False
            self.speaker_info = None
            self._cancel_timer()
            soloist = self._soloist if self._listener is None else None
        if playing and soloist is not None and soloist.running:
            try:
                soloist.ctl("pause")
            except Exception as e:  # the phone has taken it away already, or Soloist has ended
                self._logger.info("Spotify speaker: pause: %s", e)

    def _cancel_timer(self) -> None:
        timer, self._timer = self._timer, None
        if timer is not None:
            timer.cancel()

    def _event(self, event: dict[str, Any]) -> None:
        listener = self._listener
        if listener is not None:
            listener.event(event)
            return
        if not self._box_idle:
            return
        kind = event.get("type")
        if kind in ("playback_state", "playback_changed"):
            status = event.get("status")
            if kind == "playback_state":
                self._speaker_track(event.get("item"))
            if status == "playing":
                self._speaker_started()
            elif status == "paused":
                self._speaker_paused()
            elif status == "idle":
                self._speaker_ended()
        elif kind == "track_changed":
            self._speaker_track(event.get("item"))
        elif kind in ("device_changed", "auth_state") and event.get("is_active") is False:
            self._speaker_ended()

    def _speaker_track(self, item: object) -> None:
        if isinstance(item, dict):
            identity = (item.get("decorations") or {}).get("identity") or {}
            self.speaker_info = {**(self.speaker_info or {}), "title": identity.get("name"), "uri": item.get("uri")}

    def _speaker_started(self) -> None:
        with self._lock:
            self._cancel_timer()
            self._speaker_paused_at = None
            started = not self._speaker_playing
            self._speaker_playing = True
            self.speaker_info = {**(self.speaker_info or {}), "state": "playing"}
        if started:
            self._logger.info("Spotify speaker: the Spotify app plays on the box")
            self.on_speaker_plays()
        else:
            self.on_speaker_paused(False)

    def _speaker_paused(self) -> None:
        with self._lock:
            if not self._speaker_playing:
                return
            self.speaker_info = {**(self.speaker_info or {}), "state": "paused"}
            self._cancel_timer()
            self._timer = threading.Timer(self._speaker_pause_limit, self._speaker_ended)
            self._timer.daemon = True
            self._timer.start()
        self.on_speaker_paused(True)

    def _speaker_ended(self) -> None:
        with self._lock:
            playing, self._speaker_playing = self._speaker_playing, False
            self.speaker_info = None
            self._cancel_timer()
        if playing:
            self._logger.info("Spotify speaker: the Spotify app does not play on the box any more")
            self.on_speaker_ends()


class SpeakerPlayback:
    """What the Spotify app plays on the box while no tag is on it - steered like an album by the buttons, the headset
    and the page (:class:`~tinyjukebox.stm.stmoperation.AlbumControl`)."""

    def __init__(self, engine: SpotifyEngine) -> None:
        self._engine = engine

    def start(self) -> None:
        self._engine.listen()

    def stop(self) -> None:
        self._engine.silence()

    def cmd_next_handler(self) -> None:
        self._engine.button(Button.NEXT)

    def cmd_prev_handler(self) -> None:
        self._engine.button(Button.PREV)

    def cmd_restart_album(self) -> None:
        """What the phone plays has no beginning the box knows."""

    def cmd_pause(self) -> None:
        self._engine.button(Button.PAUSE)

    def cmd_play(self) -> None:
        self._engine.button(Button.PLAY)

    def cmd_output_lost(self) -> None:
        self._engine.button(Button.PAUSE)
        reader = self._engine.reader
        if reader is not None:
            reader.muted = True

    def cmd_output_changed(self) -> None:
        reader = self._engine.reader
        if reader is not None:
            reader.restart_output()
            reader.sound()

    def get_info(self) -> dict[str, Any]:
        info = self._engine.speaker_info or {}
        return {"target_type": "speaker", "name": info.get("title"), "uri": info.get("uri"),
                "state": info.get("state", "playing")}  # fmt: skip
