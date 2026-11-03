import functools
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.sync.bed import ADMIN, Bed
from tests.sync.card import Card
from tinyjukebox.sync import acting_as
from tinyjukebox.web import auth
from tinyjukebox.web.certificates import create_self_signed_certificate


@pytest.fixture(scope="session")
def certificate(tmp_path_factory):
    """(certificate, private key) of the library - created once (openssl), copied into each library."""
    folder = tmp_path_factory.mktemp("certificate")
    files = (str(folder / "library.crt"), str(folder / "library.key"))
    create_self_signed_certificate(*files)
    return files


@functools.cache
def users():
    """Users file content - hashed with a small scrypt cost (fast logins)."""
    original = auth._SCRYPT_N
    auth._SCRYPT_N = 2**8
    try:
        return {ADMIN[0]: {"password-hash": auth.hash_password(ADMIN[1]), "role": "admin"}}
    finally:
        auth._SCRYPT_N = original


@pytest.fixture(autouse=True)
def _clock_set(monkeypatch) -> None:
    """The boxes of the tests have a clock that was set from the network, whatever the computer of the tests has - the
    statistics count the time of day only then."""
    monkeypatch.setattr("tinyjukebox.sync.box.clock_synchronized", lambda: True)


@pytest.fixture(autouse=True)
def _at_the_console() -> Iterator[None]:
    """What a test changes on a box counts as entered by a person, as on the console - not as done by the software
    itself."""
    with acting_as("console"):
        yield


@pytest.fixture
def bed(tmp_path, logger, certificate) -> Iterator[Bed]:
    """A running library; boxes are added with ``bed.box(name)``."""
    bed = Bed(tmp_path, logger, certificate, users())
    yield bed
    bed.stop()


@pytest.fixture
def empty_card(tmp_path, monkeypatch) -> Card:
    """A box right after its setup: the slot scripts, the public key of the releases, no slot yet."""
    import subprocess

    if any(shutil.which(tool) is None for tool in ("openssl", "tar", "sha256sum")):
        pytest.skip("making a slot archive needs openssl, tar and sha256sum")
    card = Card(tmp_path / "card", monkeypatch)
    (card.base / "slot.conf").write_text("PLATFORM=test\n")
    key = tmp_path / "release.key"
    subprocess.run(
        ["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", str(key)],
        check=True, capture_output=True,
    )  # fmt: skip
    subprocess.run(
        ["openssl", "pkey", "-in", str(key), "-pubout", "-out", str(card.base / "release.pub")],
        check=True, capture_output=True,
    )  # fmt: skip
    return card


@pytest.fixture
def archive(empty_card, tmp_path: Path):
    """``archive(version)`` makes a small slot archive that the card trusts and returns its file; its signature and
    its description lie next to it, as in a release. ``program`` is what the version does when it is started."""
    import hashlib
    import json
    import subprocess

    def make(version: str, program: str = "exec sleep infinity") -> Path:
        folder = tmp_path / f"slot-{version}"
        (folder / "bin").mkdir(parents=True)
        (folder / "bin" / "tinyjukebox").write_text(f"#!/bin/sh\n{program}\n")
        (folder / "bin" / "tinyjukebox").chmod(0o755)
        (folder / "VERSION").write_text(version + "\n")
        sums = subprocess.run(
            ["sha256sum", "VERSION", "bin/tinyjukebox"], cwd=folder, check=True, capture_output=True, text=True
        )
        (folder / "SHA256SUMS").write_text(sums.stdout)
        file = tmp_path / f"tinyjukebox-box-{version}-test.tar.gz"
        subprocess.run(["tar", "-czf", str(file), "-C", str(folder), "."], check=True)
        description = {"version": version, "interface": 1, "platform": "test", "size": file.stat().st_size,
                       "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
                       "system": {"setup": 1, "slot-script": 1}}  # fmt: skip
        (tmp_path / f"tinyjukebox-box-{version}-test.json").write_text(json.dumps(description))
        key = str(tmp_path / "release.key")
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", key, "-out", f"{file}.sig", str(file)], check=True)
        return file

    return make
