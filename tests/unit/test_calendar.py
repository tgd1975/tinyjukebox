"""Advent calendars of Spotify: the calendar and its figures in the catalog, the open days, the REST interface and
the console, the locked figure on the box, and how a chapter and a growing playlist play
(docs/developer/architecture.md, section 4.2)."""

import datetime
import json
from typing import Any, cast

import jsonschema
import pytest

from tests.support import CREATED, TAG_UNMAPPED, deliver_rfid, noop, wait_for
from tests.unit.test_spotify_play import FakeEngine, FakePlayer, FakeSoloist, playing
from tests.unit.test_web import JPEG, _fast_server, certificate, make_box, quote  # noqa: F401 - fixtures
from tinyjukebox.albumplayer.spotifyplayer import SpotifyPlayer, SpotifyState, Track
from tinyjukebox.data.activity import ActivityAlbum, calendar_key
from tinyjukebox.data.common import TargetType
from tinyjukebox.data.opendays import OpenDays
from tinyjukebox.data.tag import Tag
from tinyjukebox.data.target import TargetCalendar, check_calendar_link
from tinyjukebox.data.targets import Targets
from tinyjukebox.rgbled import Color
from tinyjukebox.schema.tags import schema as tags_schema
from tinyjukebox.schema.targets import schema as targets_schema
from tinyjukebox.spotify.oembed import Found
from tinyjukebox.stm.playspotify import PlaySpotify, States
from tinyjukebox.stm.stmoperation import States as StmStates
from tinyjukebox.stm.stmoperation import STMOperation

URI = "spotify:album:4aawyAB9vmqN3uQ7FjRGTy"
CALENDAR = {"target-id": "31", "target-type": "calendar", "name": "Adventskalender", "uri": URI, "mode": "chapters",
            "year": 2026, "locked": True, "created": CREATED}  # fmt: skip
TRACKS = [f"spotify:track:c{i}" for i in range(24)]


def day(n: int, year: int = 2026) -> datetime.date:
    return datetime.date(year, 12, n)


class Today:
    """The date of the box - the tests set it."""

    def __init__(self, date: datetime.date) -> None:
        self.date = date

    def __call__(self) -> datetime.date:
        return self.date


# --- the calendar in the catalog --------------------------------------------------------------------------------------


def test_calendar(logger):
    changes = []
    calendar = TargetCalendar(dict(CALENDAR), logger, lambda: changes.append(1))
    assert calendar.target_type == TargetType.calendar
    assert (calendar.kind, calendar.growing) == ("album", False)
    assert calendar.to_dict() == CALENDAR
    assert calendar.sanity_check() == (True, "")
    jsonschema.validate({"31": calendar.to_dict()}, targets_schema)
    calendar.update_calendar(mode="growing", year="2027", locked=False)
    assert (calendar.mode, calendar.year, calendar.locked, len(changes)) == ("growing", 2027, False, 1)
    with pytest.raises(ValueError, match="album or a playlist"):
        calendar.update_calendar(uri="spotify:track:abc")
    with pytest.raises(ValueError, match="kind"):
        calendar.update_calendar(mode="weekly")
    with pytest.raises(ValueError, match="year"):
        calendar.update_calendar(year=1999)
    assert calendar.uri == URI


def test_calendar_links():
    assert check_calendar_link("https://open.spotify.com/playlist/abc?si=1") == "spotify:playlist:abc"
    for link in ("https://open.spotify.com/show/abc", "spotify:track:abc", "nope"):
        with pytest.raises(ValueError, match=r"album or a playlist|Spotify|link"):
            check_calendar_link(link)


def test_a_broken_calendar_fails_the_sanity_check(logger):
    passed, errors = TargetCalendar(dict(CALENDAR, mode="x", year=3000), logger, noop).sanity_check()
    assert not passed
    assert "kind" in errors
    assert "year" in errors


