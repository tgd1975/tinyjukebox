"""Helpers of box and library (tinyjukebox.sync.util): whether the clock was set from the network."""

from tinyjukebox.sync import util


def test_the_clock_set_by_timesyncd_is_synchronized(tmp_path, monkeypatch):
    marker = tmp_path / "synchronized"
    marker.write_text("")
    monkeypatch.setattr(util, "TIMESYNC", str(marker))
    monkeypatch.setattr(util, "_kernel_max_error", lambda: util.MAX_ERROR * 10)
    assert util.clock_synchronized()


def test_without_timesyncd_the_kernel_tells(tmp_path, monkeypatch):
    """chrony, ntpd, a container with the clock of its host: the maximum error the kernel knows decides, as for
    systemd."""
    monkeypatch.setattr(util, "TIMESYNC", str(tmp_path / "missing"))
    monkeypatch.setattr(util, "_kernel_max_error", lambda: 5000)
    assert util.clock_synchronized()
    monkeypatch.setattr(util, "_kernel_max_error", lambda: util.MAX_ERROR)
    assert not util.clock_synchronized()


def test_the_kernel_can_be_asked_here():
    max_error = util._kernel_max_error()
    assert max_error is None or max_error >= 0
