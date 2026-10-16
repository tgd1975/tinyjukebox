"""A new box joins the library with its line (docs/developer/deployment.md, section 5): the library hands out the
installer, the slot and its signature of the newest usable version without credentials - and nothing else."""

import base64
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.support import ApiClient, wait_for
from tinyjukebox.library.releases import installer_name
from tinyjukebox.sync import INTERFACE
from tinyjukebox.sync.util import public_key_pin

FILES = (("description", "json"), ("archive", "tar.gz"), ("signature", "tar.gz.sig"))


def in_the_archive(bed, version: str, tmp_path: Path, installer: bool = True) -> None:
    """A version as a release puts it into the archive of the library, with or without the installer of a box."""
    files = {}
    for kind, ending in FILES:
        files[kind] = str(tmp_path / f"tinyjukebox-box-{version}-aarch64-debian-trixie.{ending}")
        Path(files[kind]).write_text(f"{kind} of {version}")
    if installer:
        files["installer"] = str(tmp_path / installer_name(version))
        Path(files["installer"]).write_text(f'#!/bin/bash\nVERSION="{version}"\n')
    description = {"version": version, "interface": INTERFACE, "platform": "aarch64-debian-trixie", "size": 1, "sha256": "-",
                   "system": {"setup": 1, "slot-script": 2}}  # fmt: skip
    bed.library.store.add_version(description, files, "release")


def anonymous(bed) -> ApiClient:
    return ApiClient(bed.library.api.port, bed._certificate[0])


def test_the_newest_usable_version_with_an_installer_is_handed_out_without_credentials(bed, tmp_path):
    client = anonymous(bed)
    assert client.get("/install.sh").status == 404  # nothing to install yet
    in_the_archive(bed, "1.0.0", tmp_path)
    in_the_archive(bed, "1.1.0", tmp_path)
    in_the_archive(bed, "1.2.0", tmp_path)
    bed.library.store.set_version("1.2.0", "dontuse", "web:admin", "does not work")
    answer = client.get("/install.sh")
    assert (answer.status, answer.content) == (200, b'#!/bin/bash\nVERSION="1.1.0"\n')
    assert client.get("/api/sync/install").json["version"] == "1.1.0"
    assert client.get("/api/sync/install/slot").content == b"archive of 1.1.0"
    assert client.get("/api/sync/install/signature").content == b"signature of 1.1.0"
    # everything else of the interface still needs the key of a box
    assert client.get("/api/sync/versions").status in (401, 403)
    assert client.get("/api/sync/versions/1.1.0").status in (401, 403)


def test_a_version_without_an_installer_is_not_offered_for_an_installation(bed, tmp_path):
    in_the_archive(bed, "1.0.0", tmp_path)
    in_the_archive(bed, "1.1.0", tmp_path, installer=False)  # fetched before the library kept installers
    offer = bed.admin().get("/api/library/overview").json["install"]
    assert (offer["version"], offer["newest"]) == ("1.0.0", "1.1.0")
    in_the_archive(bed, "1.2.0", tmp_path, installer=False)
    bed.library.store.drop_version_files("1.0.0")
    offer = bed.admin().get("/api/library/overview").json["install"]
    assert (offer["version"], offer["newest"]) == (None, "1.2.0")
    assert anonymous(bed).get("/api/sync/install").status == 404


@pytest.mark.skipif(shutil.which("openssl") is None, reason="the pin is compared with the one of openssl")
def test_the_pin_of_the_line_fits_the_certificate_of_the_library(bed):
    certificate = bed.library.api.certificate_file
    key = subprocess.run(["openssl", "x509", "-in", certificate, "-pubkey", "-noout"], capture_output=True, check=True)
    der = subprocess.run(["openssl", "pkey", "-pubin", "-outform", "der"], input=key.stdout, capture_output=True,
                         check=True)  # fmt: skip
    digest = subprocess.run(["openssl", "dgst", "-sha256", "-binary"], input=der.stdout, capture_output=True,
                            check=True)  # fmt: skip
    expected = "sha256//" + base64.b64encode(digest.stdout).decode()
    with open(certificate, encoding="utf-8") as infile:
        assert public_key_pin(infile.read()) == expected
    assert bed.admin().get("/api/library/overview").json["install"]["pin"] == expected


def test_the_package_brings_the_installer_of_its_version(bed, tmp_path):
    slots = tmp_path / "slots"
    slots.mkdir()
    description = {"version": "9.9.9", "interface": INTERFACE, "platform": "aarch64-debian-trixie", "size": 1, "sha256": "-",
                   "system": {"setup": 1, "slot-script": 2}}  # fmt: skip
    base = slots / "tinyjukebox-box-9.9.9-aarch64-debian-trixie"
    Path(f"{base}.json").write_text(json.dumps(description))
    Path(f"{base}.tar.gz").write_text("archive")
    Path(f"{base}.tar.gz.sig").write_text("signature")
    (slots / installer_name("9.9.9")).write_text('VERSION="9.9.9"\n')
    bed.library._config = {**bed.library._config, "slots-folder": str(slots)}
    bed.library._own_slot()
    assert bed.library.store.versions()["9.9.9"]["files"]["installer"] == installer_name("9.9.9")
    assert anonymous(bed).get("/install.sh").content == b'VERSION="9.9.9"\n'


def test_the_library_fetches_the_installer_with_a_release(bed, empty_card, archive):
    from tests.sync.gitserver import GitServer

    git = GitServer()
    try:
        git.publish(archive("1.0.0"), installer=b'VERSION="1.0.0"\n')
        bed.releases.update({"api": git.url.removesuffix("/repos/owner/box"), "repository": "owner/box"})
        bed.platform = "test"
        bed.restart_library()
        assert wait_for(lambda: "1.0.0" in bed.library.store.versions())
        assert bed.library.store.versions()["1.0.0"]["files"]["installer"] == installer_name("1.0.0")
        with open(bed.library.store.version_file("1.0.0", "installer"), "rb") as infile:
            assert infile.read() == b'VERSION="1.0.0"\n'
    finally:
        git.stop()