def test_the_days_open(logger):
    calendar = TargetCalendar(dict(CALENDAR), logger, noop)
    assert calendar.opens(17) == day(17)
    assert not calendar.is_open(17, day(16))
    assert calendar.is_open(17, day(17))
    assert calendar.is_open(24, datetime.date(2027, 3, 1))
    calendar.update_calendar(locked=False)
    assert calendar.is_open(24, datetime.date(2026, 6, 1))


def test_targets_keep_calendars_apart_from_entries(library, logger):
    calendar = library.targets.add_target(
        {"target-type": "calendar", "name": "x", "uri": URI, "mode": "growing", "year": 2026}
    )
    library.targets.add_target({"target-type": "spotify", "name": "y", "uri": URI})
    assert isinstance(calendar, TargetCalendar)
    assert calendar.locked
    reloaded = Targets(library.targets_file, library.albums_folder, logger)
    assert [c.to_dict() for c in reloaded.calendars()] == [calendar.to_dict()]
    assert [e.name for e in reloaded.spotify_entries()] == ["y"]
    with pytest.raises(ValueError, match="album or a playlist"):
        library.targets.add_target({"target-type": "calendar", "name": "x", "uri": "spotify:show:a", "mode": "growing",
                                    "year": 2026})  # fmt: skip


def test_a_figure_has_its_day(library, logger):
    library.targets.add_target(dict(CALENDAR, **{"target-id": "31"}), create_id=False)
    library.tags.assign_target(TAG_UNMAPPED, TargetType.calendar, "31", 17)
    tag = library.tags.get_tag(TAG_UNMAPPED)
    assert tag.to_dict()["target"] == {"type": "calendar", "id": "31", "day": 17}
    jsonschema.validate({TAG_UNMAPPED: tag.to_dict()}, tags_schema)
    with pytest.raises(ValueError, match="day"):
        library.tags.assign_target(TAG_UNMAPPED, TargetType.calendar, "31")
    with pytest.raises(ValueError, match="day"):
        library.tags.assign_target(TAG_UNMAPPED, TargetType.album, "1", 3)
    library.tags.assign_target(TAG_UNMAPPED, TargetType.album, "1")
    assert "day" not in library.tags.get_tag(TAG_UNMAPPED).to_dict()["target"]
    loaded = Tag(logger, {"tag": {"uid": "a"}, "target": {"type": "calendar", "id": "31", "day": 3}}, None)
    assert loaded.day == 3


def test_a_growing_calendar_has_one_position(library):
    activity = library.activities.calendar_activity("31")
    assert activity.tag_uid == calendar_key("31") == "calendar:31"
    assert library.activities.calendar_activity("31") is activity
    activity.update(4, 12.5, True, TRACKS[4])
    with open(library.activities_file) as f:
        stored = json.load(f)["calendar:31"]
    assert stored == {"tag-uid": "calendar:31", "target-id": "31", "track-id": 4, "track-time": 12.5,
                      "album-finished": True, "target-type": "spotify", "track-uri": TRACKS[4]}  # fmt: skip


# --- the open days ----------------------------------------------------------------------------------------------------


def test_open_days_follow_the_date_and_stay_open(logger, tmp_path):
    today = Today(day(16))
    filename = str(tmp_path / "calendar-days.json")
    days = OpenDays(filename, logger, today)
    calendar = TargetCalendar(dict(CALENDAR), logger, noop)
    assert days.is_open(calendar, 16)
    assert not days.is_open(calendar, 17)
    today.date = day(17)
    assert days.is_open(calendar, 17)
    today.date = day(5)  # the clock is behind after a power cut: an open day stays open
    assert OpenDays(filename, logger, today).is_open(calendar, 17)
    assert not OpenDays(filename, logger, today).is_open(calendar, 18)
    calendar.update_calendar(year=2027)  # the calendar of another year opens its own days
    assert not OpenDays(filename, logger, Today(datetime.date(2027, 11, 30))).is_open(calendar, 1)
    calendar.update_calendar(locked=False)
    assert OpenDays(filename, logger, Today(datetime.date(2027, 11, 30))).is_open(calendar, 24)


