"""Volume: limits and steps (VolumeManager) of the speaker and the headset (Volumes) and the mixer that applies it
(ALSA, virtual or the bluetooth driver)."""

import collections
import importlib
import logging
import select
import threading
import time
from collections.abc import Callable, Mapping
from enum import StrEnum
from typing import Any, Protocol

from tinyjukebox.core.logging import get_child
from tinyjukebox.core.threads import LoggerThread
from tinyjukebox.data.volumelimits import VolumeLimits

__all__ = [
    "HOLD_STEP",
    "AlsaVolumeControl",
    "VirtualVolumeControl",
    "VolumeControl",
    "VolumeManager",
    "VolumeProfile",
    "Volumes",
    "check_limits",
]

WATCH_POLL_MS = 500  # the watch thread checks for stop at least this often
WATCH_RETRY = 5.0  # seconds until the watch thread opens the mixer again after an error
OWN_CHANGE_WINDOW = 1.0  # seconds in which a reported volume that VolumeManager has set itself is ignored


class VolumeControl(Protocol):
    def set_volume(self, volume: float) -> object:
        """Set the volume in % (0-100)."""


class AlsaVolumeControl:
    """Sets the volume of an ALSA mixer control (e.g. 'Master', 'PCM', 'Headphone') of the default card - or of the
    mixer ``device`` (e.g. the one of BlueALSA for a headset) - with pyalsaaudio, in the same scale as
    ``amixer sset <control> N%`` (linear over the raw range of the control).

    pyalsaaudio is imported on first use: it is only installed on the Pi. Its thread safety is not documented - the
    lock serializes the calls of state machines, rest api and command line. If the mixer fails (e.g. sound card
    re-plugged), it is created once more before the error is logged.

    :meth:`watch` reports the volume of the control whenever it changes - also changes made outside tinyjukebox, e.g.
    with alsamixer or (later) by a bluetooth headset.
    """

    def __init__(
        self, control: str, logger: logging.Logger, device: str | None = None, watch_retry: float | None = None
    ) -> None:
        """``watch_retry``: seconds until :meth:`watch` opens a failing mixer again (default: :data:`WATCH_RETRY`)."""
        self._logger = get_child(logger, type(self).__name__)
        self._control = control
        self._device = device
        self._watch_retry = watch_retry
        self._lock = threading.Lock()
        self._mixer: Any = None
        self._watch_stop = threading.Event()
        self._watch_thread: LoggerThread | None = None

    def set_volume(self, volume: float) -> bool:
        """Set the volume in % - False if that failed (logged)."""
        with self._lock:
            try:
                alsaaudio = importlib.import_module("alsaaudio")
            except ImportError:
                self._logger.error("set_volume - pyalsaaudio not installed (only on the pi, needs libasound2-dev).")
                return False
            for attempt in (1, 2):
                try:
                    if self._mixer is None:
                        self._mixer = self._open(alsaaudio)
                    self._mixer.setvolume(round(volume))
                    return True
                except alsaaudio.ALSAAudioError as e:
                    self._mixer = None
                    if attempt == 2:
                        self._logger.error("set_volume - '%s' %s%% failed: %s", self._control, round(volume), e)
            return False

    def _open(self, alsaaudio: Any) -> Any:
        """A new mixer of the control. :raises alsaaudio.ALSAAudioError: if it does not exist."""
        if self._device is None:
            return alsaaudio.Mixer(self._control)
        return alsaaudio.Mixer(self._control, device=self._device)

    def watch(self, callback: Callable[[float], None]) -> None:
        """Call ``callback`` with the volume (mean of the channels, %) whenever the control changes - in the watch
        thread, also for the changes made with :meth:`set_volume`."""
        if self._watch_thread is not None:
            return
        self._watch_stop.clear()
        self._watch_thread = LoggerThread(
            target=self._watch, args=(callback,), name="AlsaVolumeControl.watch", logger=self._logger, daemon=True
        )
        self._watch_thread.start()

    def stop_watching(self) -> None:
        self._watch_stop.set()
        if self._watch_thread is not None:
            self._watch_thread.join()
            self._watch_thread = None

    def _watch(self, callback: Callable[[float], None]) -> None:
        try:
            alsaaudio = importlib.import_module("alsaaudio")
        except ImportError:
            self._logger.error("watch - pyalsaaudio not installed (only on the pi, needs libasound2-dev).")
            return
        retry = WATCH_RETRY if self._watch_retry is None else self._watch_retry
        failure: str | None = None
        while not self._watch_stop.is_set():
            try:
                # a mixer of its own: alsa delivers the events to the handle that is polled
                mixer = self._open(alsaaudio)
                try:
                    poller = select.poll()
                    for fd, eventmask in mixer.polldescriptors():
                        # the mixer of BlueALSA reports -1 for a descriptor that is unused at the moment - poll(2)
                        # ignores those, register() raises
                        if fd >= 0:
                            poller.register(fd, eventmask)
                    failure = None
                    while not self._watch_stop.is_set():
                        if poller.poll(WATCH_POLL_MS) and mixer.handleevents():
                            callback(self._read_volume(alsaaudio))
                finally:
                    mixer.close()
            except alsaaudio.ALSAAudioError as e:
                # the same reason is reported once - a mixer may be missing for a while (headset not ready yet)
                level = logging.DEBUG if str(e) == failure else logging.WARNING
                failure = str(e)
                self._logger.log(level, "watch - mixer '%s' failed: %s - retrying in %s s", self._control, e, retry)
                self._watch_stop.wait(retry)

    def _read_volume(self, alsaaudio: Any) -> float:
        """The volume (mean of the channels, %), read with a mixer of its own - never with the watched one.

        pyalsaaudio looks the control up for every call without checking the result. Once the watched mixer has
        handled the removal of the control (headset disconnected - and perhaps connected again at once, e.g. when
        bluetoothd restarts), its getvolume() aborts the whole process in alsa-lib (assertion 'elem' in
        snd_mixer_selem_has_playback_channel). A new mixer finds the control when it is created - or raises - and
        handles no events until it is closed.

        :raises alsaaudio.ALSAAudioError: if the control is gone.
        """
        mixer = self._open(alsaaudio)
        try:
            volumes: list[int] = mixer.getvolume()
        finally:
            mixer.close()
        return sum(volumes) / len(volumes)


