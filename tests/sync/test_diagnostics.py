"""Central diagnostics between box and library (docs/developer/library.md, section 4.22): what a box logs reaches the
library, only admins see it, and a box that cannot start says why - through its start script."""

import os
import shutil
import subprocess

import pytest

from tests.support import ApiClient, wait_for
from tests.sync.card import REPOSITORY, TOOLS, Card


def records(bed, **filters):
    return bed.library.diagnostics.query(**filters)


def test_what_a_box_logs_reaches_the_library_and_only_admins_see_it(bed, certificate):
    box = bed.box("box1")
    assert box.synced()
    box_id = box.sync.data.box_id
    box.service._logger.getChild("sync").warning("the album 'x' could not be fetched, token=abc123def")
    box.sync.link.wake()
    assert wait_for(lambda: records(bed, box=box_id, text="could not be fetched"))
    record = records(bed, box=box_id, text="could not be fetched")[-1]
    assert record["component"] == "sync"
    assert record["message"] == "the album 'x' could not be fetched, token=<secret>"
    assert record["version"] == box.sync.version
    admin = bed.admin()
    answer = admin.get(f"/api/library/diagnostics?box={box_id}&level=WARNING&text=fetched").json
    assert [entry["message"] for entry in answer["records"]] == [record["message"]]
    assert "sync" in answer["choices"]["component"]
    exported = admin.get(f"/api/library/diagnostics/export?box={box_id}&text=fetched")
    assert exported.status == 200
    assert bed.admin().post("/api/users", {"username": "anna", "password": "anna-password"}).status == 201
    user = ApiClient(bed.library.api.port, certificate[0]).login("anna", "anna-password")
    assert user.get(f"/api/library/diagnostics?box={box_id}").status == 403


def test_an_error_goes_to_the_library_at_once(bed):
    box = bed.box("box1")
    assert box.synced()
    box.service._logger.error("something broke")  # wakes the synchronization: no waiting for its next pass
    assert wait_for(lambda: records(bed, text="something broke", level="ERROR"), timeout=5)


def test_a_box_that_cannot_start_shows_why_until_it_runs_again(bed):
    box = bed.box("box1")
    assert box.synced()
    box_id = box.sync.data.box_id
    report = {"version": "0.40.0", "slot": "a", "failures": 3, "since": "2026-10-19T10:00:00Z",
              "error": "Traceback (most recent call last):\nValueError: targets.json"}  # fmt: skip
    box.sync.link.client.put("/api/sync/startup", report)  # what the start script sends
    startup = bed.admin().get("/api/library/overview").json["boxes"][0]["startup"]
    assert (startup["failures"], startup["slot"]) == (3, "a")
    box.stop()
    box.start()  # its application runs and reports: the failure becomes history
    assert box.synced()
    assert wait_for(lambda: bed.admin().get("/api/library/overview").json["boxes"][0]["startup"] is None)
    history = records(bed, box=box_id, code="startup.failed")
    assert history[0]["message"].endswith("ValueError: targets.json")


@pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in (*TOOLS, "curl", "openssl")), reason="the slot scripts need their tools"
)
def test_the_start_script_reports_a_box_that_cannot_start(bed, tmp_path, monkeypatch):
    """The application cannot start: it leaves its traceback with the slot script, the start script counts the
    failed starts and sends the report before the next start - also when the application never gets as far as the
    library."""
    card = Card(tmp_path / "card", monkeypatch)
    box = bed.box("box1", identity=card.base / "identity")  # the box and its slot script share the identity
    assert box.synced()
    box_id = box.sync.data.box_id
    box.stop()
    card.slot("a", "0.40.0", "confirmed")
    card.activate("a")
    program = card.base / "slots" / "a" / "bin" / "tinyjukebox"
    program.write_text('#!/bin/sh\ntinyjukebox-slot _error "Traceback\nValueError: targets.json"\nexit 1\n')
    start = [str(REPOSITORY / "deploy" / "slot" / "tinyjukebox-start")]
    environment = {**os.environ, "ROOT": str(card.root)}
    for _ in range(2):
        started = subprocess.run(start, env=environment, capture_output=True, text=True, timeout=60, check=False)
        assert started.returncode == 1, started.stdout + started.stderr
    assert card.state("failures").split()[0] == "2"
    startup = bed.library.diagnostics.startups()[box_id]
    assert startup["failures"] == 1  # sent before the second start: the first one had failed
    assert startup["error"].endswith("ValueError: targets.json")
    assert (startup["version"], startup["slot"]) == ("0.40.0", "a")
    # the application runs again: stage 1 of the health check clears the count
    subprocess.run([str(card.root / "bin" / "tinyjukebox-slot"), "_passed", "1"], check=True)
    assert card.state("failures") == ""
    assert card.state("error") == ""
