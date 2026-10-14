"""The play statistics of entries of Spotify (docs/developer/library.md, section 7)."""

from tests.sync.test_statistics import at, line
from tinyjukebox.library.statistics import statistics

TAGS = {"S": {"tag": {"uid": "S", "name": "Hörspiel"}}}
TARGETS = {"9": {"name": "Die drei ???", "uri": "spotify:playlist:abc"}}


def test_an_entry_counts_like_an_album_and_its_skipped_tracks_by_title():
    lines = [
        line(1, "start", 0.0, time="2026-10-06T10:00:00Z"),
        line(2, "tag", 10.0, uid="S", kind="spotify", target="9", output="speaker"),
        line(3, "track", 70.0, album="9", track=0, seconds=60.0, to_end=True, uri="spotify:track:a", title="Folge 1"),
        line(4, "track", 80.0, album="9", track=1, seconds=10.0, to_end=False, uri="spotify:track:b", title="Folge 2"),
        line(5, "track", 90.0, album="9", track=2, seconds=10.0, to_end=False, uri="spotify:track:b", title="Folge 2"),
        line(6, "end", 95.0, uid="S", reason="album finished"),
    ]
    result = statistics(lines, [], TAGS, TARGETS, {"b1": "Kinderzimmer"}, now=at("2026-10-08T10:00:00Z"))
    entry = result["tags"][0]
    assert (entry["name"], entry["album"], entry["plays"], entry["seconds"], entry["finished"]) == (
        "Hörspiel", "Die drei ???", 1, 80, 1)  # fmt: skip
    # the same track at another place of the playlist is the same track
    assert result["skipped"] == [{"album": "9", "title": "Die drei ???", "track": "Folge 2", "count": 2}]
    assert result["stations"] == []
