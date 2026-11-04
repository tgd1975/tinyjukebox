"""Journal and play statistics (docs/developer/library.md, section 7)."""

import datetime

import pytest

from tests.support import TAG_ALBUM, TAG_UNMAPPED, ApiClient, wait_for
from tinyjukebox import __version__
from tinyjukebox.library.statistics import statistics
from tinyjukebox.sync import INTERFACE
from tinyjukebox.sync.client import HttpError
from tinyjukebox.sync.data import JOURNAL_LIMIT


def line(n, event, uptime, start=1, time=None, box="b1", **fields):
    """A journal line of a box; ``time``: its clock was synchronized then."""
    clock = {"time": time or "1970-01-01T00:00:10Z", "synchronized": time is not None}
    return {"box": box, "n": n, "start": start, "uptime": uptime, "clock": clock, "version": "0.9.0", "event": event,
            "discarded": False, **fields}  # fmt: skip


TAGS = {"A": {"tag": {"uid": "A", "name": "Album A"}}, "B": {"tag": {"uid": "B", "name": "Radio"}},
        "C": {"tag": {"uid": "C", "name": "never"}}}  # fmt: skip
TARGETS = {"1": {"album-folder": "Album A (Hörspiel)"}, "2": {"name": "FM4"}}
DAY = 24 * 3600


def at(time):
    return datetime.datetime.strptime(time, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.UTC).timestamp()


def test_tracks_stations_and_the_time_of_day():
    lines = [
        line(1, "start", 0.0),
        # the clock is not set yet: the time of day comes from line 5 of the same start
        line(2, "tag", 10.0, uid="A", kind="album", target="1", output="headphones"),
        line(3, "track", 70.0, album="1", track=0, seconds=60.0, to_end=True),
        line(4, "track", 80.0, album="1", track=1, seconds=10.0, to_end=False),
        line(5, "end", 80.0, time="2026-10-06T10:01:20Z", uid="A", reason="tag removed"),
        line(6, "tag", 100.0, time="2026-10-06T10:01:40Z", uid="B", kind="radio", target="2", output="speaker"),
        line(7, "end", 400.0, time="2026-10-06T10:06:40Z", uid="B", reason="tag removed"),
    ]
    result = statistics(lines, [], TAGS, TARGETS, {"b1": "Kinderzimmer"}, now=at("2026-10-08T10:00:00Z"))
    album, radio = result["tags"][1], result["tags"][0]
    assert (radio["name"], radio["album"], radio["plays"], radio["seconds"]) == ("Radio", "FM4", 1, 300)
    assert (album["name"], album["plays"], album["seconds"]) == ("Album A", 1, 70)
    assert album["boxes"] == [{"id": "b1", "name": "Kinderzimmer", "seconds": 70}]
    assert album["album"] == "Album A (Hörspiel)"
    assert album["last"] == "2026-10-06T10:00:10Z"  # corrected: the box started at 10:00:00
    assert result["never"] == ["never"]
    assert result["stations"] == [{"id": "2", "name": "FM4", "seconds": 300}]
    assert result["skipped"] == [{"album": "1", "title": "Album A (Hörspiel)", "track": 2, "count": 1}]
    assert len(result["days"]) == 1
    day = result["days"][0]
    assert (day["seconds"], day["headphones"], day["speaker"]) == (370, 70, 300)
    assert (day["box"], day["name"]) == ("b1", "Kinderzimmer")
    assert result["unknown_time"] == 0
    assert result["boxes"] == [
        {"id": "b1", "name": "Kinderzimmer", "seconds": 370, "plays": 2, "last": "2026-10-06T10:01:40Z"}
    ]
    # installed since the box started at 10:00:00 two days ago; it ran for 400 seconds, 370 of them with sound
    assert result["versions"] == [
        {
            "version": "0.9.0",
            "starts": 1,
            "errors": 0,
            "fallbacks": 0,
            "installed": 2 * DAY,
            "running": 400,
            "active": 370,
        }
    ]


def test_a_start_without_wlan_a_box_switched_off_and_discarded_lines():
    lines = [
        line(1, "start", 0.0),
        line(2, "tag", 5.0, uid="A", kind="album", target="1"),
        line(3, "track", 35.0, album="1", track=0, seconds=30.0, to_end=True),
        line(4, "end", 36.0, uid="A", reason="album finished"),
        line(5, "tag", 50.0, uid="B", kind="radio", target="2"),
        line(6, "unexpected", 80.0, error="boom"),
        # the box was switched off while the station played: the last line of that start closes the session
        line(7, "start", 0.0, start=2),
        {**line(8, "tag", 5.0, start=2, uid="A", kind="album", target="1"), "discarded": True},
    ]
    records = [{"kind": "switch", "box": "b1", "from": "b:0.9.0", "to": "a:0.8.0", "why": "health check failed"}]
    result = statistics(lines, records, TAGS, TARGETS, {})
    by_name = {tag["name"]: tag for tag in result["tags"]}
    assert (by_name["Album A"]["plays"], by_name["Album A"]["finished"], by_name["Album A"]["seconds"]) == (1, 1, 30)
    assert by_name["Radio"]["seconds"] == 30  # until the last line before the box was switched off
    assert result["days"] == []  # the durations are right, only the time of day is unknown
    assert result["unknown_time"] == 60
    # without a time of day nobody knows since when the version is installed
    assert result["versions"] == [
        {"version": "0.9.0", "starts": 2, "errors": 1, "fallbacks": 1, "installed": 0, "running": 80, "active": 60}
    ]


