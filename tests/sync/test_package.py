"""The scripts of the package for the NAS (deploy/synology), run the way the Package Center of DSM runs them: with the
folders of the package in a temporary folder and the Python of the tests in place of the one of the NAS."""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from tests.support import ApiClient

SOURCE = Path(__file__).parents[2] / "deploy" / "synology"
PACKAGE_DIR = "/var/packages/tinyjukebox-library"
PYTHON = "/var/packages/python312/target/bin/python3"


@pytest.fixture
def package(tmp_path):
    """The package as DSM lays it out: ``target`` (the content), ``var`` (kept), ``shares/tinyjukebox`` (shared
    folder)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    for folder in ("target/lib", "var", "shares/tinyjukebox", "scripts"):
        (tmp_path / folder).mkdir(parents=True)
    files = {"package/library.yaml": "target/library.yaml"}
    files.update({f"scripts/{name}": f"scripts/{name}" for name in ("postinst", "postupgrade", "start-stop-status")})
    for source, target in files.items():
        text = (SOURCE / source).read_text()
        text = text.replace(PACKAGE_DIR, str(tmp_path)).replace(PYTHON, sys.executable).replace("@PORT@", str(port))
        (tmp_path / target).write_text(text)
        shutil.copymode(SOURCE / source, tmp_path / target)
    environment = dict(os.environ, SYNOPKG_PKG_STATUS="INSTALL", SYNOPKG_PKGVER="1.2.3-0001")

    def run(script: str, *arguments: str, **variables: str) -> int:
        done = subprocess.run(
            [str(tmp_path / "scripts" / script), *arguments],
            env=dict(environment, **variables),
            timeout=60,
            check=False,
        )
        return done.returncode

    run.folder, run.port = tmp_path, port  # type: ignore[attr-defined]
    yield run
    run("start-stop-status", "stop")


def test_the_package_installs_starts_and_stops(package):
    assert package("postinst", wizard_admin_password="admin-password", wizard_bootstrap_password="bootstrap-pw") == 0
    assert 'bootstrap-password: "bootstrap-pw"' in (package.folder / "var" / "library.yaml").read_text()
    assert "admin" in (package.folder / "var" / "ssl" / "users.yaml").read_text()
    assert package("start-stop-status", "status") == 3

    assert package("start-stop-status", "start") == 0, (package.folder / "var" / "server.log").read_text()
    assert package("start-stop-status", "status") == 0
    page = ApiClient(package.port, str(package.folder / "var" / "ssl" / "library.crt")).login("admin", "admin-password")
    assert page.get("/api/session").json["mode"] == "library"

    assert package("start-stop-status", "stop") == 0
    assert package("start-stop-status", "status") == 3
    assert "Traceback" not in (package.folder / "var" / "server.log").read_text()


def test_an_upgrade_gives_the_config_the_key_of_the_images_folder(package):
    """The config is written once. One written before 0.29 names the folder of the covers with the key of then."""
    assert package("postinst", wizard_admin_password="admin-password", wizard_bootstrap_password="bootstrap-pw") == 0
    config = package.folder / "var" / "library.yaml"
    old = (
        config.read_text()
        .replace("    images-folder: ", "    cards-folder: ")
        .replace("tinyjukebox/images", "tinyjukebox/cards")
    )
    config.write_text(old)
    assert package("postupgrade", SYNOPKG_PKG_STATUS="UPGRADE", SYNOPKG_OLD_PKGVER="0.28.0-0001") == 0
    assert config.read_text() == old.replace("    cards-folder: ", "    images-folder: ")  # the folder stays
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    assert package("postupgrade", SYNOPKG_PKG_STATUS="UPGRADE", SYNOPKG_OLD_PKGVER="0.29.0-0001") == 0  # again: nothing
    assert config.read_text() == old.replace("    cards-folder: ", "    images-folder: ")
    assert package("start-stop-status", "start") == 0, (package.folder / "var" / "server.log").read_text()
    assert (package.folder / "shares" / "tinyjukebox" / "cards").is_dir()


def test_any_bootstrap_password_is_written_as_it_was_typed(package):
    """The dialog of DSM takes any characters; sed and plain double quotes of YAML broke on | & \\ and "."""
    import yaml

    password = 'pass|word & "quotes" \\ back slash, ünïcode 😀 and a # that is no comment'
    assert package("postinst", wizard_admin_password="admin-password", wizard_bootstrap_password=password) == 0
    config = package.folder / "var" / "library.yaml"
    assert yaml.safe_load(config.read_text())["library"]["bootstrap-password"] == password
    assert oct(config.stat().st_mode & 0o777) == "0o600"
    assert "platform: aarch64-debian-trixie" in config.read_text()  # the rest of the config as it was


def test_a_number_in_the_pid_file_that_belongs_to_another_program(package):
    """After a restart of the NAS the number in the pid file may be the one of another program: the library is
    neither taken as running nor is the other program stopped."""
    assert package("postinst", wizard_admin_password="admin-password", wizard_bootstrap_password="bootstrap-pw") == 0
    other = subprocess.Popen(["sleep", "300"])
    try:
        (package.folder / "var" / "server.pid").write_text(f"{other.pid}\n")
        assert package("start-stop-status", "status") == 3
        assert package("start-stop-status", "stop") == 0
        assert other.poll() is None  # not stopped
        assert package("start-stop-status", "start") == 0, (package.folder / "var" / "server.log").read_text()
        assert package("start-stop-status", "status") == 0
        assert int((package.folder / "var" / "server.pid").read_text()) != other.pid
    finally:
        other.kill()
        other.wait()


def test_a_start_that_fails_is_reported(package):
    assert package("start-stop-status", "start") == 1  # no config: the installation did not run
    assert "library.yaml" in (package.folder / "var" / "server.log").read_text()
    assert not (package.folder / "var" / "server.pid").exists()