def test_open_days_before_and_after_december(logger):
    calendar = TargetCalendar(dict(CALENDAR), logger, noop)
    assert not OpenDays(None, logger, Today(datetime.date(2026, 11, 30))).is_open(calendar, 1)
    assert OpenDays(None, logger, Today(datetime.date(2026, 12, 31))).is_open(calendar, 24)
    assert OpenDays(None, logger, Today(datetime.date(2030, 1, 1))).is_open(calendar, 24)


def test_a_damaged_file_of_open_days(logger, tmp_path):
    filename = tmp_path / "calendar-days.json"
    filename.write_text("{not json")
    calendar = TargetCalendar(dict(CALENDAR), logger, noop)
    assert OpenDays(str(filename), logger, Today(day(3))).is_open(calendar, 3)
    assert json.loads(filename.read_text()) == {"31": {"year": 2026, "day": 3}}


# --- REST and console -------------------------------------------------------------------------------------------------


@pytest.fixture
def calendars(make_box):  # noqa: F811 - the fixture
    box = make_box()
    box.control.spotify_lookup = lambda uri: Found("Adventskalender 2026", JPEG)
    box.control.open_days = OpenDays(None, box.logger, Today(day(10)))
    return box


def test_calendars_crud(calendars, library):
    admin, user = calendars.admin(), calendars.user()
    assert admin.get("/api/calendars").json == []
    link = "https://open.spotify.com/album/4aawyAB9vmqN3uQ7FjRGTy"
    assert user.post("/api/calendars", {"link": link, "mode": "chapters", "year": 2026}).status == 403
    assert admin.post("/api/calendars", {"link": "spotify:show:abc", "mode": "chapters", "year": 2026}).status == 400
    assert admin.post("/api/calendars", {"link": link, "mode": "daily", "year": 2026}).status == 400
    assert admin.post("/api/calendars", {"link": link, "mode": "chapters"}).status == 400
    created = admin.post("/api/calendars", {"link": link, "mode": "chapters", "year": 2026})
    assert created.status == 201
    calendar = created.json
    calendar_id = calendar["id"]
    assert (calendar["name"], calendar["uri"], calendar["mode"], calendar["year"], calendar["locked"]) == (
        "Adventskalender 2026", URI, "chapters", 2026, True)  # fmt: skip
    assert [d["day"] for d in calendar["days"]] == list(range(1, 25))
    assert calendar["days"][9] == {"day": 10, "opens": "2026-12-10", "open": True, "tags": []}
    assert calendar["days"][10]["open"] is False
    assert user.get(f"/api/calendars/{calendar_id}/cover").content == JPEG
    assert admin.get("/api/spotify").json == []  # a calendar is no entry of Spotify
    assert admin.get(f"/api/spotify/{calendar_id}").status == 404
    assert admin.get("/api/calendars/1").status == 404  # an album

    changed = admin.patch(f"/api/calendars/{calendar_id}", {"name": "Mine", "mode": "growing", "locked": False}).json
    assert (changed["name"], changed["mode"], changed["locked"]) == ("Mine", "growing", False)
    assert all(d["open"] for d in changed["days"])
    assert admin.patch(f"/api/calendars/{calendar_id}", {"year": "next"}).status == 400
    with open(library.targets_file) as f:
        stored = json.load(f)[calendar_id]
    assert stored | {"created": None, "cover": None} == {
        "target-id": calendar_id, "target-type": "calendar", "name": "Mine", "uri": URI, "mode": "growing",
        "year": 2026, "locked": False, "created": None, "cover": None}  # fmt: skip
    assert admin.delete(f"/api/calendars/{calendar_id}").status == 204
    assert admin.delete(f"/api/calendars/{calendar_id}").status == 404