def test_how_long_a_version_was_installed_and_how_long_the_boxes_ran():
    lines = [
        line(1, "start", 0.0, time="2026-10-01T08:00:00Z"),
        # nothing is played: the line "alive" says that the box was still switched on after an hour
        line(2, "alive", 3600.0, time="2026-10-01T09:00:00Z"),
        line(3, "start", 0.0, start=2, time="2026-10-03T08:00:00Z", version="1.0.0"),
        line(4, "alive", 7200.0, start=2, time="2026-10-03T10:00:00Z", version="1.0.0"),
        # a box that was removed from the library: its version counts until the box was seen last
        line(1, "start", 0.0, box="b2", time="2026-10-01T08:00:00Z"),
        line(2, "alive", 600.0, box="b2", time="2026-10-01T08:10:00Z"),
    ]
    result = statistics(lines, [], TAGS, TARGETS, {}, gone=frozenset({"b2"}), now=at("2026-10-04T08:00:00Z"))
    versions = {entry["version"]: entry for entry in result["versions"]}
    old, new = versions["0.9.0"], versions["1.0.0"]
    assert (old["installed"], old["running"], old["active"]) == (2 * DAY + 600, 3600 + 600, 0)
    assert (new["installed"], new["running"], new["active"]) == (DAY, 7200, 0)  # the version the box has now
    assert (old["starts"], new["starts"]) == (2, 1)


def test_a_day_belongs_to_one_box():
    """Two children listen on the same day: their times and hours are not added up, a figure names both boxes."""
    lines = [
        line(1, "start", 0.0, time="2026-10-06T07:00:00Z"),
        line(2, "tag", 10.0, time="2026-10-06T07:00:10Z", uid="A", kind="album", target="1", output="headphones"),
        line(3, "track", 610.0, time="2026-10-06T07:10:10Z", album="1", track=0, seconds=600.0, to_end=True),
        line(1, "start", 0.0, box="b2", time="2026-10-06T15:00:00Z"),
        line(2, "tag", 10.0, box="b2", time="2026-10-06T15:00:10Z", uid="A", kind="album", target="1"),
        line(3, "track", 130.0, box="b2", time="2026-10-06T15:02:10Z", album="1", track=0, seconds=120.0, to_end=True),
    ]
    names = {"b1": "Kinderzimmer", "b2": "Anna", "b3": "Keller", "b4": "weg"}
    result = statistics(lines, [], TAGS, TARGETS, names, gone=frozenset({"b4"}))
    assert [(day["day"], day["name"], day["seconds"]) for day in result["days"]] == [
        ("2026-10-06", "Anna", 120),
        ("2026-10-06", "Kinderzimmer", 600),
    ]
    assert [len(day["hours"]) for day in result["days"]] == [1, 1]
    assert (result["days"][0]["speaker"], result["days"][1]["headphones"]) == (120, 600)
    album = result["tags"][0]
    assert (album["plays"], album["seconds"]) == (2, 720)
    assert [(box["name"], box["seconds"]) for box in album["boxes"]] == [("Kinderzimmer", 600), ("Anna", 120)]
    # every box that is known, also one that played nothing yet - not one that was removed and left nothing
    assert [(box["name"], box["seconds"], box["plays"]) for box in result["boxes"]] == [
        ("Anna", 120, 1),
        ("Keller", 0, 0),
        ("Kinderzimmer", 600, 1),
    ]
    # one box: its lines only
    alone = statistics([entry for entry in lines if entry["box"] == "b2"], [], TAGS, TARGETS, names)
    assert [(day["name"], day["seconds"]) for day in alone["days"]] == [("Anna", 120)]
    assert alone["tags"][0]["seconds"] == 120


def test_a_box_notes_from_time_to_time_that_it_is_switched_on(bed, monkeypatch):
    monkeypatch.setattr("tinyjukebox.sync.box.ALIVE_SECONDS", 0.05)
    box = bed.box("box1")
    assert box.synced()
    assert wait_for(lambda: "alive" in [entry["event"] for entry in bed.library.store.journal.lines()])
    assert bed.admin().get("/api/library/statistics").json["versions"][0]["running"] >= 0


