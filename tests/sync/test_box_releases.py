"""A box without a library gets its versions from the releases of the git server (docs/developer/library.md,
section 6.16): the slot script asks, the application says when. The git server is a stand-in in this process."""

import shutil
import subprocess
from collections.abc import Iterator

import pytest

from tests.support import wait_for
from tests.sync.card import TOOLS, Card
from tests.sync.gitserver import GitServer
from tinyjukebox.slots import Health, Slots

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in (*TOOLS, "curl")), reason="the slot scripts need their tools and curl"
)


@pytest.fixture
def git() -> Iterator[GitServer]:
    server = GitServer()
    yield server
    server.stop()


@pytest.fixture
def box(empty_card: Card, git: GitServer) -> Card:
    """A box without a library that runs version 0.9.0 and knows where the releases are."""
    card = empty_card
    card.slot("a", "0.9.0", "confirmed")
    card.activate("a")
    with open(card.base / "state" / "slots", "a", encoding="utf-8") as outfile:
        outfile.write("newest 0.9.0\n")
    (card.base / "slot.conf").write_text(f"PLATFORM=test\nRELEASES={git.url}\n")
    return card


def run(logger, *arguments: str) -> subprocess.CompletedProcess[str]:
    return Slots(logger).run(*arguments)


def status(logger) -> dict:
    return Slots(logger).status()


def test_a_release_waits_before_the_box_installs_it_by_itself(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=1, notes="what is new")
    result = run(logger, "update", "--auto")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "waits 3 days" in result.stdout
    assert not git.asked_for(".tar.gz")  # looked at, not downloaded
    state = status(logger)
    assert (state["origin"], state["releases"], state["wait"], state["script"]) == ("releases", git.url, 3, 2)
    assert "next" not in state["slots"]
    offer = state["offer"]
    assert (offer["version"], offer["notes"]) == ("1.0.0", "what is new")
    assert offer["size"] > 0
    assert offer["due"] > offer["published"]  # the day the box installs it by itself
    # "install now" on the page: the same installation, at once
    result = run(logger, "update", "--by", "web page, user anna")
    assert result.returncode == 0, result.stdout + result.stderr
    state = status(logger)
    assert state["slots"]["next"]["version"] == "1.0.0"
    assert state["offer"] is None  # nothing is new any more
    assert (box.base / "slots" / "next" / "bin" / "tinyjukebox").exists()


def test_after_the_waiting_time_the_box_installs_the_release(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=4)
    result = run(logger, "update", "--auto")
    assert result.returncode == 0, result.stdout + result.stderr
    state = status(logger)
    assert state["slots"]["next"] == {"version": "1.0.0", "state": "auto", "since": "-", "starts": 0}
    assert state["offer"] is None
    assert box.state("versions") == "1.0.0 use 0\n"
    # asked again: nothing is fetched a second time
    before = len(git.asked_for(".tar.gz"))
    assert run(logger, "update", "--auto").returncode == 0
    assert len(git.asked_for(".tar.gz")) == before


def test_of_releases_in_a_row_only_the_newest_is_fetched(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=6)
    git.publish(archive("1.0.1"), days_ago=5)
    git.publish(archive("1.0.2"), days_ago=4)
    assert run(logger, "update", "--auto").returncode == 0
    assert status(logger)["slots"]["next"]["version"] == "1.0.2"
    assert [path.rsplit("/", 1)[1] for path in git.asked_for(".tar.gz")] == [
        "tinyjukebox-box-1.0.2-test.tar.gz", "tinyjukebox-box-1.0.2-test.tar.gz.sig",
    ]  # fmt: skip


def test_a_withdrawn_release_is_dropped_before_the_box_switches_to_it(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=9)
    git.publish(archive("1.0.1"), days_ago=4)
    assert run(logger, "update", "--auto").returncode == 0
    assert status(logger)["slots"]["next"]["version"] == "1.0.1"
    git.withdraw("1.0.1")  # marked as a pre-release: 1.0.0 is the newest release again
    result = run(logger, "update", "--auto")
    assert "version 1.0.1 is no longer offered" in result.stdout
    state = status(logger)
    assert state["slots"]["next"]["version"] == "1.0.0"  # what the git server offers now
    assert not git.asked_for("releases/tags")


