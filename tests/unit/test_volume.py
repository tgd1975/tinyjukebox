"""VolumeManager, Volumes (speaker and headset), their limits, AlsaVolumeControl and STMVolume."""

import json
import os
import select
import sys
import threading
import time
import types

import pytest

from tests.support import HEADPHONES, file_id, make_volumes, wait_for
from tinyjukebox import volumemanager
from tinyjukebox.core import unexpected
from tinyjukebox.data.volumelimits import VolumeLimits
from tinyjukebox.messages import BUTTON_HELD, BUTTON_PRESSED, Button
from tinyjukebox.stm.stmvolume import STMVolume
from tinyjukebox.volumemanager import (
    AlsaVolumeControl,
    VirtualVolumeControl,
    VolumeManager,
    VolumeProfile,
    check_limits,
)

CONFIG = {"max-volume": 80, "default-volume": 50, "volume-step": 5, "steps-per-second": 2}


class FakeVolumeControl:
    def __init__(self):
        self.volumes = []

    def set_volume(self, volume):
        self.volumes.append(volume)


@pytest.fixture
def control():
    return FakeVolumeControl()


@pytest.fixture
def manager(control, logger):
    return VolumeManager(control, CONFIG, logger)


def test_default_volume_is_applied(manager, control):
    assert manager.get_volume() == 50
    assert control.volumes == [50]


def test_up_down_off(manager, control):
    manager.volume_up()
    assert manager.get_volume() == 55
    manager.volume_down()
    manager.volume_down()
    assert manager.get_volume() == 45
    manager.volume_off()
    assert control.volumes[-1] == 0


def test_volume_limits(manager, control):
    for _ in range(20):
        manager.volume_up()
    assert manager.get_volume() == 80
    manager.set_volume(-10)
    assert manager.get_volume() == 0
    manager.set_volume(100)
    assert control.volumes[-1] == 80


