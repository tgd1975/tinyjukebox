"""What the box could not load and works without (docs/developer/library.md, section 4.21).

A damaged or unknown entry of a json file, a damaged line of the journal, a file that cannot be read: the box leaves it
out and goes on with the rest instead of not starting. Each such case is noted here with a message, so that the log, the
page of the box and the library say that the box does not have everything. Noted per source (a file) and key (an
entry): loading the source again replaces its notes.
"""

import threading

__all__ = ["clear", "note", "notes", "replace"]

_lock = threading.Lock()
_notes: dict[str, dict[str, str]] = {}  # source -> key -> message


def note(source: str, key: str, message: str) -> None:
    with _lock:
        _notes.setdefault(source, {})[key] = message


def replace(source: str, messages: dict[str, str]) -> None:
    """The notes of ``source`` are these now (none: it is complete)."""
    with _lock:
        if messages:
            _notes[source] = dict(messages)
        else:
            _notes.pop(source, None)


def notes() -> list[str]:
    with _lock:
        return [message for source in sorted(_notes) for message in _notes[source].values()]


def clear() -> None:
    """Forget everything - for the tests, which run many boxes in one process."""
    with _lock:
        _notes.clear()
