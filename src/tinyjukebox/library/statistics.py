"""The play statistics (docs/developer/library.md, section 7): what the journals of the boxes say about what
was played when.

A journal line carries the box, the start of the box, a running number, the time since that start and the time of
day with a note whether the clock was synchronized then. The Pi has no real-time clock: for a line written before
the clock was set, the time of day is computed from another line of the same start that has it. For a start entirely
without WLAN the durations are right and only the time of day is unknown.

Switching a box off leaves no line. How long it ran is what the last line of that start says: a box writes a line
"alive" from time to time for that.

What belongs to one child is counted per box: the days, the hours of a day, speaker or headphones. The caller asks for
one box by passing its lines only; a box without a library counts its own journal this way.
"""

import datetime
import itertools
import time
from collections import defaultdict
from typing import Any

from tinyjukebox.sync.data import RECOVERY_GAP

__all__ = ["statistics"]

FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _times(lines: list[dict[str, Any]]) -> dict[tuple[str, int], float]:
    """Per box and start: the time of day (seconds since the epoch) at which the box started, where it is known."""
    started: dict[tuple[str, int], float] = {}
    for line in lines:
        clock = line.get("clock", {})
        if clock.get("synchronized"):
            moment = datetime.datetime.strptime(clock["time"], FORMAT).replace(tzinfo=datetime.UTC).timestamp()
            started[(line["box"], line["start"])] = moment - line["uptime"]
    return started


class _Session:
    """A tag on a box, from the moment it was placed."""

    def __init__(self, line: dict[str, Any]) -> None:
        self.uid = str(line.get("uid"))
        self.kind = line.get("kind")
        self.target = line.get("target")
        self.output = line.get("output") or "speaker"
        self.start = line["start"]
        self.began = line["uptime"]


