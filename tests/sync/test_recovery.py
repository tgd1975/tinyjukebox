"""A damaged journal (docs/developer/library.md, sections 4.21 and 7): the box keeps every complete line, marks the
lost numbers as ``recovery_gap`` and starts; the library goes on past them and counts no listening for them."""

import json

from tests.support import TAG_ALBUM, wait_for
from tinyjukebox.data import degraded
from tinyjukebox.sync.data import RECOVERY_GAP, BoxData


def journal_line(n, event="track", start=1):
    return {"n": n, "start": start, "uptime": float(n), "clock": {"time": "2026-10-17T10:00:00Z", "synchronized": True},
            "version": "0.39.1", "event": event}  # fmt: skip


def write_journal(folder, lines, tail=b"", reserved=None):
    """The journal as a power cut left it: ``lines``, then ``tail``; ``reserved``: the last number in numbers.json."""
    with open(folder / "journal", "wb") as outfile:
        outfile.write(b"".join(json.dumps(line).encode() + b"\n" for line in lines) + tail)
    (folder / "numbers.json").write_text(json.dumps({"journal": reserved or lines[-1]["n"], "start": 1}))


def numbers_and_events(data):
    return [(line["n"], line["event"]) for line in data.journal_lines(None)]


def test_a_journal_with_a_line_of_zeros_keeps_every_complete_line(tmp_path, logger):
    write_journal(tmp_path, [journal_line(912), journal_line(913)], tail=b"\0" * 405, reserved=915)
    data = BoxData(str(tmp_path), "box", "0.40.0", logger)
    assert numbers_and_events(data) == [(912, "track"), (913, "track"), (914, RECOVERY_GAP), (915, RECOVERY_GAP)]
    assert degraded.notes() == ["journal: 1 damaged line(s); lines lost: 914-915"]
    # the file is valid JSONL again, and the next line follows the numbers that were reserved
    data.journal("start")
    lines = [json.loads(line) for line in (tmp_path / "journal").read_text().splitlines()]
    assert [line["n"] for line in lines] == [912, 913, 914, 915, 916]


def test_a_number_without_its_line_becomes_a_gap(tmp_path, logger):
    """The number was saved, then the power was cut before its line reached the card (acceptance test 6)."""
    write_journal(tmp_path, [journal_line(1), journal_line(3)], reserved=4)
    data = BoxData(str(tmp_path), "box", "0.40.0", logger)
    assert numbers_and_events(data) == [(1, "track"), (2, RECOVERY_GAP), (3, "track"), (4, RECOVERY_GAP)]
    gap = data.journal_lines(None)[1]
    assert (gap["start"], gap["version"]) == (2, "0.40.0")  # written by this start


def test_a_complete_journal_is_left_as_it_is(tmp_path, logger):
    write_journal(tmp_path, [journal_line(1), journal_line(2)])
    before = (tmp_path / "journal").read_bytes()
    data = BoxData(str(tmp_path), "box", "0.40.0", logger)
    assert numbers_and_events(data) == [(1, "track"), (2, "track")]
    assert (tmp_path / "journal").read_bytes() == before
    assert degraded.notes() == []


def test_the_library_goes_on_past_lost_lines_and_counts_no_listening_for_them(bed):
    box = bed.box("box1")
    assert box.synced()
    assert wait_for(lambda: not box.sync.data.journal_lines(1))
    confirmed = bed.library.store.boxes()[0]["journal"]
    folder = box.folder / "sync"
    box.stop()
    # the lines after the cursor of the library are lost, the box has a later one, and its last line is zeros
    first = confirmed + 3
    write_journal(folder, [journal_line(first, "start", start=5)], tail=b"\0" * 40, reserved=first + 1)
    numbers = json.loads((folder / "numbers.json").read_text())
    (folder / "numbers.json").write_text(json.dumps({**numbers, "journal": first + 1, "start": 5}))
    box.start()
    assert box.synced()
    assert wait_for(lambda: bed.library.store.boxes()[0]["journal"] >= first + 1)
    lines = {line["n"]: line["event"] for line in bed.library.store.journal.lines()}
    assert [lines[n] for n in range(confirmed + 1, first + 2)] == [RECOVERY_GAP, RECOVERY_GAP, "start", RECOVERY_GAP]
    # the box says that lines were lost: on its page and to the library
    assert box.control.system_info()["degraded"] == [f"journal: 1 damaged line(s); lines lost: {first + 1}"]
    assert wait_for(
        lambda: bed.library.store.boxes()[0]["status"].get("degraded") == box.control.system_info()["degraded"]
    )
    statistics = bed.admin().get("/api/library/statistics").json
    assert TAG_ALBUM not in [tag["uid"] for tag in statistics["tags"]]
    assert statistics["versions"][0]["starts"] >= 1