def test_figures_of_a_calendar(calendars):
    admin = calendars.admin()
    calendar_id = admin.post("/api/calendars", {"link": URI, "mode": "chapters", "year": 2026}).json["id"]
    path = f"/api/tags/{quote(TAG_UNMAPPED)}/calendar"
    assert admin.put(path, {"calendar_id": calendar_id, "day": 25}).status == 400
    assert admin.put(path, {"calendar_id": "1", "day": 3}).status == 404  # an album
    tag = admin.put(path, {"calendar_id": calendar_id, "day": 17}).json
    assert (tag["calendar_id"], tag["calendar_exists"], tag["day"], tag["opens"], tag["open"]) == (
        calendar_id, True, 17, "2026-12-17", False)  # fmt: skip
    assert TAG_UNMAPPED not in [t["uid"] for t in admin.get("/api/tags?unmapped=1").json]
    created = admin.post("/api/tags", {"uid": "9:9:9:9", "calendar_id": calendar_id, "day": "17"}).json
    assert (created["calendar_id"], created["day"]) == (calendar_id, 17)
    assert admin.post("/api/tags", {"uid": "9:9:9:8", "calendar_id": calendar_id, "day": 0}).status == 400
    assert admin.post("/api/tags", {"uid": "9:9:9:7", "calendar_id": calendar_id, "album_id": "1"}).status == 400
    calendar = admin.get(f"/api/calendars/{calendar_id}").json
    assert {t["uid"] for t in calendar["days"][16]["tags"]} == {TAG_UNMAPPED, "9:9:9:9"}  # two figures: allowed
    assert admin.delete(f"/api/calendars/{calendar_id}").status == 409  # it has figures
    assert admin.delete(path).json["calendar_id"] is None


def test_console(calendars, capsys, monkeypatch):
    from tinyjukebox import cli as cli_module

    monkeypatch.setattr(cli_module, "_confirm", lambda: True)
    cli = cli_module.CLI(lambda *args: None, calendars.control)
    cli.cmd_add_calendar(f"{URI} growing 2026")
    assert "added calendar" in capsys.readouterr().out
    calendar_id = calendars.control.list_calendars()[0]["id"]
    cli.cmd_set_calendar_locked(f"{calendar_id} off")
    assert "all days open" in capsys.readouterr().out
    cli.cmd_link_tag_calendar(f"{TAG_UNMAPPED} {calendar_id} 5")
    assert f"-> calendar {calendar_id}, day 5" in capsys.readouterr().out
    cli.cmd_list_calendars("")
    assert f"figures: 5: {TAG_UNMAPPED}" in capsys.readouterr().out
    cli.cmd_list_tags("")
    assert f"calendar: {calendar_id}, day 5" in capsys.readouterr().out
    cli.cmd_remove_calendar(calendar_id)
    assert "has figures" in capsys.readouterr().out
    cli.cmd_unlink_tag(TAG_UNMAPPED)
    assert f"calendar {calendar_id}, day 5" in capsys.readouterr().out
    cli.cmd_remove_calendar(calendar_id)
    assert "removed calendar" in capsys.readouterr().out


def test_a_figure_cannot_play_without_a_player(calendars):
    admin = calendars.admin()
    calendar_id = admin.post("/api/calendars", {"link": URI, "mode": "chapters", "year": 2026}).json["id"]
    admin.put(f"/api/tags/{quote(TAG_UNMAPPED)}/calendar", {"calendar_id": calendar_id, "day": 3})
    calendars.place_tag(TAG_UNMAPPED)
    assert calendars.stmoperation.get_active_state() in (StmStates.BROKEN_ALBUM, StmStates.LOCKED_DAY)


# --- the figure on the box --------------------------------------------------------------------------------------------


class Playback:
    def __init__(self, calendar, activity, figure_day):
        self.calendar, self.activity, self.day = calendar, activity, figure_day

    def start(self):
        pass

    def stop(self):
        pass

    def get_info(self):
        return {"target_id": self.calendar.target_id, "target_type": "spotify"}


