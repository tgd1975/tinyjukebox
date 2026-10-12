"""What a box and the library share: the interface between them, files with checksums, the handshake.

The design is in docs/developer/library.md. ``data``, ``link``, ``files`` and ``box`` are the side of a box,
``tinyjukebox.library`` is the library; ``client``, ``handshake``, ``manifest`` and ``util`` are used by both.
"""

import contextlib
import contextvars
from collections.abc import Iterator

__all__ = ["INTERFACE", "KINDS", "LIBRARY", "SOFTWARE", "acting_as", "actor"]

INTERFACE = 1
"""Version of the interface between box and library that this code speaks (a box) or serves (the library). Raised
only for a change that an older counterpart cannot cope with."""

KINDS = ("tag", "target", "position", "setting")
"""What is synchronized entry by entry. Tags and targets are the catalog, the same for all boxes; positions and
settings belong to one box."""

LIBRARY = "library"
"""The side that changed an entry last, if it was not a box: something entered on the page of the library."""

SOFTWARE = "software"
"""Who made a change that no person asked for: a position that moved on, a tag created for an unknown uid."""

_actor: contextvars.ContextVar[str] = contextvars.ContextVar("tinyjukebox_actor", default=SOFTWARE)


def actor() -> str:
    """Who is making the changes of this thread right now: a user of the web page, the console, or the software."""
    return _actor.get()


@contextlib.contextmanager
def acting_as(who: str) -> Iterator[None]:
    """Changes made inside are noted as made by ``who``, e.g. ``web:anna`` or ``console``."""
    token = _actor.set(who)
    try:
        yield
    finally:
        _actor.reset(token)