def test_concurrent_changes_are_not_lost(manager):
    """Buttons and web page change the volume at the same time - every step counts (no lost update)."""
    manager.set_volume(0)
    threads = [threading.Thread(target=lambda: [manager.volume_up() for _ in range(2)]) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert manager.get_volume() == 80  # 16 steps of 5


def test_virtual_volume_control(logger):
    control = VirtualVolumeControl(logger)
    control.set_volume(42)
    assert control.volume == 42


class FakeAlsaAudio(types.ModuleType):
    """``alsaaudio``: records the mixers and their volumes; ``failures`` lets the next setvolume calls fail.

    Like alsa, every change of the control (:meth:`external` or ``setvolume``) makes the poll descriptor readable.
    """

    class ALSAAudioError(Exception):
        pass

    def __init__(self):
        super().__init__("alsaaudio")
        self.created: list = []  # the mixers
        self.failures = 0
        self.volume = 50
        self.controls: dict[str, list[str]] = {}  # mixer device -> names of its controls
        self.unused_descriptors: list[tuple[int, int]] = []  # reported by polldescriptors in addition
        self.aborted = False  # getvolume() of a removed control
        self.generations: dict[str, int] = {}  # mixer device -> number of disconnects (BlueALSA removes the controls)
        self._read_fd, self._write_fd = os.pipe()
        os.set_blocking(self._read_fd, False)
        alsa = self

        class Mixer:
            def __init__(self, control, device=None):
                if device is not None and control not in alsa.mixers(device):
                    raise alsa.ALSAAudioError(f"no control {control} on {device}")
                self.control = control
                self.device = device
                self.volumes = []
                self.generation = alsa.generations.get(device, 0)
                self.present = True  # alsa-lib: the element of the control in this handle
                alsa.created.append(self)

            def setvolume(self, volume):
                if alsa.failures:
                    alsa.failures -= 1
                    raise alsa.ALSAAudioError("device gone")
                self.volumes.append(volume)
                alsa.external(volume)

            def getvolume(self):
                if not self.present:
                    alsa.aborted = True  # alsa-lib: assertion 'elem' - the real process ends here
                    raise RuntimeError("abort")
                return [alsa.volume, alsa.volume]

            def polldescriptors(self):
                return [(alsa._read_fd, select.POLLIN), *alsa.unused_descriptors]

            def handleevents(self):
                # a disconnect since the handle was opened: the handle removes the element - also if the headset is
                # connected again by now (the new element would arrive with a later event)
                if alsa.generations.get(self.device, 0) != self.generation:
                    self.present = False
                try:
                    return len(os.read(alsa._read_fd, 1024))
                except BlockingIOError:
                    return 0

            def close(self):
                pass

        self.Mixer = Mixer

    def mixers(self, device="default"):
        if device not in self.controls:
            raise self.ALSAAudioError(f"no mixer {device}")
        return list(self.controls[device])

    def disconnect(self, device):
        """The headset disconnects: BlueALSA removes its mixer."""
        self.generations[device] = self.generations.get(device, 0) + 1
        self.controls.pop(device, None)

    def external(self, volume):
        """The control changes (e.g. alsamixer)."""
        self.volume = volume
        os.write(self._write_fd, b"x")

    def close(self):
        os.close(self._read_fd)
        os.close(self._write_fd)


@pytest.fixture
def alsaaudio(monkeypatch):
    module = FakeAlsaAudio()
    monkeypatch.setitem(sys.modules, "alsaaudio", module)
    yield module
    module.close()


def test_alsa_volume_control_sets_percent(alsaaudio, logger):
    control = AlsaVolumeControl("PCM", logger)
    control.set_volume(42.4)
    control.set_volume(45)
    assert [m.control for m in alsaaudio.created] == ["PCM"]  # the mixer is created once
    assert alsaaudio.created[0].volumes == [42, 45]


def test_alsa_volume_control_recreates_the_mixer_once(alsaaudio, logger, caplog):
    control = AlsaVolumeControl("PCM", logger)
    control.set_volume(10)
    alsaaudio.failures = 1
    control.set_volume(20)
    assert len(alsaaudio.created) == 2
    assert alsaaudio.created[-1].volumes == [20]
    assert "failed" not in caplog.text
    alsaaudio.failures = 2
    control.set_volume(30)  # fails twice - logged, not raised
    assert "set_volume - 'PCM' 30% failed: device gone" in caplog.text
    control.set_volume(40)
    assert alsaaudio.created[-1].volumes == [40]


def test_alsa_volume_control_without_pyalsaaudio(monkeypatch, logger, caplog):
    monkeypatch.setitem(sys.modules, "alsaaudio", None)  # import raises ImportError
    AlsaVolumeControl("PCM", logger).set_volume(10)
    assert "pyalsaaudio not installed" in caplog.text


def test_alsa_volume_control_serializes_calls(alsaaudio, logger):
    control = AlsaVolumeControl("PCM", logger)
    threads = [threading.Thread(target=control.set_volume, args=(v,)) for v in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(alsaaudio.created) == 1
    assert sorted(alsaaudio.created[0].volumes) == list(range(20))


def test_stm_volume(manager, control, pubsub, logger):
    stm = STMVolume(pubsub, manager, logger)
    stm.start()
    assert manager.get_volume() == 50  # start keeps the default volume
    pubsub.deliver(BUTTON_PRESSED, Button.VOLUME_UP)
    pubsub.deliver(BUTTON_PRESSED, Button.VOLUME_UP)
    stm.wait_until_idle()
    assert manager.get_volume() == 60
    pubsub.deliver(BUTTON_PRESSED, Button.VOLUME_DOWN)
    stm.wait_until_idle()
    assert manager.get_volume() == 55
    pubsub.deliver(BUTTON_PRESSED, Button.NEXT)  # ignored - for STMPlayAlbum
    pubsub.deliver(BUTTON_PRESSED, "SIDEWAYS")  # ignored
    stm.wait_until_idle()
    assert manager.get_volume() == 55
    stm.stop()
    assert control.volumes[-1] == 0  # mute on shutdown
    assert pubsub.handlers(BUTTON_PRESSED) == []
    assert pubsub.handlers(BUTTON_HELD) == []


def test_stm_volume_held_button_changes_in_small_steps(manager, control, pubsub, logger):
    """As volumecontrol.py of the old box: 5 % per press, 1 % per hold event (repeated by the input driver)."""
    stm = STMVolume(pubsub, manager, logger)
    stm.start()
    pubsub.deliver(BUTTON_PRESSED, Button.VOLUME_UP)
    for _ in range(3):
        pubsub.deliver(BUTTON_HELD, Button.VOLUME_UP)
    stm.wait_until_idle()
    assert control.volumes[-4:] == [55, 56, 57, 58]
    pubsub.deliver(BUTTON_HELD, Button.VOLUME_DOWN)
    pubsub.deliver(BUTTON_HELD, Button.PREV)  # ignored - for STMPlayAlbum
    stm.wait_until_idle()
    assert manager.get_volume() == 57
    stm.stop()


@pytest.mark.parametrize(
    ("topic", "button", "expected"),
    [(BUTTON_PRESSED, Button.VOLUME_UP, [60]), (BUTTON_HELD, Button.VOLUME_DOWN, [48])],
)
def test_stm_volume_takes_the_next_press_after_a_failed_step(manager, control, pubsub, logger, topic, button, expected):
    """A step whose mixer fails is reported as unexpected - and the buttons keep working: the machine is idle again
    (before, it stayed in the state of the failed step, which takes no button, until the next start)."""
    reported = []

    def listener(error, where):
        reported.append((error, where))

    unexpected.subscribe(listener)
    stm = STMVolume(pubsub, manager, logger)
    stm.start()
    failing = RuntimeError("mixer gone")

    def fail(volume):
        raise failing

    working, control.set_volume = control.set_volume, fail
    try:
        pubsub.deliver(topic, button)
        stm.wait_until_idle()
        assert stm._machine.get_active_state().id.value == "idle"
        assert [(error, where) for error, where in reported] == [
            (failing, f"volume button ({'up' if button is Button.VOLUME_UP else 'down_held'})")
        ]
        control.set_volume = working
        count = len(control.volumes)
        pubsub.deliver(topic, button)
        stm.wait_until_idle()
        assert control.volumes[count:] == expected  # the next press changes the volume
    finally:
        stm.stop()
        unexpected.unsubscribe(listener)


def test_hold_step(control, logger):
    manager = VolumeManager(control, {**CONFIG, "volume-step-hold": 2, "max-volume": 53}, logger)
    manager.volume_up(held=True)
    manager.volume_up(held=True)  # limited to max-volume
    assert control.volumes[-2:] == [52, 53]
    manager.volume_down(held=True)
    assert manager.get_volume() == 51


def test_stm_volume_handler_does_not_block(manager, pubsub, logger):
    """The handler runs in the pubsub dispatch thread - the mixer is set in the machine's thread."""
    release = threading.Event()
    calls = []

    class SlowControl:
        def set_volume(self, volume):
            calls.append(threading.current_thread().name)
            release.wait(5)

    slow = VolumeManager(None, CONFIG, logger)
    stm = STMVolume(pubsub, slow, logger)
    stm.start()
    slow._volume_control = SlowControl()
    start = time.monotonic()
    pubsub.deliver(BUTTON_PRESSED, Button.VOLUME_UP)  # returns although the mixer is still busy
    assert time.monotonic() - start < 1
    assert wait_for(lambda: calls)
    assert calls[0] != threading.current_thread().name
    release.set()
    stm.wait_until_idle()
    assert slow.get_volume() == 55
    stm.stop()


@pytest.fixture
def watched(alsaaudio, logger, monkeypatch):
    """VolumeManager on an AlsaVolumeControl that watches the (fake) mixer."""
    monkeypatch.setattr(volumemanager, "WATCH_POLL_MS", 20)
    control = AlsaVolumeControl("PCM", logger)
    manager = VolumeManager(control, CONFIG, logger)
    control.watch(manager.mixer_changed)
    yield manager, control
    control.stop_watching()


def test_external_volume_change_is_taken_over(watched, alsaaudio, caplog):
    manager, _ = watched
    alsaaudio.external(30)
    assert wait_for(lambda: manager.get_volume() == 30)
    assert "volume 30% set outside tinyjukebox" in caplog.text
    manager.volume_up()  # the next step starts from the new volume
    assert manager.get_volume() == 35
    assert alsaaudio.created[0].volumes[-1] == 35


def test_external_volume_above_max_is_limited(watched, alsaaudio, caplog):
    manager, _ = watched
    alsaaudio.external(95)
    assert wait_for(lambda: alsaaudio.created[0].volumes[-1:] == [80])  # set back to max-volume
    assert manager.get_volume() == 80
    assert "limited to max-volume" in caplog.text


def test_own_changes_are_not_taken_over(watched, alsaaudio, monkeypatch):
    manager, _ = watched
    for _ in range(4):  # the reports of 55, 60, 65 arrive late - they must not undo the later steps
        manager.volume_up()
    time.sleep(0.1)
    assert manager.get_volume() == 70


def test_late_report_of_an_own_change(alsaaudio, logger):
    manager = VolumeManager(AlsaVolumeControl("PCM", logger), CONFIG, logger)
    manager.volume_up()
    manager.volume_up()
    manager.mixer_changed(55)  # report of the first step, after the second one
    assert manager.get_volume() == 60


def test_watch_retries_after_mixer_errors(alsaaudio, logger, monkeypatch, caplog):
    monkeypatch.setattr(volumemanager, "WATCH_POLL_MS", 20)
    monkeypatch.setattr(volumemanager, "WATCH_RETRY", 0.05)
    created = []
    original = alsaaudio.Mixer

    def failing_first(control):
        created.append(control)
        if len(created) == 1:
            raise alsaaudio.ALSAAudioError("no such control")
        return original(control)

    alsaaudio.Mixer = failing_first
    reported: list[float] = []
    control = AlsaVolumeControl("PCM", logger)
    control.watch(reported.append)
    try:
        assert wait_for(lambda: len(created) == 2)
        alsaaudio.external(33)
        assert wait_for(lambda: reported == [33])
    finally:
        control.stop_watching()
    assert "retrying" in caplog.text


def test_watch_skips_unused_descriptors(alsaaudio, logger, monkeypatch, caplog):
    monkeypatch.setattr(volumemanager, "WATCH_POLL_MS", 20)
    alsaaudio.unused_descriptors = [(-1, 0)]  # as the mixer of BlueALSA does (its d-bus watch for writing)
    reported: list[float] = []
    control = AlsaVolumeControl("PCM", logger)
    control.watch(reported.append)
    try:
        assert wait_for(lambda: alsaaudio.created)
        alsaaudio.external(30)
        assert wait_for(lambda: reported == [30])
    finally:
        control.stop_watching()
    assert "uncaught exception" not in caplog.text


def test_watch_without_pyalsaaudio(monkeypatch, logger, caplog):
    monkeypatch.setitem(sys.modules, "alsaaudio", None)
    control = AlsaVolumeControl("PCM", logger)
    control.watch(print)
    control.stop_watching()
    assert "watch - pyalsaaudio not installed" in caplog.text


# --- mixer of another device (BlueALSA) -------------------------------------------------------------------------------


def test_mixer_of_a_headset(alsaaudio, logger, monkeypatch):
    """BlueALSA: one mixer per headset, its control is 'A2DP'."""
    monkeypatch.setattr(volumemanager, "WATCH_POLL_MS", 20)
    device = "bluealsa:AC:80:0A:11:22:33"
    alsaaudio.controls[device] = ["A2DP", "SCO"]
    control = AlsaVolumeControl("A2DP", logger, device=device)
    assert control.set_volume(40) is True
    mixer = alsaaudio.created[0]
    assert (mixer.control, mixer.device, mixer.volumes) == ("A2DP", device, [40])
    reported: list[float] = []
    control.watch(reported.append)
    try:
        alsaaudio.external(46)  # volume buttons of the headset
        assert wait_for(lambda: reported[-1:] == [46])
    finally:
        control.stop_watching()


def test_watch_of_a_headset_that_disconnects(alsaaudio, logger, monkeypatch, caplog):
    """The control disappears with the headset - getvolume() would abort the process (box 2026-10-03)."""
    monkeypatch.setattr(volumemanager, "WATCH_POLL_MS", 20)
    monkeypatch.setattr(volumemanager, "WATCH_RETRY", 0.05)
    device = "bluealsa:AC:80:0A:11:22:33"
    alsaaudio.controls[device] = ["A2DP", "SCO"]
    reported: list[float] = []
    control = AlsaVolumeControl("A2DP", logger, device=device)
    control.watch(reported.append)
    try:
        assert wait_for(lambda: alsaaudio.created)
        alsaaudio.disconnect(device)
        alsaaudio.external(46)  # the event of the removal
        assert wait_for(lambda: "watch - mixer 'A2DP' failed" in caplog.text)
        alsaaudio.controls[device] = ["A2DP", "SCO"]  # connected again
        assert wait_for(lambda: len(alsaaudio.created) > 1)
        alsaaudio.external(30)
        assert wait_for(lambda: reported[-1:] == [30])
    finally:
        control.stop_watching()
    assert not alsaaudio.aborted
    assert 46 not in reported


def test_watch_of_a_headset_that_reconnects_at_once(alsaaudio, logger, monkeypatch, caplog):
    """Removed and added again before the watch reads the volume (bluetoothd restarted, box 2026-10-03): the
    control exists, but not in the watched handle any more."""
    monkeypatch.setattr(volumemanager, "WATCH_POLL_MS", 20)
    device = "bluealsa:AC:80:0A:11:22:33"
    alsaaudio.controls[device] = ["A2DP", "SCO"]
    reported: list[float] = []
    control = AlsaVolumeControl("A2DP", logger, device=device)
    control.watch(reported.append)
    try:
        assert wait_for(lambda: alsaaudio.created)
        alsaaudio.disconnect(device)
        alsaaudio.controls[device] = ["A2DP", "SCO"]  # connected again
        alsaaudio.external(37)
        assert wait_for(lambda: reported[-1:] == [37])
    finally:
        control.stop_watching()
    assert not alsaaudio.aborted
    assert "uncaught exception" not in caplog.text


def test_mixer_of_a_headset_that_is_not_connected(alsaaudio, logger, caplog):
    control = AlsaVolumeControl("A2DP", logger, device="bluealsa:AC:80:0A:11:22:33")
    assert control.set_volume(40) is False  # the caller may try again
    assert "set_volume - 'A2DP' 40% failed: no mixer bluealsa:AC:80:0A:11:22:33" in caplog.text
    assert alsaaudio.created == []


# --- limits -----------------------------------------------------------------------------------------------------------


def test_limits_can_be_changed(manager, control):
    manager.set_volume(70)
    manager.set_limits(30, 60)  # the current volume is above the new maximum
    assert (manager.get_volume(), manager.default_volume, manager.max_volume) == (60, 30, 60)
    assert control.volumes[-1] == 60
    manager.set_limits(20, 100)  # a new start value does not change the current volume
    assert manager.get_volume() == 60
    manager.reset()
    assert manager.get_volume() == 20
    assert control.volumes[-1] == 20


@pytest.mark.parametrize("limits", [(50, 40), (-1, 50), (50, 101), ("40", 50), (True, 50), (None, 50), (40, None)])
def test_invalid_limits(manager, limits):
    with pytest.raises(ValueError, match="start value"):
        check_limits(*limits)
    if all(isinstance(value, (int, float)) for value in limits):
        with pytest.raises(ValueError, match="start value"):
            manager.set_limits(*limits)
    assert (manager.default_volume, manager.max_volume) == (50, 80)


def test_valid_limits():
    assert check_limits(0, 0) == (0, 0)
    assert check_limits(40.5, 100) == (40.5, 100)


# --- speaker and headset ----------------------------------------------------------------------------------------------


def test_buttons_change_the_active_volume(logger, control):
    headphones = FakeVolumeControl()
    volumes = make_volumes(logger, control, CONFIG, headphones)
    assert volumes.active is VolumeProfile.SPEAKER
    volumes.volume_up()
    assert (volumes.get_volume(), control.volumes[-1]) == (55, 55)
    volumes.volume_up(held=True)
    volumes.volume_down(held=True)
    assert control.volumes[-2:] == [56, 55]
    volumes.use(VolumeProfile.HEADPHONES)
    assert volumes.get_volume() == HEADPHONES["default-volume"]
    volumes.volume_up()
    volumes.set_volume(99)  # limited to the maximum of the headset
    assert (volumes.get_volume(), headphones.volumes[-1]) == (75, 75)
    volumes.volume_down()
    volumes.update_volume()
    assert headphones.volumes[-2:] == [70, 70]
    assert volumes.manager(VolumeProfile.SPEAKER).get_volume() == 55  # untouched meanwhile
    volumes.volume_off()  # shutdown: the speaker is muted
    assert control.volumes[-1] == 0
    assert volumes.get_volume() == 70


def test_changed_limits_survive_a_restart(logger, tmp_path):
    filename = str(tmp_path / "volume.json")
    volumes = make_volumes(logger, config=CONFIG, limits=VolumeLimits(filename, logger))
    with open(filename, encoding="utf-8") as f:
        assert json.load(f) == {}  # nothing changed yet - the config applies
    volumes.set_limits(VolumeProfile.HEADPHONES, 30, 60)
    with pytest.raises(ValueError, match="start value"):
        volumes.set_limits(VolumeProfile.SPEAKER, 70, 60)
    with open(filename, encoding="utf-8") as f:
        assert json.load(f) == {"headphones": {"default-volume": 30, "max-volume": 60}}
    # restart: the stored limits win over the config, the speaker still follows the config
    restarted = make_volumes(
        logger, config=dict(CONFIG, **{"default-volume": 45}), limits=VolumeLimits(filename, logger)
    )
    headphones = restarted.manager(VolumeProfile.HEADPHONES)
    assert (headphones.get_volume(), headphones.default_volume, headphones.max_volume) == (30, 30, 60)
    assert restarted.manager(VolumeProfile.SPEAKER).default_volume == 45
    restarted.set_limits(VolumeProfile.SPEAKER, 10, 20)
    with open(filename, encoding="utf-8") as f:
        assert json.load(f) == {
            "headphones": {"default-volume": 30, "max-volume": 60},
            "speaker": {"default-volume": 10, "max-volume": 20},
        }


def test_broken_volume_file_is_replaced(logger, tmp_path, caplog):
    filename = tmp_path / "volume.json"
    filename.write_text('{"speaker": {"default-volume": 500}}')  # not valid
    limits = VolumeLimits(str(filename), logger)
    assert limits.apply("speaker", CONFIG) == CONFIG
    assert "no usable volume file" in caplog.text
    before = file_id(filename)
    limits.store("speaker", 20, 30)
    assert file_id(filename) != before
    assert limits.apply("speaker", CONFIG)["max-volume"] == 30
