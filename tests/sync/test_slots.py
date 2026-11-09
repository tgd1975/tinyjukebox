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


def test_a_version_on_probation_ends_also_when_the_journal_cannot_be_written(card, logger):
    """The card is full or read-only: noting the exception fails, the version ends all the same."""
    ended: list[int] = []

    def journal(event, **fields):
        raise OSError(30, "Read-only file system")

    health = Health(Slots(logger), logger, 5, 5, journal, ended.append)
    health.start(list)
    unexpected.report(RuntimeError("boom"), "thread test")
    assert ended == [DEFECT]
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


def test_the_time_for_stage_1_is_counted_on_a_clock_that_does_not_jump(card, tmp_path):
    """The Pi has no real-time clock: after it was switched on, the clock of the system jumps forward by the hours it
    was off as soon as it is set from the network. That must not end a new version that has just started - the time
    limit of stage 1 counts the seconds since the boot."""
    import subprocess

    (card.base / "slot.conf").write_text("STAGE1_LIMIT=2\n")
    uptime = tmp_path / "uptime"
    uptime.write_text("100.25 400.00\n")
    started = subprocess.Popen(
        [str(REPOSITORY / "deploy" / "slot" / "tinyjukebox-start")],
        env={**os.environ, "ROOT": str(card.root), "UPTIME_FILE": str(uptime)},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )  # fmt: skip
    try:
        time.sleep(3)  # more than the limit passes on the clock of the system, none since the boot
        assert started.poll() is None
        assert os.readlink(card.base / "current") == "slots/b"
        uptime.write_text("102.80 410.00\n")  # now the limit has passed
        output, _ = started.communicate(timeout=10)
    finally:
        if started.poll() is None:
            started.kill()
    assert started.returncode == 1, output
    assert "stage 1 of the health check not passed within 2 s" in output
    assert os.readlink(card.base / "current") == "slots/a"


# --- the start script and its notes ----------------------------------------------------------------------------------


START = str(REPOSITORY / "deploy" / "slot" / "tinyjukebox-start")


def start_until(card: Card, done, timeout: float = 15) -> str:
    """The start script as the service unit runs it, until ``done()`` holds; returns what it printed."""
    import subprocess

    started = subprocess.Popen(
        [START], env={**os.environ, "ROOT": str(card.root)}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    try:
        reached = wait_for(done, timeout=timeout)
    finally:
        started.terminate()
        output, _ = started.communicate(timeout=10)
    assert reached, output
    return output


def history(card: Card) -> list[list[str]]:
    return [line.split("\t") for line in card.state("history.log").splitlines()]


@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        ({"a": ("0.9.0", "confirmed"), "b": ("1.0.0", "probation")}, "a"),
        ({"a": ("0.10.0", "confirmed"), "b": ("0.9.0", "confirmed")}, "a"),  # the newer one: 0.10 after 0.9
        ({"a": ("0.9.0", "confirmed"), "b": ("1.0.0", "confirmed")}, "b"),
        ({"a": ("0.9.0", "abandoned"), "b": ("1.0.0", "probation")}, "fallback"),
    ],
)
def test_r7_a_link_that_points_to_nothing_starts_the_newest_confirmed_version(tmp_path, monkeypatch, slots, expected):
    """A typing error by hand: the box plays the confirmed version it has - recovery mode only without one."""
    card = Card(tmp_path / "card", monkeypatch)
    (card.base / "slot.conf").write_text("RECOVERY_WAIT=1\n")  # recovery mode leaves no long wait behind
    card.slot("fallback", "0.8.0", "confirmed")
    for name, (version, state) in slots.items():
        card.slot(name, version, state)
    (card.base / "current").symlink_to("slots/c")
    (card.base / "state" / "active").write_text("b\n")
    start_until(card, lambda: os.readlink(card.base / "current") == f"slots/{expected}")
    version = (card.base / "slots" / expected / "VERSION").read_text().strip()
    assert history(card)[-1][1:4] == ["c:", f"{expected}:{version}", "the link current pointed to nothing"]