@pytest.fixture
def figures(library, pubsub, rgbled, logger):
    """STMOperation with a calendar 31 (chapters) and 32 (growing), the date of the box on 10 December."""
    today = Today(day(10))
    started: list[Playback] = []

    def calendar_factory(calendar, activity, figure_day):
        playback = Playback(calendar, activity, figure_day)
        started.append(playback)
        return playback

    library.targets.add_target(dict(CALENDAR), create_id=False)
    library.targets.add_target(dict(CALENDAR, **{"target-id": "32", "mode": "growing"}), create_id=False)
    for uid, calendar_id, figure_day in (("f3", "31", 3), ("f17", "31", 17), ("g1", "32", 1), ("g2", "32", 2)):
        library.tags.add_tag(uid)
        library.tags.assign_target(uid, TargetType.calendar, calendar_id, figure_day)
    stm = STMOperation(pubsub, logger, rgbled, library.tags, library.targets, library.activities, cast(Any, None),
                       poll_interval=60, calendar_factory=calendar_factory,
                       open_days=OpenDays(None, logger, today), day_check_interval=0.05)  # fmt: skip
    stm.start()
    stm.wait_until_idle()
    yield stm, started, today, rgbled
    stm.stop()


def place(stm, pubsub, uid):
    deliver_rfid(pubsub, uid)
    stm.wait_until_idle()


def test_a_figure_of_an_open_day_plays_its_chapter(figures, pubsub, library):
    stm, started, _, _ = figures
    place(stm, pubsub, "f3")
    assert stm.get_active_state() == StmStates.PLAY_ALBUM
    assert (started[-1].calendar.target_id, started[-1].day, started[-1].activity.tag_uid) == ("31", 3, "f3")


def test_a_figure_of_a_later_day_waits_for_its_day(figures, pubsub):
    stm, started, today, rgbled = figures
    place(stm, pubsub, "f17")
    assert stm.get_active_state() == StmStates.LOCKED_DAY
    assert rgbled.last() == ("blink", Color.YELLOW)
    assert not started
    today.date = day(17)  # midnight passes while the figure lies on the box
    wait_for(lambda: started)
    stm.wait_until_idle()
    assert stm.get_active_state() == StmStates.PLAY_ALBUM
    assert started[-1].day == 17
    deliver_rfid(pubsub, "f17", present=False)
    stm.wait_until_idle()
    assert stm.get_active_state() == StmStates.WAIT_FOR_TAG


def test_the_figures_of_a_growing_calendar_share_one_position(figures, pubsub, library):
    stm, started, _, _ = figures
    place(stm, pubsub, "g1")
    deliver_rfid(pubsub, "g1", present=False)
    place(stm, pubsub, "g2")
    assert [p.activity for p in started[-2:]] == [library.activities.calendar_activity("32")] * 2
    assert started[-1].activity.tag_uid == "calendar:32"
    assert "g1" not in library.activities.activities


# --- how a calendar plays ---------------------------------------------------------------------------------------------


def test_a_chapter_plays_alone(logger):
    engine = FakeEngine(FakeSoloist(tracks=TRACKS[:5]))
    finished: list[bool] = []
    played = SpotifyPlayer(logger, "spotify:album:a", cast(Any, engine), wait=2.0, retry_delays=(0.05,))
    played.first, played.single = 2, True
    played.on_finished = lambda: finished.append(True)
    played.start(Track(2), 0.0)
    try:
        playing(played)
        assert played.position()[0].uri == TRACKS[2]
        played.prev()  # the start of the chapter, not the day before
        wait_for(lambda: "seek 0" in engine.soloist.commands)
        assert played.position()[0].uri == TRACKS[2]
        engine.soloist.ctl("next")  # the chapter ended: Soloist goes on to the next track
        wait_for(lambda: finished)
        assert played.state == SpotifyState.FINISHED
        assert played.position()[0].uri == TRACKS[2]  # the position stays at the chapter
        played.restart()
        wait_for(lambda: played.state == SpotifyState.PLAYING and played.position()[0].uri == TRACKS[2])
        played.next()  # "next" ends the chapter
        wait_for(lambda: len(finished) == 2)
    finally:
        played.stop()


