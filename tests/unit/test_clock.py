"""SystemClock - the real time source of the players (the other tests use the ManualClock)."""

import threading
import time

from tinyjukebox.albumplayer.clock import SYSTEM_CLOCK, SystemClock


def test_now_is_monotonic_time():
    before = time.monotonic()
    now = SystemClock().now()
    assert before <= now <= time.monotonic()


def test_call_later_fires_in_a_daemon_thread():
    fired = threading.Event()
    threads: list[threading.Thread] = []

    def callback():
        threads.append(threading.current_thread())
        fired.set()

    start = time.monotonic()
    SYSTEM_CLOCK.call_later(0.05, callback)
    assert fired.wait(3)
    assert time.monotonic() - start >= 0.04
    assert threads[0] is not threading.main_thread()
    assert threads[0].daemon
    assert threads[0].name == "tinyjukebox.Timer"


def test_call_later_with_negative_delay_fires_immediately():
    fired = threading.Event()
    SystemClock().call_later(-1, fired.set)
    assert fired.wait(3)


def test_cancel_prevents_the_callback():
    fired = threading.Event()
    handle = SystemClock().call_later(0.1, fired.set)
    handle.cancel()
    assert not fired.wait(0.3)