def test_an_empty_note_of_a_trigger_file_is_left_out_and_the_file_obeyed(card):
    """An older slot script wrote its notes in place: a power cut could leave them empty. Such a note is no note."""
    boot = card.root / "boot" / "firmware"
    boot.mkdir(parents=True)
    (boot / "tinyjukebox-revert").write_text("")
    (card.base / "state" / "trigger").write_text("")
    sudo = card.root / "bin" / "sudo"  # the rule of the installer for the boot partition, as root of this card
    sudo.write_text('#!/bin/sh\nexec "$@"\n')
    sudo.chmod(0o755)
    output = start_until(card, lambda: os.readlink(card.base / "current") == "slots/a")
    assert "state/trigger is damaged" in output
    assert not (boot / "tinyjukebox-revert").exists()
    assert "revert: switched from slot b to slot a" in (boot / "tinyjukebox-ergebnis.txt").read_text()
    assert not (card.base / "state" / "trigger").exists()


def test_an_empty_note_of_a_move_is_left_out_and_the_version_moved(card):
    card.slot("next", "9.9.9", "auto")  # installed, waits for the start
    (card.base / "state" / "switch").write_text("")
    output = start_until(card, lambda: "b 9.9.9 probation" in card.state("slots"))
    assert "state/switch is damaged" in output
    assert os.readlink(card.base / "current") == "slots/b"  # into the slot without the confirmed version
    assert (card.base / "slots" / "b" / "VERSION").read_text() == "9.9.9\n"
    assert (card.base / "slots" / "a" / "VERSION").read_text() == "0.0.1\n"
    assert not (card.base / "state" / "switch").exists()


def test_damaged_notes_of_the_mode_and_the_active_slot_count_as_none(card, logger):
    for text, mode in (("", "automatic"), ("autom", "automatic"), ("manual\n", "manual"), ("off\n", "off")):
        (card.base / "state" / "update").write_text(text)
        assert Slots(logger).status()["mode"] == mode
    (card.base / "state" / "active").write_text("")
    start_until(card, lambda: card.state("active") == "b\n")
    assert "by hand" not in card.state("history.log")  # no switch by hand is made up


def test_the_scripts_do_not_run_as_root(tmp_path):
    """Root would own every file the scripts write, and the service could not replace them any more."""
    import subprocess

    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "id").write_text('#!/bin/sh\n[ "$1" = -u ] && { echo 0; exit 0; }\nexec /usr/bin/id "$@"\n')
    (fake / "id").chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if key != "ROOT"}
    environment["PATH"] = f"{fake}{os.pathsep}{environment['PATH']}"
    for script in ("tinyjukebox-slot", "tinyjukebox-start"):
        done = subprocess.run(
            [str(REPOSITORY / "deploy" / "slot" / script), "status"], env=environment, capture_output=True, text=True,
            check=False, timeout=10,
        )  # fmt: skip
        assert done.returncode == 1
        assert "not as root" in done.stderr


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


@pytest.mark.parametrize(
    ("slots", "expected"),
    [
        ({"a": ("0.48.1", "probation"), "b": ("0.44.0", "confirmed")}, "0.44.0"),  # the one on trial runs
        ({"a": ("0.10.0", "confirmed"), "b": ("0.9.0", "confirmed")}, "0.10.0"),  # the newer one: 0.10 after 0.9
        ({"a": ("0.9.0", "abandoned"), "b": ("1.0.0", "confirmed")}, "1.0.0"),
    ],
)
def test_the_newest_confirmed_version_becomes_the_fallback_version(tmp_path, monkeypatch, logger, slots, expected):
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("fallback", "0.30.0", "confirmed")
    for name, (version, state) in slots.items():
        card.slot(name, version, state)
    card.activate("a")
    result = Slots(logger).command("promote", [], "test")
    assert result["status"] == 0, result["output"]
    assert Slots(logger).status()["slots"]["fallback"]["version"] == expected
    assert (card.base / "slots" / "fallback" / "VERSION").read_text() == expected + "\n"
    assert history(card)[-1][3] == "promoted to the fallback version"
    again = Slots(logger).command("promote", [], "test")  # the same one again: nothing to copy
    assert again["status"] == 0
    assert "already" in again["output"]


