"""The SD card of a box for the tests of the slot scripts (deploy/slot), which run on a folder here."""

import os
import shutil
import stat
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parent.parent.parent
TOOLS = ("bash", "jq", "flock")


class Card:
    """The SD card of a box as the slot scripts see it: ``srv/tinyjukebox`` with slots and state."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.root = root
        self.base = root / "srv" / "tinyjukebox"
        (self.base / "state").mkdir(parents=True)
        (root / "etc").mkdir()
        (root / "etc" / "os-release").write_text("ID=debian\nVERSION_CODENAME=trixie\n")
        # tinyjukebox-slot as the playbook installs it, working on this card
        bin_folder = root / "bin"
        bin_folder.mkdir()
        script = bin_folder / "tinyjukebox-slot"
        script.write_text(f'#!/bin/bash\nROOT={root} exec {REPOSITORY}/deploy/slot/tinyjukebox-slot "$@"\n')
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        monkeypatch.setenv("PATH", f"{bin_folder}{os.pathsep}{os.environ['PATH']}")

    def slot(self, name: str, version: str, state: str) -> None:
        folder = self.base / "slots" / name / "bin"
        folder.mkdir(parents=True)
        program = folder / "tinyjukebox"
        program.write_text("#!/bin/sh\nexec sleep infinity\n")
        program.chmod(0o755)
        (self.base / "slots" / name / "VERSION").write_text(version + "\n")
        with open(self.base / "state" / "slots", "a", encoding="utf-8") as outfile:
            outfile.write(f"{name} {version} {state} 2026-10-06T10:00:00Z 1\n")

    def activate(self, name: str) -> None:
        link = self.base / "current"
        if link.is_symlink():
            link.unlink()
        link.symlink_to(f"slots/{name}")
        (self.base / "state" / "active").write_text(name + "\n")

    def state(self, name: str) -> str:
        return (self.base / "state" / name).read_text() if (self.base / "state" / name).exists() else ""

    def watch_curl(self) -> Path:
        """curl, as the slot scripts find it, writes its arguments to the returned file first - what anybody can
        read in the list of processes."""
        arguments = self.root / "curl-arguments"
        curl = self.root / "bin" / "curl"
        curl.write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> {arguments}\nexec {shutil.which("curl")} "$@"\n')
        curl.chmod(0o755)
        return arguments
