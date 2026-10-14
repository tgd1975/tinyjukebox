"""Name and cover of an entry of Spotify, from Spotify's oEmbed address - it needs no account.

What it returns depends on the kind: an album, a playlist, a track and an artist come with their name and a square
cover. For a podcast (``show``) it returns its newest episode: that title is not taken, and its picture only if it is
square. An audiobook or an episode often returns nothing. Whatever fails leaves the name or the cover out - the entry
is then named by hand.
"""

import json
import logging
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tinyjukebox.data.target import spotify_kind, spotify_link
from tinyjukebox.images import MAX_IMAGE_SIZE, image_extension

__all__ = ["OEMBED", "Found", "Opener", "look_up"]

OEMBED = "https://open.spotify.com/oembed?url="
TIMEOUT = 10.0
MAX_ANSWER = 64 * 1024

Opener = Callable[..., Any]


@dataclass(frozen=True)
class Found:
    """What Spotify says about an entry: ``name`` and ``image`` (jpeg, png or webp) or ``None``."""

    name: str | None = None
    image: bytes | None = None


def _read(opener: Opener, url: str, limit: int) -> bytes:
    with opener(url, timeout=TIMEOUT) as response:
        return response.read(limit + 1)  # type: ignore[no-any-return]


def look_up(uri: str, logger: logging.Logger, opener: Opener = urllib.request.urlopen, image: bool = True) -> Found:
    """Name and cover of the entry ``uri`` (``spotify:<kind>:<id>``); ``image``: fetch the cover as well."""
    try:
        raw = _read(opener, OEMBED + urllib.parse.quote(spotify_link(uri), safe=""), MAX_ANSWER)
        answer = json.loads(raw[:MAX_ANSWER].decode("utf-8"))
    except (OSError, ValueError) as e:
        logger.info("no name and cover from Spotify for %s: %s", uri, e)
        return Found()
    if not isinstance(answer, dict):
        return Found()
    podcast = spotify_kind(uri) == "show"
    title = answer.get("title")
    name = title.strip()[:100] if isinstance(title, str) and title.strip() and not podcast else None
    width, height = answer.get("thumbnail_width"), answer.get("thumbnail_height")
    thumbnail = answer.get("thumbnail_url")
    data = None
    if image and isinstance(thumbnail, str) and thumbnail.startswith("https://") and (not podcast or width == height):
        try:
            data = _read(opener, thumbnail, MAX_IMAGE_SIZE)
        except OSError as e:
            logger.info("no cover from Spotify for %s: %s", uri, e)
        if data is not None and (len(data) > MAX_IMAGE_SIZE or image_extension(data) is None):
            data = None
    return Found(name, data)