def test_a_button_makes_the_confirmed_version_of_its_slot_the_fallback_version(tmp_path, monkeypatch, logger):
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("fallback", "0.30.0", "confirmed")
    card.slot("a", "0.48.5", "confirmed")
    card.slot("b", "0.44.0", "confirmed")
    card.activate("a")
    result = Slots(logger).command("promote", ["b"], "test")  # the older one, though the newer one runs
    assert result["status"] == 0, result["output"]
    assert (card.base / "slots" / "fallback" / "VERSION").read_text() == "0.44.0\n"
    assert history(card)[-1][1:3] == ["b:0.44.0", "fallback:0.44.0"]
    result = Slots(logger).command("promote", ["a"], "test")
    assert result["status"] == 0, result["output"]
    assert Slots(logger).status()["slots"]["fallback"]["version"] == "0.48.5"


def test_a_version_on_probation_does_not_become_the_fallback_version(tmp_path, monkeypatch, logger):
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("fallback", "0.30.0", "confirmed")
    card.slot("a", "0.48.5", "probation")
    card.activate("a")
    result = Slots(logger).command("promote", ["a"], "test")
    assert result["status"] != 0
    assert "no confirmed version" in result["output"]
    result = Slots(logger).command("promote", ["b"], "test")  # an empty slot
    assert result["status"] != 0
    assert "holds no version" in result["output"]
    assert (card.base / "slots" / "fallback" / "VERSION").read_text() == "0.30.0\n"


def test_without_a_confirmed_version_and_while_it_runs_the_fallback_version_stays(tmp_path, monkeypatch, logger):
    card = Card(tmp_path / "card", monkeypatch)
    card.slot("fallback", "0.30.0", "confirmed")
    card.slot("a", "0.48.1", "probation")
    card.activate("a")
    result = Slots(logger).command("promote", [], "test")
    assert result["status"] != 0
    assert "confirmed" in result["output"]
    card.slot("b", "0.44.0", "confirmed")
    card.activate("fallback")
    result = Slots(logger).command("promote", [], "test")
    assert result["status"] != 0
    assert "runs" in result["output"]
    assert (card.base / "slots" / "fallback" / "VERSION").read_text() == "0.30.0\n"


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


def test_the_library_learns_why_a_box_went_back_from_a_version(bed, card, tmp_path, caplog):
    """A version that failed its health check: the reason is on the card of the box, in its list of switches. It
    reaches the library with the version the box went back from - also when the list began anew on a card that was
    set up again, while the box and the library still count the switches of the card before."""
    version_in_the_archive(bed, "9.9.9", tmp_path)
    box = bed.box("box1")
    assert box.synced()
    box_id = box.sync.data.box_id
    # what the start script left: the version went back to the one before, the reason in the list of switches
    reason = "stage 1 of the health check not passed within 120 s"
    (card.base / "state" / "history.log").write_text(
        f"2026-10-13T22:55:00Z\tb:9.9.9\ta:0.0.1\thealth check failed\tstart script\t{reason}\n"
    )
    (card.base / "state" / "versions").write_text("9.9.9 manual 1 box\n")
    # ... and both still count seven switches of the card before
    box.sync.data.history_reported(7)
    with bed.library.store.lock:
        bed.library.store.box(box_id)["history"] = 7
    box.sync.link._reported = None
    box.sync.tell()
    assert wait_for(lambda: bed.library.store.versions()["9.9.9"]["state"] == "manual", timeout=10)
    entry = bed.library.store.versions()["9.9.9"]
    assert entry["why"] == f"health check failed: {reason}"
    assert box_id in entry["by"]
    assert f"health check failed: {reason}" in caplog.text  # the log of the library says why
    switches = bed.library.store.journal.records("switch")
    assert [(line["from"], line["why"], line["details"]) for line in switches[-1:]] == [
        ("b:9.9.9", "health check failed", reason)
    ]
    assert wait_for(lambda: box.sync.data.numbers["history"] == 1, timeout=10)  # counted anew


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
    arguments = card.watch_curl()
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
    # the slot of the pipeline brings the signature of its list of checksums, which the list does not name
    assert (card.base / "slots" / "next" / "SHA256SUMS.sig").exists()
    assert "SHA256SUMS.sig" not in (card.base / "slots" / "next" / "SHA256SUMS").read_text()
    assert card.state("versions").startswith(f"{__version__} use 1")
    assert slots.status()["problem"] == ""
    # the key of the box was never an argument of curl, which anybody can read in the list of processes
    assert "/api/sync/versions" in arguments.read_text()
    assert (card.base / "identity" / "key").read_text().strip() not in arguments.read_text()
