"""Latency log of the reaction chains."""

import logging
import time

from tinyjukebox.latency import ReactionTimer


def messages(caplog):
    return [record.getMessage() for record in caplog.records if record.name.endswith(".latency")]


def test_one_line_per_step(logger, caplog):
    caplog.set_level(logging.INFO)
    timer = ReactionTimer("tag", logger)
    timer.begin(time.monotonic() - 0.05)
    timer.step("NEW_TAG")
    timer.end("ffmpeg started")
    lines = messages(caplog)
    assert len(lines) == 2
    assert lines[0].startswith("tag: NEW_TAG after ")
    assert lines[1].startswith("tag: ffmpeg started after ")
    assert int(lines[1].split()[-2]) >= 50


def test_steps_without_trigger_are_ignored(logger, caplog):
    caplog.set_level(logging.INFO)
    timer = ReactionTimer("tag", logger)
    timer.end("ffmpeg started")  # e.g. the next track
    timer.begin()
    timer.end("ffmpeg started")
    timer.end("ffmpeg started")
    assert len(messages(caplog)) == 1


def test_begin_without_time_starts_now(logger, caplog):
    caplog.set_level(logging.INFO)
    timer = ReactionTimer("button", logger)
    timer.begin(None)
    timer.end("volume set")
    assert messages(caplog) == ["button: volume set after 0 ms"]