def statistics(  # noqa: PLR0912, PLR0915 - one pass over the journal that counts everything
    lines: list[dict[str, Any]],
    records: list[dict[str, Any]],
    tags: dict[str, Any],
    targets: dict[str, Any],
    boxes: dict[str, str],
    gone: frozenset[str] = frozenset(),
    now: float | None = None,
) -> dict[str, Any]:
    """``lines``: the journal lines of all boxes in the order of their numbers; ``records``: what the library noted
    itself; ``tags`` and ``targets``: the catalog; ``boxes``: names by id; ``gone``: ids of boxes that were removed.
    For the statistics of one box: its lines and its records only."""
    started = _times(lines)
    per_tag: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"plays": 0, "seconds": 0.0, "finished": 0, "last": None, "target": None, "boxes": defaultdict(float)}
    )
    per_day: dict[tuple[str, str], dict[str, Any]] = defaultdict(  # per day and box: a day is the day of one box
        lambda: {"seconds": 0.0, "speaker": 0.0, "headphones": 0.0, "hours": defaultdict(float)}
    )
    stations: dict[str, float] = defaultdict(float)
    skipped: dict[tuple[str, int | str], int] = defaultdict(int)
    titles: dict[tuple[str, int | str], str] = {}  # the tracks of an entry of Spotify: by address, with their title
    per_box: dict[str, dict[str, Any]] = defaultdict(lambda: {"seconds": 0.0, "plays": 0, "last": None})
    versions: dict[str, dict[str, float]] = defaultdict(
        lambda: {"starts": 0, "errors": 0, "fallbacks": 0, "installed": 0.0, "running": 0.0, "active": 0.0}
    )
    runs: dict[tuple[str, int], dict[str, Any]] = {}  # per box and start: the version, and how long the box ran
    unknown_time = 0.0
    sessions: dict[str, _Session | None] = {}
    last: dict[str, dict[str, Any]] = {}  # the last line of every box

    def moment(line: dict[str, Any]) -> float | None:
        offset = started.get((line["box"], line["start"]))
        return None if offset is None else offset + line["uptime"]

    def listened(line: dict[str, Any], session: _Session, seconds: float) -> None:
        nonlocal unknown_time
        if seconds <= 0:
            return
        entry = per_tag[session.uid]
        entry["seconds"] += seconds
        entry["boxes"][line["box"]] += seconds
        per_box[line["box"]]["seconds"] += seconds
        versions[str(line.get("version"))]["active"] += seconds
        when = moment(line)
        if when is None:
            unknown_time += seconds
            return
        local = datetime.datetime.fromtimestamp(when)
        day = per_day[(local.strftime("%Y-%m-%d"), line["box"])]
        day["seconds"] += seconds
        day[session.output if session.output in ("speaker", "headphones") else "speaker"] += seconds
        day["hours"][local.hour] += seconds

    def close(box: str, line: dict[str, Any]) -> None:
        """The tag is gone - or the box was switched off, which leaves no line: then the last line of the start
        before says how long the session lasted at least."""
        session = sessions.get(box)
        sessions[box] = None
        if session is None or session.kind != "radio":
            return  # the tracks of an album were counted one by one
        end = line if line["start"] == session.start else last.get(box)
        if end is not None and end["start"] == session.start:
            seconds = end["uptime"] - session.began
            stations[str(session.target)] += max(0.0, seconds)
            listened(end, session, seconds)

    for line in lines:
        box, event = line["box"], line.get("event")
        if line.get("discarded") or event == RECOVERY_GAP:
            continue  # written by a version that was found defective: not trusted; or a line that was lost
        session = sessions.get(box)
        run = runs.setdefault((box, line["start"]), {"version": str(line.get("version")), "uptime": 0.0})
        run["uptime"] = max(run["uptime"], line["uptime"])
        if event == "start":
            close(box, line)
            versions[str(line.get("version"))]["starts"] += 1
        elif event == "tag":
            close(box, line)
            placed = sessions[box] = _Session(line)
            if placed.kind is not None:
                entry = per_tag[placed.uid]
                entry["target"] = placed.target  # what the tag played last
                when = moment(line)
                for counted in (entry, per_box[box]):
                    counted["plays"] += 1
                    if when is not None and (counted["last"] is None or when > counted["last"]):
                        counted["last"] = when
        elif event == "track" and session is not None:
            listened(line, session, float(line.get("seconds", 0)))
            if not line.get("to_end"):
                key: tuple[str, int | str]
                if line.get("uri"):  # an entry of Spotify: the place of a track in a playlist changes
                    key = (str(line.get("album")), str(line["uri"]))
                    titles[key] = str(line.get("title") or line["uri"])
                else:
                    key = (str(line.get("album")), int(line.get("track", 0)))
                skipped[key] += 1
        elif event == "end" and session is not None and line.get("reason") == "album finished":
            per_tag[session.uid]["finished"] += 1
        elif event == "end":
            close(box, line)
        elif event == "unexpected":
            versions[str(line.get("version"))]["errors"] += 1
        last[box] = line
    for record in records:
        if record.get("kind") == "switch" and record.get("why") == "health check failed":
            versions[str(record.get("from", "")).partition(":")[2]]["fallbacks"] += 1
    for run in runs.values():
        versions[run["version"]]["running"] += run["uptime"]
    for version, seconds in _installed(runs, started, gone, time.time() if now is None else now).items():
        versions[version]["installed"] += seconds
    return _tables(
        per_tag, per_day, stations, skipped, titles, per_box, versions, unknown_time, tags, targets, boxes, gone
    )


def _installed(
    runs: dict[tuple[str, int], dict[str, Any]], started: dict[tuple[str, int], float], gone: frozenset[str], now: float
) -> dict[str, float]:
    """Seconds a version was the one of a box, summed over the boxes: from its first start with a known time of day
    to the first start of another version, for the version a box has now until today. A box that was removed has its
    last version until it was seen last."""
    installed: dict[str, float] = defaultdict(float)
    per_box: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    for (box, start), run in sorted(runs.items()):
        if (box, start) in started:
            began = started[(box, start)]
            per_box[box].append((began, began + run["uptime"], run["version"]))
    for box, known in per_box.items():
        for (began, _, version), (following, _, _) in itertools.pairwise(known):
            installed[version] += max(0.0, following - began)
        began, seen, version = known[-1]
        installed[version] += max(0.0, (seen if box in gone else now) - began)
    return installed


