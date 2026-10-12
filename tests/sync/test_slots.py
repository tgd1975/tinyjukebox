"""The application and the slot scripts (docs/developer/library.md, section 6): the health check of a new
version, what the box reports to the library, the commands of the tab "System". The scripts themselves - installing,
switching, going back, power cuts - were proven in the test bed; here they run on a folder that stands for a box."""

import os
import shutil
import time
from pathlib import Path

import pytest

from tests.support import TAG_ALBUM, wait_for
from tests.sync.card import REPOSITORY, TOOLS, Card
from tinyjukebox import __version__
from tinyjukebox.core import unexpected
from tinyjukebox.slots import DEFECT, Health, Slots

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in TOOLS), reason=f"the slot scripts need {', '.join(TOOLS)}"
)


@pytest.fixture
def card(tmp_path, monkeypatch) -> Card:
    """A box that runs this version on probation in slot b; the version before is confirmed in slot a."""
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("a", "0.0.1", "confirmed")
    card.slot("b", __version__, "probation")
    card.activate("b")
    return card


def test_without_slots_nothing_happens(logger, monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", str(tmp_path))  # no tinyjukebox-slot: the PC, a box that was not set up with slots
    slots = Slots(logger)
    assert not slots.available
    assert slots.status() == {}
    health = Health(slots, logger, stage1_seconds=0.01)
    health.start(list)
    health.played("album")
    unexpected.report(RuntimeError("no slots"), "test")  # only noted
    health.stop()
    assert not health.probation


def test_a_new_version_passes_both_stages_and_is_confirmed(card, logger):
    slots = Slots(logger)
    assert slots.available
    status = slots.status()
    assert status["active"] == "b"
    assert status["slots"]["b"] == {"version": __version__, "state": "probation", "since": "2026-10-06T10:00:00Z",
                                    "starts": 1}  # fmt: skip
    journal: list[str] = []
    health = Health(slots, logger, 0.05, 0.05, lambda event, **fields: journal.append(event))
    assert health.probation
    health.start(list)
    assert wait_for((card.base / "state" / "stage1").exists)  # the start script waits for this
    assert slots.status()["slots"]["b"]["state"] == "probation"  # ... but only a tag that plays confirms
    health.played("radio")  # a station never confirms a version
    time.sleep(0.2)
    assert slots.status()["slots"]["b"]["state"] == "probation"
    health.played("album")
    assert wait_for(lambda: slots.status()["slots"]["b"]["state"] == "confirmed")
    assert wait_for(lambda: journal == ["confirmed"])
    assert slots.status()["history"][-1]["why"] == "confirmed"
    health.stop()


def test_stage_1_is_not_passed_while_something_is_missing(card, logger):
    slots = Slots(logger)
    health = Health(slots, logger, 0.05, 0.05)
    health.start(lambda: ["rfid"])
    time.sleep(0.3)
    assert not (card.base / "state" / "stage1").exists()  # the start script ends the version at its time limit
    health.stop()


def test_an_unexpected_exception_ends_a_version_on_probation(card, logger):
    ended: list[int] = []
    slots = Slots(logger)
    health = Health(slots, logger, 5, 5, end=ended.append)
    health.start(list)
    unexpected.report(RuntimeError("boom"), "thread test")
    assert ended == [DEFECT]
    assert "unexpected exception in thread test: RuntimeError('boom')" in card.state("failure")
    health.stop()


def test_after_the_confirmation_an_unexpected_exception_is_only_reported(tmp_path, monkeypatch, logger):
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("a", __version__, "confirmed")
    card.activate("a")
    ended: list[int] = []
    journal: list[str] = []
    told: list[bool] = []
    health = Health(Slots(logger), logger, 5, 5, lambda event, **fields: journal.append(event), ended.append)
    health.on_change = lambda: told.append(True)
    health.start(list)
    unexpected.report(RuntimeError("boom"), "thread test")
    assert ended == []
    assert journal == ["unexpected"]
    assert told == [True]
    health.stop()


# --- the first version is the fallback version -----------------------------------------------------------------------


def install(logger, file: Path) -> dict:
    """Install from a file, as the playbook does it for the first version of a box; returns the status."""
    slots = Slots(logger)
    result = slots.run("update", str(file), "--yes", "--by", "playbook")
    assert result.returncode == 0, result.stdout + result.stderr
    return slots.status()


def test_the_first_version_of_a_box_is_its_fallback_version(empty_card, archive, logger):
    card = empty_card
    status = install(logger, archive("1.0.0"))
    assert status["slots"]["next"]["version"] == "1.0.0"  # waits for the first start
    assert status["slots"]["fallback"] == {"version": "1.0.0", "state": "confirmed", "since": "-", "starts": 0}
    assert (card.base / "slots" / "fallback" / "bin" / "tinyjukebox").exists()
    assert (card.base / "slots" / "fallback" / "VERSION").read_text() == "1.0.0\n"
    assert status["history"][-1]["why"] == "the first version is the fallback version"
    assert status["history"][-1]["by"] == "playbook"
    # installing again before the first start - the playbook ran twice - leaves the fallback version as it is
    status = install(logger, archive("1.0.1"))
    assert status["slots"]["next"]["version"] == "1.0.1"
    assert status["slots"]["fallback"]["version"] == "1.0.0"


def test_a_first_version_that_fails_is_not_left_for_itself(empty_card, archive, logger):
    import subprocess

    card = empty_card
    install(logger, archive("1.0.0", program="exit 3"))
    # the first start, as the service unit runs it: the version crashes, and the fallback version is the same one
    started = subprocess.run(
        [str(REPOSITORY / "deploy" / "slot" / "tinyjukebox-start")],
        env={**os.environ, "ROOT": str(card.root)}, capture_output=True, text=True, check=False, timeout=30,
    )  # fmt: skip
    assert started.returncode == 3, started.stdout + started.stderr
    status = Slots(logger).status()
    assert status["active"] == "a"  # a version that may play is better than a box that does not start
    assert status["slots"]["a"]["state"] == "probation"
    assert "no other version can be started" in status["problem"]
    assert status["versions"] == []  # the box did not go back from it: it is not manual


def test_an_update_never_touches_the_fallback_version(empty_card, archive, logger):
    card = empty_card
    card.slot("a", "0.9.0", "confirmed")
    card.slot("fallback", "0.8.0", "confirmed")
    card.activate("a")
    status = install(logger, archive("1.0.0"))
    assert status["slots"]["next"]["version"] == "1.0.0"
    assert status["slots"]["fallback"]["version"] == "0.8.0"
    assert (card.base / "slots" / "fallback" / "VERSION").read_text() == "0.8.0\n"


def test_a_box_that_runs_a_version_gets_no_fallback_version_from_an_update(empty_card, archive, logger):
    # a box of today with an empty fallback slot: a new version is not its first one, and nothing proved it yet
    card = empty_card
    card.slot("a", "0.9.0", "confirmed")
    card.activate("a")
    status = install(logger, archive("1.0.0"))
    assert status["slots"]["next"]["version"] == "1.0.0"
    assert "fallback" not in status["slots"]
    assert not (card.base / "slots" / "fallback").exists()


# --- with the library ------------------------------------------------------------------------------------------------


def version_in_the_archive(bed, version, tmp_path):
    """A version of the box software in the archive of the library, as a release would put it there."""
    files = {}
    for kind, name in (("description", "slot.json"), ("archive", "slot.tar.gz"), ("signature", "slot.tar.gz.sig")):
        files[kind] = str(tmp_path / f"{version}-{name}")
        Path(files[kind]).write_text("{}")
    description = {"version": version, "interface": 1, "platform": "x86_64-debian-trixie", "size": 2, "sha256": "-",
                   "system": {"setup": 1, "slot-script": 1}}  # fmt: skip
    bed.library.store.add_version(description, files, "release")


def test_the_box_reports_what_runs_and_the_library_shows_it(bed, card, tmp_path):
    version_in_the_archive(bed, __version__, tmp_path)
    box = bed.box("box1")
    assert box.synced()
    overview = bed.admin().get("/api/library/overview").json
    status = overview["boxes"][0]["status"]
    assert (status["version"], status["slot"], status["mode"]) == (__version__, "b", "automatic")
    assert status["slots"]["b"]["state"] == "probation"  # on probation: since when and for how many starts
    assert overview["versions"][0]["boxes"] == [box.sync.data.box_id]
    # the update mode is changed on the page of the box: the library hears of it at once
    result = box.control.system_command("mode", ["manual"], "web page, user anna")
    assert result["status"] == 0
    assert wait_for(lambda: bed.library.store.boxes()[0]["status"].get("mode") == "manual")
    assert box.control.system_info()["mode"] == "manual"


def test_the_library_shows_a_version_that_waits_for_the_next_start_of_a_box(bed, card, tmp_path):
    version_in_the_archive(bed, __version__, tmp_path)
    version_in_the_archive(bed, "99.0.0", tmp_path)
    card.slot("next", "99.0.0", "auto")  # fetched by the box, active from its next start
    box = bed.box("box1")
    assert box.synced()
    versions = {entry["version"]: entry for entry in bed.admin().get("/api/library/overview").json["versions"]}
    assert (versions["99.0.0"]["boxes"], versions["99.0.0"]["prepared"]) == ([], [box.sync.data.box_id])
    assert (versions[__version__]["boxes"], versions[__version__]["prepared"]) == ([box.sync.data.box_id], [])


def test_r1_a_revert_on_the_box_makes_the_version_manual_for_all_boxes(bed, card, tmp_path):
    version_in_the_archive(bed, __version__, tmp_path)
    box = bed.box("box1")
    assert box.synced()
    result = box.control.system_command("revert", [], "web page, user anna")
    assert result["status"] == 0, result["output"]
    assert "revert: b:" in result["output"]  # (on a real box the service restarts at once)
    assert os.readlink(card.base / "current") == "slots/a"
    # the box went back from the version: no box installs it by itself any more
    assert wait_for(lambda: bed.library.store.versions()[__version__]["state"] == "manual")
    assert "box" in bed.library.store.versions()[__version__]["by"]
    assert wait_for(lambda: f"{__version__} manual" in card.state("versions") and " box" not in card.state("versions"))
    switches = bed.library.store.journal.records("switch")
    assert switches[-1]["why"] == "revert"
    assert switches[-1]["by"] == "web page, user anna"


def test_the_box_plays_and_confirms_its_version(bed, card):
    box = bed.box("box1")
    box.service._health._stage2_seconds = 0.1
    assert box.synced()
    box.place(TAG_ALBUM)
    assert wait_for(lambda: box.state == "play_album")
    assert wait_for(lambda: box.control.system_info()["slots"]["b"]["state"] == "confirmed")
    assert wait_for(
        lambda: bed.library.store.boxes()[0]["status"].get("slots", {}).get("b", {}).get("state") == "confirmed"
    )


# --- the whole chain with a real slot archive ------------------------------------------------------------------------


@pytest.fixture(scope="session")
def release(tmp_path_factory):
    """A signed slot archive of this version for this computer, as the pipeline builds it for the boxes: description,
    archive, signature, and the public key of the releases. Skipped if it cannot be built here (no network yet)."""
    import subprocess

    folder = tmp_path_factory.mktemp("release")
    key = folder / "release.key"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(key)],
        check=True, capture_output=True,
    )  # fmt: skip
    subprocess.run(
        ["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(folder / "release.pub")],
        check=True, capture_output=True,
    )  # fmt: skip
    built = subprocess.run(
        [str(REPOSITORY / "scripts" / "build_slot.sh"), "--out", str(folder), "--key", str(key), "--no-rfid-reader"],
        capture_output=True, text=True, check=False, timeout=600,
    )  # fmt: skip
    if built.returncode != 0:
        pytest.skip(f"the slot cannot be built here: {built.stderr[-300:]}")
    archive = Path(built.stdout.strip().splitlines()[-1])
    base = str(archive).removesuffix(".tar.gz")
    return {"description": f"{base}.json", "archive": str(archive), "signature": f"{archive}.sig",
            "key": str(folder / "release.pub")}  # fmt: skip


@pytest.mark.timeout(120)
def test_u1_the_box_fetches_a_new_version_from_the_library_and_installs_it(bed, tmp_path, monkeypatch, release):
    import json

    if shutil.which("curl") is None:
        pytest.skip("the slot script fetches with curl")
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("a", "0.0.1", "confirmed")
    card.activate("a")
    shutil.copy(release["key"], card.base / "release.pub")
    with open(release["description"], encoding="utf-8") as infile:
        description = json.load(infile)
    (card.base / "slot.conf").write_text(f"PLATFORM={description['platform']}\n")
    # the box keeps what identifies it where the slot script finds it
    box = bed.box("box1", identity=card.base / "identity")
    assert box.synced()
    files = {kind: release[kind] for kind in ("description", "archive", "signature")}
    bed.library.store.add_version(description, dict(files), "release")  # the library tells the boxes
    slots = box.service._slots
    assert wait_for(lambda: slots.status().get("slots", {}).get("next", {}).get("version") == __version__, timeout=60)
    assert slots.status()["slots"]["next"]["state"] == "auto"  # installed by the box itself
    assert (card.base / "slots" / "next" / "bin" / "tinyjukebox").exists()
    assert card.state("versions").startswith(f"{__version__} use 1")
    assert slots.status()["problem"] == ""