class VirtualVolumeControl:
    """Only remembers the volume - for running without hardware (--no-hardware): the mixer of the pc stays as it is."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self.volume: float | None = None

    def set_volume(self, volume: float) -> None:
        self.volume = volume
        self._logger.info("set_volume - virtual volume %s%%", round(volume))


HOLD_STEP = 1.0
"""Change of the volume in % per hold event of a volume button unless the config (``volume-step-hold``) says
otherwise - as ``volumecontrol.py`` of the old box."""


class VolumeManager:
    """Current volume between 0 and ``max-volume``; changed by buttons, web page and command line concurrently.
    A button press changes it by ``volume-step``, every hold event of a button by ``volume-step-hold``.

    The lock is held while the mixer is set - never from a pubsub handler directly.

    :meth:`mixer_changed` takes over volumes that were set outside tinyjukebox; ``max-volume`` stays a limit for them.
    """

    def __init__(
        self,
        volume_control: VolumeControl | None,
        config: Mapping[str, Any],
        logger: logging.Logger,
        logger_name: str | None = None,
    ) -> None:
        self._logger = get_child(logger, logger_name or type(self).__name__)
        self._volume_control = volume_control
        self._max_volume: float = config["max-volume"]
        self._default_volume: float = config["default-volume"]
        self._volume_step: float = config["volume-step"]
        self._hold_step: float = config.get("volume-step-hold", HOLD_STEP)
        self._lock = threading.RLock()  # the mixer may report a change while it is set
        self._current_volume: float = min(self._default_volume, self._max_volume)
        self._own_changes: collections.deque[tuple[float, int]] = collections.deque(maxlen=50)  # (time, volume)
        self._logger.info(
            "__init__ - default %s, max %s, step %s", self._default_volume, self._max_volume, self._volume_step
        )
        self.update_volume()

    @property
    def default_volume(self) -> float:
        return self._default_volume

    @property
    def max_volume(self) -> float:
        return self._max_volume

    @property
    def volume_step(self) -> float:
        return self._volume_step

    def set_limits(self, default_volume: float, max_volume: float) -> None:
        """New start value and maximum (see :func:`check_limits`) - a current volume above the new maximum is lowered
        at once, the start value applies from the next start (:meth:`reset`)."""
        check_limits(default_volume, max_volume)
        with self._lock:
            self._logger.info("set_limits - default %s, max %s", default_volume, max_volume)
            self._default_volume, self._max_volume = default_volume, max_volume
            if self._current_volume > max_volume:
                self._apply(max_volume)

    def reset(self) -> None:
        """Back to the start value (e.g. whenever the headset connects)."""
        with self._lock:
            self._apply(self._default_volume)

    def update_volume(self) -> None:
        """Apply the current volume (limited to 0..max-volume) to the mixer."""
        with self._lock:
            self._apply(self._current_volume)

    def _apply(self, volume: float) -> None:
        self._current_volume = min(max(volume, 0), self._max_volume)
        self._own_changes.append((time.monotonic(), round(self._current_volume)))
        self._logger.info("volume %s", self._current_volume)
        if self._volume_control is not None:
            self._volume_control.set_volume(self._current_volume)

    def get_volume(self) -> float:
        return self._current_volume

    def mixer_changed(self, volume: float) -> None:
        """The mixer reports ``volume`` (%): volumes set outside tinyjukebox become the current volume; above
        ``max-volume`` the mixer is set back to it. The reports of the own changes are ignored - also late ones, which
        would otherwise undo a quick second button press for a moment."""
        with self._lock:
            now = time.monotonic()
            if any(now - at < OWN_CHANGE_WINDOW and own == round(volume) for at, own in self._own_changes):
                return
            if volume > self._max_volume:
                self._logger.warning("volume %s%% set outside tinyjukebox - limited to max-volume", round(volume))
                self._apply(self._max_volume)
            elif round(volume) != round(self._current_volume):
                self._logger.info("volume %s%% set outside tinyjukebox", round(volume))
                self._current_volume = volume

    def set_volume(self, volume: float) -> None:
        with self._lock:
            self._apply(volume)

    def volume_up(self, held: bool = False) -> None:
        """One step up - ``held``: the small step of a button that is kept pressed."""
        with self._lock:
            self._apply(self._current_volume + (self._hold_step if held else self._volume_step))

    def volume_down(self, held: bool = False) -> None:
        with self._lock:
            self._apply(self._current_volume - (self._hold_step if held else self._volume_step))

    def volume_off(self) -> None:
        with self._lock:
            self._apply(0)


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("start value and maximum must be numbers.")
    return value


def check_limits(default_volume: object, max_volume: object) -> tuple[float, float]:
    """(start value, maximum). :raises ValueError: unless they are numbers with 0 <= start value <= maximum <= 100."""
    start, maximum = _number(default_volume), _number(max_volume)
    if not 0 <= start <= maximum <= 100:
        raise ValueError("0 <= start value <= maximum <= 100 is required.")
    return start, maximum


class VolumeProfile(StrEnum):
    """Which volume the buttons change - value: name in the rest api, the command line and ``volume.json``."""

    SPEAKER = "speaker"
    HEADPHONES = "headphones"


class Volumes:
    """The volumes of speaker and headset - buttons, web page and command line change the active one (the headset's
    while it is connected). Start value and maximum of both can be changed at runtime; they are stored in
    :class:`~tinyjukebox.data.volumelimits.VolumeLimits` and override the config from then on."""

    def __init__(
        self, speaker: VolumeManager, headphones: VolumeManager, limits: VolumeLimits | None, logger: logging.Logger
    ) -> None:
        self._logger = get_child(logger, type(self).__name__)
        self._managers = {VolumeProfile.SPEAKER: speaker, VolumeProfile.HEADPHONES: headphones}
        self._limits = limits
        self._active = VolumeProfile.SPEAKER
        self._lock = threading.Lock()  # changes of the limits are stored one after the other

    @property
    def active(self) -> VolumeProfile:
        return self._active

    def manager(self, profile: VolumeProfile | None = None) -> VolumeManager:
        """The volume of ``profile`` (default: the active one)."""
        return self._managers[profile or self._active]

    def use(self, profile: VolumeProfile) -> None:
        if profile != self._active:
            self._logger.info("buttons change the volume of the %s now", profile.value)
        self._active = profile

    def set_limits(self, profile: VolumeProfile, default_volume: float, max_volume: float) -> None:
        """:raises ValueError: for invalid values (see :func:`check_limits`)."""
        with self._lock:
            self._managers[profile].set_limits(default_volume, max_volume)
            if self._limits is not None:
                self._limits.store(profile.value, default_volume, max_volume)

    # --- the active volume (buttons, web page, command line) ------------------------------------------------------

    def get_volume(self) -> float:
        return self.manager().get_volume()

    def set_volume(self, volume: float) -> None:
        self.manager().set_volume(volume)

    def volume_up(self, held: bool = False) -> None:
        self.manager().volume_up(held)

    def volume_down(self, held: bool = False) -> None:
        self.manager().volume_down(held)

    def update_volume(self) -> None:
        self.manager().update_volume()

    def volume_off(self) -> None:
        """Mute the speaker (shutdown) - the headset is disconnected anyway."""
        self._managers[VolumeProfile.SPEAKER].volume_off()
