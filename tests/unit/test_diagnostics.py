"""Central diagnostics on the box and in the library (docs/developer/library.md, section 4.22): what is collected,
what is removed before it leaves the box, how the buffer stays bounded, and how the library keeps the records."""

import logging
import time

import pytest

from tests.support import SHORT_MP3, wait_for
from tinyjukebox.albumplayer.simpleplayer import AudioPipeline
from tinyjukebox.diagnostics import Collector, diagnostic, redact
from tinyjukebox.library.diagnostics import Diagnostics


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Authorization: Box b3f2a1c9:Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5", "Authorization: Box <secret>"),
        ("login failed, password=hunter2 for anna", "login failed, password=<secret> for anna"),
        ('{"client_secret": "abc123"}', '{"client_secret": "<secret>"}'),
        (
            "GET https://radio.example/live.mp3?token=abc&listener=7 failed",
            "GET https://radio.example/live.mp3?<query> failed",
        ),
        ("https://anna:pw@radio.example/x", "https://<user>@radio.example/x"),
        ("tag 04:1A:2B:3C:4D:5E:6F placed", "tag <uid> placed"),
        ("plays spotify:track:4uLU6hMCjMI75M1A2tKUQC", "plays spotify:track:<id>"),
        ("-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----", "<pem>"),
        ("box b3f2a1c9 registered", "box b3f2a1c9 registered"),
    ],
)
def test_nothing_secret_or_personal_leaves_the_box(text, expected):
    assert redact(text) == expected


@pytest.fixture
def collected():
    logger = logging.getLogger("tests.diagnostics.TinyJukebox")
    logger.setLevel(logging.DEBUG)
    collector = Collector(capacity=5, root="tests.diagnostics.TinyJukebox")
    logger.addHandler(collector)
    yield logger, collector
    logger.removeHandler(collector)


def test_warnings_and_marked_events_are_collected(collected):
    logger, collector = collected
    logger.getChild("sync").info("an ordinary line")
    logger.getChild("sync").warning("the library answered %d", 503)
    logger.getChild("RadioPlayer").info(
        "stream https://r.example/a?k=1 started", extra=diagnostic("radio.startup", text="start of r.example", ms=12)
    )
    records, dropped = collector.take()
    assert [(r["level"], r["component"], r["code"], r["message"]) for r in records] == [
        ("WARNING", "sync", "the library answered %d", "the library answered 503"),
        ("INFO", "RadioPlayer", "radio.startup", "start of r.example"),
    ]
    assert records[1]["params"] == {"ms": 12}
    assert dropped == {}


def test_an_exception_comes_with_its_redacted_traceback(collected):
    logger, collector = collected
    try:
        raise ValueError("password=hunter2")
    except ValueError:
        logger.exception("loading failed")
    record = collector.take()[0][0]
    assert record["exception"] == "ValueError"
    assert "password=<secret>" in record["traceback"]
    assert "hunter2" not in record["traceback"]


def test_a_repeated_fault_is_one_record(collected):
    logger, collector = collected
    for _ in range(3):
        logger.warning("no connection")
    records, _ = collector.take()
    assert [r["repeated"] for r in records] == [3]
    logger.warning("no connection")  # a record that was handed out is not changed any more
    assert [r.get("repeated", 1) for r in collector.take()[0]] == [3, 1]


def test_the_buffer_drops_info_before_warnings_and_counts_it(collected):
    logger, collector = collected
    logger.error("error 1")
    for number in range(3):
        logger.info("info %d", number, extra=diagnostic("info"))
    logger.warning("warning 1")
    logger.warning("warning 2")
    logger.warning("warning 3")
    records, dropped = collector.take()
    assert [r["message"] for r in records] == ["error 1", "info 2", "warning 1", "warning 2", "warning 3"]
    assert dropped == {"INFO": 2}
    collector.delivered(records[2]["seq"], dropped)
    assert [r["message"] for r in collector.take()[0]] == ["warning 2", "warning 3"]
    assert collector.take()[1] == {}