def _stamp(when: float | None) -> str | None:
    return None if when is None else datetime.datetime.fromtimestamp(when, datetime.UTC).strftime(FORMAT)


def _tables(
    per_tag: dict[str, dict[str, Any]],
    per_day: dict[tuple[str, str], dict[str, Any]],
    stations: dict[str, float],
    skipped: dict[tuple[str, int | str], int],
    titles: dict[tuple[str, int | str], str],
    per_box: dict[str, dict[str, Any]],
    versions: dict[str, dict[str, float]],
    unknown_time: float,
    tags: dict[str, Any],
    targets: dict[str, Any],
    boxes: dict[str, str],
    gone: frozenset[str],
) -> dict[str, Any]:
    """What was counted, as the tables of the page."""

    def box_of(box: str) -> str:
        return boxes.get(box, box)

    def name_of(uid: str) -> str:
        return str(tags.get(uid, {}).get("tag", {}).get("name") or uid)

    def title_of(target: str) -> str:
        value = targets.get(target, {})
        return str(value.get("name") or value.get("title") or value.get("album-folder") or target)

    by_tag: list[dict[str, Any]] = [
        {
            "uid": uid,
            "name": name_of(uid),
            "album": title_of(str(entry["target"])) if entry["target"] is not None else None,
            "plays": entry["plays"],
            "seconds": round(entry["seconds"]),
            "finished": entry["finished"],
            "last": _stamp(entry["last"]),
            "boxes": [
                {"id": box, "name": box_of(box), "seconds": round(seconds)}
                for box, seconds in sorted(entry["boxes"].items(), key=lambda item: (-item[1], box_of(item[0])))
            ],
        }
        for uid, entry in per_tag.items()
    ]
    by_day = [
        {
            "day": day,
            "box": box,
            "name": box_of(box),
            "seconds": round(entry["seconds"]),
            "speaker": round(entry["speaker"]),
            "headphones": round(entry["headphones"]),
            "hours": {str(hour): round(seconds) for hour, seconds in sorted(entry["hours"].items())},
        }
        # the newest day first, the boxes of a day by name
        for (day, box), entry in sorted(
            sorted(per_day.items(), key=lambda item: box_of(item[0][1]).casefold()),
            key=lambda item: item[0][0],
            reverse=True,
        )
    ]
    # every box that is known, and every box that left something: a removed box stays as long as it has lines
    listed = [box for box in boxes if box not in gone or box in per_box] + [box for box in per_box if box not in boxes]
    by_box = [
        {
            "id": box,
            "name": box_of(box),
            "seconds": round(per_box[box]["seconds"]) if box in per_box else 0,
            "plays": per_box[box]["plays"] if box in per_box else 0,
            "last": _stamp(per_box[box]["last"]) if box in per_box else None,
        }
        for box in listed
    ]
    by_station: list[dict[str, Any]] = [
        {"id": target, "name": title_of(target), "seconds": round(seconds)} for target, seconds in stations.items()
    ]
    by_track: list[dict[str, Any]] = [
        {
            "album": album,
            "title": title_of(album),
            "track": track + 1 if isinstance(track, int) else titles[(album, track)],
            "count": count,
        }
        for (album, track), count in skipped.items()
    ]
    return {
        "tags": sorted(by_tag, key=lambda entry: (-entry["seconds"], entry["name"])),
        "never": sorted(name_of(uid) for uid in tags if per_tag.get(uid, {}).get("plays", 0) == 0),
        "days": by_day,
        "unknown_time": round(unknown_time),
        "stations": sorted(by_station, key=lambda entry: -entry["seconds"]),
        "skipped": sorted(by_track, key=lambda entry: -entry["count"])[:50],
        "versions": [
            {"version": version, **{key: round(value) for key, value in counts.items()}}
            for version, counts in sorted(versions.items())
        ],
        "boxes": sorted(by_box, key=lambda entry: (entry["name"].casefold(), entry["id"])),
    }