@pytest.mark.parametrize(("tracks", "expected"), [(TRACKS[:4], TRACKS[3]), (TRACKS[:3], TRACKS[2])])
def test_a_growing_calendar_goes_onward(logger, tracks, expected):
    """Finished at the third chapter: a fourth that Spotify has added plays next - without it the third again; what
    Spotify adds by itself is never taken for a chapter."""
    engine = FakeEngine(FakeSoloist(tracks=tracks, autoplay_after="spotify:track:other"))
    played = SpotifyPlayer(logger, "spotify:playlist:a", cast(Any, engine), wait=2.0, retry_delays=(0.05,))
    played.start(Track(2, TRACKS[2]), 0.0, onward=True)
    try:
        playing(played)
        assert played.position()[0].uri == expected
        assert played.position()[1] < 5
    finally:
        played.stop()


ENTRY_GROWING = dict(CALENDAR, mode="growing", uri="spotify:playlist:abc")


class OnwardPlayer(FakePlayer):
    """Notes whether it was started onward."""

    def start(self, track, seconds, onward=False):
        super().start(track, seconds)
        self.calls[-1] = ("start", track, seconds, onward)


@pytest.fixture
def make_calendar_play(logger, rgbled, pubsub):
    made = []

    def make(calendar=CALENDAR, activity=None, figure_day=3):
        player = OnwardPlayer()
        entry = TargetCalendar(dict(calendar), logger, noop)
        play: Any = PlaySpotify(logger, rgbled, entry, activity, lambda log, uri: cast(Any, player), pubsub,
                                update_schedule=60, day=figure_day)  # fmt: skip
        play.fake = player
        play.start()
        made.append(play)
        play.wait_until_idle()
        return play

    yield make
    for play in made:
        play.stop()


def activity(track_id, seconds, finished, uri, logger):
    entry = {"tag-uid": "f", "target-id": "31", "track-id": track_id, "track-time": seconds,
             "album-finished": finished, "target-type": "spotify", "track-uri": uri}  # fmt: skip
    return ActivityAlbum(entry, noop, logger)


def test_a_chapter_starts_at_its_day(make_calendar_play, logger):
    play = make_calendar_play(figure_day=3)
    assert (play.fake.first, play.fake.single) == (2, True)
    assert play.fake.calls[0] == ("start", Track(2), 0.0, False)
    resumed = make_calendar_play(activity=activity(2, 31.0, False, TRACKS[2], logger), figure_day=3)
    assert resumed.fake.calls[0] == ("start", Track(2, TRACKS[2]), 31.0, False)
    again = make_calendar_play(activity=activity(2, 199.0, True, TRACKS[2], logger), figure_day=3)
    assert again.fake.calls[0] == ("start", Track(2), 0.0, False)
    other = make_calendar_play(activity=activity(7, 10.0, False, TRACKS[7], logger), figure_day=3)
    assert other.fake.calls[0] == ("start", Track(2), 0.0, False)  # the position of another day
    assert play.get_info()["day"] == 3


def test_a_finished_growing_calendar_goes_onward(make_calendar_play, logger):
    play = make_calendar_play(ENTRY_GROWING, activity(4, 200.0, True, TRACKS[4], logger))
    assert play.fake.calls[0] == ("start", Track(4, TRACKS[4]), 0.0, True)
    assert not getattr(play.fake, "single", False)
    resumed = make_calendar_play(ENTRY_GROWING, activity(4, 50.0, False, TRACKS[4], logger))
    assert resumed.fake.calls[0] == ("start", Track(4, TRACKS[4]), 50.0, False)
    assert play.get_state() == States.PLAY