def test_a_release_with_a_wrong_signature_is_refused_once(box, archive, git, logger):
    file = archive("1.0.0")
    shutil.copy(f"{archive('0.0.1')}.sig", f"{file}.sig")  # the signature of another archive
    git.publish(file, days_ago=4)
    result = run(logger, "update", "--auto")
    assert result.returncode != 0
    state = status(logger)
    assert "next" not in state["slots"]
    assert "the signature does not fit" in state["problem"]
    assert box.state("refused").startswith("1.0.0 ")
    # not fetched again by itself: the box would download the same file with every start
    before = len(git.asked_for(".tar.gz"))
    result = run(logger, "update", "--auto")
    assert "was refused before" in result.stdout
    assert len(git.asked_for(".tar.gz")) == before


def test_an_interrupted_download_continues(box, archive, git, logger):
    file = archive("1.0.0")
    git.publish(file, days_ago=4)
    git.cut[file.name] = 100
    result = run(logger, "update", "--auto")
    assert result.returncode != 0
    assert "the download was interrupted" in result.stderr
    part = box.base / "slots" / ".download" / "tinyjukebox-box-1.0.0.tar.gz.part"
    assert part.stat().st_size == 100
    assert run(logger, "update", "--auto").returncode == 0
    assert status(logger)["slots"]["next"]["version"] == "1.0.0"


def test_mode_off_asks_nobody_and_mode_manual_downloads_nothing(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=4)
    assert run(logger, "mode", "off").returncode == 0
    result = run(logger, "update", "--auto")
    assert (result.returncode, git.requests) == (0, [])
    assert status(logger)["offer"] is None
    assert run(logger, "mode", "manual").returncode == 0
    result = run(logger, "update", "--auto")
    assert result.returncode == 0
    assert git.requests  # it looked: the release and its description
    assert not git.asked_for(".tar.gz")
    state = status(logger)
    assert "next" not in state["slots"]
    assert (state["offer"]["version"], state["offer"]["due"]) == ("1.0.0", "")  # waits for a person, not for a day
    # by hand an update is possible in every mode
    assert run(logger, "mode", "off").returncode == 0
    assert run(logger, "update").returncode == 0
    assert status(logger)["slots"]["next"]["version"] == "1.0.0"
    assert run(logger, "mode", "sometimes").returncode != 0


def test_a_box_that_asks_shows_the_release_until_somebody_answers(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=1, notes="- a fix")
    assert run(logger, "mode", "manual").returncode == 0
    assert run(logger, "update", "--auto").returncode == 0
    offer = status(logger)["offer"]
    assert (offer["version"], offer["notes"], offer["due"]) == (
        "1.0.0",
        "- a fix",
        "",
    )  # no waiting time: a person decides
    # "skip": the box does not ask for this release any more ...
    result = run(logger, "skip", "1.0.0", "--by", "web page, user anna")
    assert result.returncode == 0, result.stdout + result.stderr
    assert status(logger)["offer"] is None
    assert run(logger, "update", "--auto").returncode == 0
    assert status(logger)["offer"] is None
    assert not git.asked_for(".tar.gz")
    # ... but for the next one
    git.publish(archive("1.0.1"))
    assert run(logger, "update", "--auto").returncode == 0
    assert status(logger)["offer"]["version"] == "1.0.1"
    assert not git.asked_for(".tar.gz")  # of all these releases nothing was downloaded
    # "install": the newest one, in the background, active with the next start
    assert run(logger, "update").returncode == 0
    state = status(logger)
    assert (state["slots"]["next"]["version"], state["offer"]) == ("1.0.1", None)
    assert run(logger, "skip").returncode != 0


def test_a_skipped_release_can_still_be_installed_by_hand(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=1)
    assert run(logger, "mode", "manual").returncode == 0
    assert run(logger, "update", "--auto").returncode == 0
    assert run(logger, "skip", "1.0.0").returncode == 0
    assert run(logger, "update").returncode == 0
    assert status(logger)["slots"]["next"]["version"] == "1.0.0"
    # skipping is an answer to a question: a box that installs by itself does not know it
    (box.base / "state" / "skipped").write_text("1.0.1\n")
    git.publish(archive("1.0.1"), days_ago=1)
    assert run(logger, "mode", "automatic").returncode == 0
    assert run(logger, "update", "--auto").returncode == 0
    assert status(logger)["offer"]["version"] == "1.0.1"  # it waits for its day


