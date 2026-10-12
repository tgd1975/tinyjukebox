"""The central place for exceptions that nobody expected.

A broken album, a dead radio station or a library that cannot be reached are expected errors and are handled where
they occur. What arrives here was caught at one of the places that keep the box alive whatever happens: a thread, a
state machine, the dispatch of the messages, a request of the web page. It was logged there already; the health check
of a new version (:mod:`tinyjukebox.slots`) listens here.
"""

import threading
from collections.abc import Callable

__all__ = ["Listener", "report", "subscribe", "unsubscribe"]

Listener = Callable[[BaseException, str], None]

_lock = threading.Lock()
_listeners: list[Listener] = []


def subscribe(listener: Listener) -> None:
    """``listener(error, where)`` is called for every unexpected exception from now on."""
    with _lock:
        _listeners.append(listener)


def unsubscribe(listener: Listener) -> None:
    with _lock:
        if listener in _listeners:
            _listeners.remove(listener)


def report(error: BaseException, where: str) -> None:
    """Tell the listeners. ``where``: the place that caught the exception, e.g. ``thread RestApi``."""
    with _lock:
        listeners = list(_listeners)
    for listener in listeners:
        listener(error, where)