def test_what_a_box_plays_arrives_in_the_statistics_of_the_library(bed):
    box = bed.box("box1")
    assert box.synced()
    played_on(bed, box)
    assert box.synced()
    assert wait_for(lambda: not box.sync.data.journal_lines(1))  # delivered and confirmed: deleted on the box
    result = bed.admin().get("/api/library/statistics").json
    played = {tag["uid"]: tag for tag in result["tags"]}
    assert (played[TAG_ALBUM]["plays"], played[TAG_ALBUM]["finished"]) == (1, 1)
    assert played[TAG_ALBUM]["seconds"] >= 1
    assert "unmapped" in result["never"]
    assert TAG_UNMAPPED not in played
    assert result["versions"][0]["version"] == __version__
    assert result["versions"][0]["starts"] == 1
    events = [entry["event"] for entry in bed.library.store.journal.lines()]
    assert events[0] == "start"
    assert events.count("track") == 3
    assert "end" in events


def played_on(bed, box):
    """The album of TAG_ALBUM played to its end, then the tag taken away. The state is "finished" a moment before
    the box notes the end in its journal; a tag taken away in between would end the session first, and the end of
    the album would not count - so it waits for the line, on the box or, once delivered, in the library."""
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")
    playback = lambda: (box.control.status()["album"] or {}).get("playback", {})  # noqa: E731
    assert wait_for(lambda: playback().get("state") == "finished", timeout=10)

    def noted():
        lines = box.sync.data.journal_lines(None) + [
            entry for entry in bed.library.store.journal.lines() if entry.get("box") == box.sync.data.box_id
        ]
        return any(entry.get("reason") == "album finished" for entry in lines)

    assert wait_for(noted, timeout=10)
    box.place(None)


def test_the_library_shows_one_box_and_a_box_shows_its_own(bed):
    first, second = bed.box("box1"), bed.box("box2")
    assert first.synced()
    assert second.synced()
    played_on(bed, first)
    assert first.synced()
    assert wait_for(lambda: not first.sync.data.journal_lines(1))
    one, other = first.sync.data.box_id, second.sync.data.box_id
    everything = bed.admin().get("/api/library/statistics").json
    assert everything["box"] is None
    assert {box["id"]: box["plays"] for box in everything["boxes"]} == {one: 1, other: 0}
    assert [day["box"] for day in everything["days"]] == [one]
    # the library, one box chosen: what this box played, and still every box to choose from
    chosen = bed.admin().get(f"/api/library/statistics?box={other}").json
    assert (chosen["box"], chosen["tags"], chosen["days"]) == (other, [], [])
    assert "album" in chosen["never"]
    assert len(chosen["boxes"]) == 2
    assert bed.admin().get("/api/library/statistics?box=nobody").status == 404
    # the page of a box: the library counts, and a box hears of nobody but itself
    own = first.control.statistics()
    assert (own["origin"], own["missing"], own["box"]) == ("library", None, one)
    assert [box["id"] for box in own["boxes"]] == [one]
    assert [(tag["uid"], tag["plays"], tag["finished"]) for tag in own["tags"]] == [(TAG_ALBUM, 1, 1)]
    assert second.control.statistics()["tags"] == []
    first.disconnect()
    assert first.control.statistics() == {"origin": "library", "missing": "unreachable"}


def test_a_box_cannot_ask_for_the_statistics_of_another_box(bed):
    first, second = bed.box("box1"), bed.box("box2")
    assert first.synced()
    assert second.synced()
    client, other = first.sync.link.client, second.sync.data.box_id
    with pytest.raises(HttpError) as refused:
        client.get(f"/api/sync/v{INTERFACE}/boxes/{other}/statistics")
    assert refused.value.status == 403


def test_a_box_without_a_library_counts_its_own_journal(bed):
    box = bed.box("box1", connect=False)
    played_on(bed, box)
    own = box.control.statistics()
    assert (own["origin"], own["missing"], own["limit"]) == ("box", None, JOURNAL_LIMIT)
    assert [(tag["name"], tag["album"], tag["plays"], tag["finished"]) for tag in own["tags"]] == [
        ("album", "short", 1, 1)
    ]
    assert own["tags"][0]["seconds"] >= 1
    assert "unmapped" in own["never"]
    assert len(own["days"]) == 1
    assert own["versions"][0]["version"] == __version__


def test_a_library_that_is_older_than_the_box(bed, monkeypatch):
    """The package of the library is installed by hand: a box may be newer and ask for what the library lacks."""
    box = bed.box("box1")
    assert box.synced()

    def unknown(path):
        raise HttpError(404, "not found.")

    monkeypatch.setattr(box.sync.link.client, "get", unknown)
    assert box.control.statistics() == {"origin": "library", "missing": "library_too_old"}


def test_every_user_sees_the_statistics_and_the_journal_stays_with_admins(bed, certificate):
    assert bed.admin().post("/api/users", {"username": "anna", "password": "anna-password"}).status == 201
    user = ApiClient(bed.library.api.port, certificate[0]).login("anna", "anna-password")
    assert user.get("/api/library/statistics").status == 200
    assert user.get("/api/library/journal").status == 403
    assert user.get("/api/library/statistics", token="").status == 401