def test_a_box_that_is_linked_to_a_library_never_asks_the_git_server(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=4)
    (box.base / "identity").mkdir()
    (box.base / "identity" / "library").write_text("https://127.0.0.1:1\n")  # nothing listens there
    result = run(logger, "update", "--auto")
    assert (result.returncode, git.requests) == (0, [])
    assert "the library cannot be reached" in result.stdout
    assert status(logger)["origin"] == "library"
    assert run(logger, "update").returncode != 0  # not by hand either
    assert git.requests == []


def test_without_an_answer_the_application_is_told_to_ask_again(box, git, logger):
    git.stop()
    result = run(logger, "update", "--auto")
    assert result.returncode == 69
    assert "the releases cannot be reached" in result.stdout
    assert status(logger)["problem"] == ""  # shown nowhere


def test_a_certain_version_is_fetched_by_its_tag(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=9)
    git.publish(archive("1.0.1"), days_ago=4)
    result = run(logger, "update", "1.0.0")
    assert result.returncode == 0, result.stdout + result.stderr
    assert git.asked_for("releases/tags/v1.0.0")
    assert status(logger)["slots"]["next"]["version"] == "1.0.0"
    assert run(logger, "update", "7.7.7").returncode != 0


def test_a_private_repository_needs_the_token_of_the_box(empty_card, archive, logger):
    git = GitServer(token="read-only-token")
    try:
        card = empty_card
        (card.base / "slot.conf").write_text(f"PLATFORM=test\nRELEASES={git.url}\nWAIT_DAYS=0\n")
        git.publish(archive("1.0.0"))
        result = run(logger, "update", "--auto")  # for a box without the token there is no repository
        assert (result.returncode, "has no release for this box" in result.stdout) == (0, True)
        (card.base / "identity").mkdir()
        (card.base / "identity" / "releases-token").write_text("read-only-token\n")
        result = run(logger, "update", "--auto")
        assert result.returncode == 0, result.stdout + result.stderr
        # the files come through the interface, where the token counts
        assert git.asked_for("releases/assets/tinyjukebox-box-1.0.0-test.tar.gz")
        assert not git.asked_for("/download/")
        # the first version of a box, fetched by the box itself: on trial from the first start, and its fallback
        state = status(logger)
        assert state["slots"]["next"]["version"] == "1.0.0"
        assert state["slots"]["fallback"]["version"] == "1.0.0"
    finally:
        git.stop()


def test_a_release_without_a_slot_for_the_box_offers_nothing(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=4)
    (box.base / "slot.conf").write_text(f"PLATFORM=another\nRELEASES={git.url}\n")
    result = run(logger, "update", "--auto")
    assert (result.returncode, "no newer version" in result.stdout) == (0, True)
    assert status(logger)["offer"] is None


# --- the application says when ---------------------------------------------------------------------------------------


def test_a_box_without_a_library_looks_by_itself(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=4)
    health = Health(Slots(logger), logger, 5, 5, look=(0.05, 30, 30), alone=lambda: False)
    health.start(list)
    try:
        assert not wait_for(lambda: git.requests, timeout=0.5)  # a box with a library waits for its library
    finally:
        health.stop()
    health = Health(Slots(logger), logger, 5, 5, look=(0.05, 30, 30), alone=lambda: True)
    health.start(list)
    try:
        assert wait_for(lambda: status(logger)["slots"].get("next", {}).get("version") == "1.0.0", timeout=10)
        assert len(git.asked_for("releases/latest")) == 1  # once a day is enough
    finally:
        health.stop()


def test_the_application_asks_again_soon_if_nobody_answered(box, git, logger):
    git.stop()  # no WLAN yet
    slots = Slots(logger)
    asked = []
    run_script = slots.run

    def counted(*arguments: str, timeout: float | None = None) -> subprocess.CompletedProcess[str]:
        result = run_script(*arguments, timeout=timeout)
        if arguments == ("update", "--auto"):
            asked.append(result.returncode)
        return result

    slots.run = counted  # type: ignore[method-assign]
    health = Health(slots, logger, 5, 5, look=(0.05, 30, 0.1), alone=lambda: True)
    health.start(list)
    try:
        assert wait_for(lambda: asked[:3] == [69, 69, 69], timeout=10)
    finally:
        health.stop()