def test_an_error_wakes_the_delivery(collected):
    logger, collector = collected
    woken = []
    collector.urgent = lambda: woken.append(1)
    logger.warning("not urgent")
    logger.error("urgent")
    assert woken == [1]


# --- the library ------------------------------------------------------------------------------------------------------


def record(seq, level="WARNING", **fields):
    return {"seq": seq, "time": f"2026-10-19T10:00:{seq:02d}Z", "uptime": seq, "level": level, "component": "sync",
            "code": f"code {seq}", "message": f"message {seq}", **fields}  # fmt: skip


def test_the_library_takes_a_record_once_and_filters(tmp_path):
    store = Diagnostics(str(tmp_path / "diagnostics.sqlite"))
    envelope = {"boot": 3, "version": "0.41.0", "records": [record(1), record(2, "ERROR", traceback="tb")]}
    assert store.add("box1", envelope) == 2
    assert store.add("box1", envelope) == 0  # sent again after a lost answer
    store.add("box2", {"boot": 1, "version": "0.40.0", "records": [record(1, "INFO")], "dropped": {"INFO": 4}})
    assert [r["message"] for r in store.query(box="box1")] == ["message 1", "message 2"]
    assert store.query(box="box1", level="ERROR")[0]["traceback"] == "tb"
    assert [r["code"] for r in store.query(box="box2", level="WARNING")] == ["diagnostics.dropped"]
    assert "4 INFO" in store.query(box="box2", code="diagnostics.dropped")[0]["message"]
    assert [r["box"] for r in store.query(text="message 2")] == ["box1"]
    assert store.query(version="0.40.0", level="DEBUG")[0]["box"] == "box2"
    assert store.choices("box1") == {"component": ["sync"], "version": ["0.41.0"], "code": ["code 1", "code 2"]}
    store.close()


def test_the_library_keeps_a_bounded_number_per_box(tmp_path):
    store = Diagnostics(str(tmp_path / "diagnostics.sqlite"), per_box=3)
    store.add("box1", {"boot": 1, "records": [record(n) for n in range(1, 6)]})
    store.prune()
    assert [r["seq"] for r in store.query(box="box1")] == [3, 4, 5]
    store.close()


def test_the_last_startup_failure_replaces_the_one_before(tmp_path):
    store = Diagnostics(str(tmp_path / "diagnostics.sqlite"))
    store.startup("box1", {"version": "0.40.0", "slot": "a", "failures": 1, "since": "2026-10-19T10:00:00Z",
                           "error": "Traceback\nValueError: first"})  # fmt: skip
    store.startup("box1", {"version": "0.40.0", "slot": "a", "failures": 7, "since": "2026-10-19T10:00:00Z",
                           "error": "Traceback\nValueError: targets.json"})  # fmt: skip
    assert store.startups()["box1"]["failures"] == 7
    store.started("box1")  # it runs again: the report becomes a record of its history
    assert store.startups() == {}
    history = store.query(box="box1", code="startup.failed")
    assert len(history) == 1
    assert history[0]["message"].endswith("ValueError: targets.json")
    store.close()


# --- the radio -------------------------------------------------------------------------------------------------------


def test_the_pipeline_tells_the_first_audio_and_breaks_of_the_sound():
    """ffmpeg says how much it decoded; aplay says when its buffer ran empty."""
    first: list[float] = []
    breaks: list[float] = []
    # aplay speaks the language of the box (as seen with aplay 1.2.15 on a German system)
    said = "underrun!!! (at least 12.500 ms long)\nUnterlauf!!! (mindestens 2844,463 ms)"
    output = ["sh", "-c", f"cat > /dev/null; printf '{said}\\n' >&2"]
    pipeline = AudioPipeline(
        SHORT_MP3, output=output, on_output=lambda: first.append(time.monotonic()), on_underrun=breaks.append
    )
    success, errors = pipeline.wait()
    assert success, errors
    assert errors == ""  # neither the progress of ffmpeg nor the break is an error message
    assert len(first) == 1
    assert wait_for(lambda: breaks == [12.5, 2844.463])
