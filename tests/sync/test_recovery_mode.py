"""Recovery mode (docs/developer/library.md, section 6.10): the fallback slot starts no application. The start script
installs a version for slot a or b and restarts into it - or waits for an administrator, without a restart loop."""

import os
import shutil
import subprocess
from collections.abc import Iterator

import pytest

from tests.support import wait_for
from tests.sync.card import REPOSITORY, TOOLS, Card
from tests.sync.gitserver import GitServer
from tinyjukebox.slots import Slots

pytestmark = pytest.mark.skipif(
    any(shutil.which(tool) is None for tool in (*TOOLS, "curl")), reason="the slot scripts need their tools and curl"
)

START = str(REPOSITORY / "deploy" / "slot" / "tinyjukebox-start")


@pytest.fixture
def git() -> Iterator[GitServer]:
    server = GitServer()
    yield server
    server.stop()


@pytest.fixture
def box(empty_card: Card, git: GitServer) -> Card:
    """A box without a library whose versions in a and b both failed: the fallback slot is active."""
    card = empty_card
    card.slot("a", "0.9.0", "abandoned")
    card.slot("b", "0.9.1", "abandoned")
    card.slot("fallback", "0.8.0", "confirmed")
    # the application of the fallback version would leave a mark: recovery mode must never start it
    program = card.base / "slots" / "fallback" / "bin" / "tinyjukebox"
    program.write_text(f"#!/bin/sh\ntouch {card.base}/fallback-started\nexec sleep infinity\n")
    card.activate("fallback")
    (card.base / "state" / "versions").write_text("0.9.0 manual 0 box\n0.9.1 manual 0 box\n")
    with open(card.base / "state" / "slots", "a", encoding="utf-8") as outfile:
        outfile.write("newest 0.9.1\n")
    (card.base / "slot.conf").write_text(f"PLATFORM=test\nRELEASES={git.url}\nRECOVERY_WAIT=1\n")
    return card


def start(card: Card) -> subprocess.Popen[str]:
    """The start script as the service unit runs it."""
    return subprocess.Popen(
        [START], env={**os.environ, "ROOT": str(card.root)}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


def test_recovery_mode_installs_a_version_and_starts_it(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=0)  # a box that cannot play does not wait for a release
    started = start(box)
    output, _ = started.communicate(timeout=30)
    assert started.returncode == 75, output  # the unit restarts the service
    assert "recovery mode" in output
    assert not (box.base / "fallback-started").exists()
    assert Slots(logger).status()["slots"]["next"]["version"] == "1.0.0"
    # the next start of the service: the version moves into a slot and is on probation
    started = start(box)
    try:
        assert wait_for(lambda: os.readlink(box.base / "current") == "slots/a")
        assert wait_for(lambda: Slots(logger).status()["slots"]["a"]["version"] == "1.0.0")
        assert Slots(logger).status()["slots"]["a"]["state"] == "probation"
    finally:
        started.terminate()
        started.communicate(timeout=10)
    assert not (box.base / "fallback-started").exists()


def test_without_a_version_recovery_mode_waits_without_a_restart_loop(box, git, logger):
    """Nothing to install - the versions offered are the ones the box went back from: the box stays in recovery mode
    for an administrator, asking again from time to time, and the service does not end."""
    started = start(box)
    try:
        assert wait_for(lambda: len(git.asked_for("/releases/latest")) >= 2, timeout=15)
        assert started.poll() is None
        assert "recovery: " in Slots(logger).status()["problem"]
    finally:
        started.terminate()
        output, _ = started.communicate(timeout=10)
    assert started.returncode == 0, output
    assert not (box.base / "fallback-started").exists()


def test_a_version_the_box_went_back_from_is_not_installed_again(box, archive, git, logger):
    git.publish(archive("0.9.1"), days_ago=10)
    started = start(box)
    try:
        assert wait_for(lambda: "no version" in Slots(logger).status()["problem"], timeout=15)
        assert not git.asked_for(".tar.gz")
    finally:
        started.terminate()
        started.communicate(timeout=10)


def test_with_updates_by_hand_recovery_mode_installs_nothing_by_itself(box, archive, git, logger):
    git.publish(archive("1.0.0"), days_ago=10)
    (box.base / "state" / "update").write_text("manual\n")
    started = start(box)
    try:
        assert wait_for(lambda: "update mode is manual" in Slots(logger).status()["problem"], timeout=15)
        assert not git.requests
    finally:
        started.terminate()
        started.communicate(timeout=10)
    # an administrator installs it on the shell: the next start runs it
    result = Slots(logger).run("update", "1.0.0", "--yes")
    assert result.returncode == 0, result.stdout + result.stderr
    assert Slots(logger).status()["slots"]["next"]["version"] == "1.0.0"
