"""The installer of a box (deploy/install/install.sh, docs/developer/deployment.md, section 5) on a folder that stands
for a fresh Raspberry Pi OS: with --root it writes only files and runs no command of the system. What needs the real
system - packages, services, the reboot, the first version - is tried on the stand-in of deploy/ansible/test/."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).parent.parent.parent
VISUDO = shutil.which("visudo") or "/usr/sbin/visudo"

pytestmark = pytest.mark.skipif(not os.path.exists(VISUDO), reason="the installer checks its sudoers rules with visudo")

FIRMWARE = "dtparam=audio=on\ncamera_auto_detect=1\ndtoverlay=vc4-kms-v3d\n"
BLOCK = """# BEGIN Tiny Jukebox (ansible)
[all]
# rfid reader
dtparam=spi=on
# onboard audio off, Audio Amp SHIM
dtparam=audio=off
dtoverlay=hifiberry-dac
gpu_mem=16
disable_splash=1
boot_delay=0
# END Tiny Jukebox (ansible)
"""


@pytest.fixture(scope="module")
def installer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("installer")
    subprocess.run([REPOSITORY / "scripts" / "build_installer.sh", "--out", out], check=True, capture_output=True)
    return out / "install.sh"


@pytest.fixture
def card(tmp_path: Path) -> Path:
    """A fresh Raspberry Pi OS Lite as the Imager writes it, and a version in slot a (the first version is fetched by
    tinyjukebox-slot, which has tests of its own)."""
    root = tmp_path / "card"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "os-release").write_text('ID=debian\nVERSION_ID="13"\nVERSION_CODENAME=trixie\n')
    (root / "etc" / "fstab").write_text("proc /proc proc defaults 0 0\nPARTUUID=1-02 / ext4 defaults 0 1\n")
    (root / "boot" / "firmware").mkdir(parents=True)
    (root / "boot" / "firmware" / "config.txt").write_text(FIRMWARE)
    slot = root / "srv" / "tinyjukebox" / "slots" / "a" / "bin"
    slot.mkdir(parents=True)
    (slot / "tinyjukebox").write_text("#!/bin/sh\n")
    (slot / "tinyjukebox").chmod(0o755)
    (root / "srv" / "tinyjukebox" / "current").symlink_to("slots/a")
    return root


def install(installer: Path, root: Path, *options: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", installer, "--root", root, "--user", "pi", "--no-admin", "--no-start", "--no-reboot", *options],
        env={**os.environ, "ARCH": "aarch64"}, capture_output=True, text=True, check=False, timeout=60,
    )  # fmt: skip


def test_a_fresh_card_is_set_up_and_needs_a_reboot(installer, card):
    result = install(installer, card)
    assert result.returncode == 10, result.stdout + result.stderr  # config.txt changed: the caller reboots
    config = (card / "boot" / "firmware" / "config.txt").read_text()
    assert config.startswith("#dtparam=audio=on\ncamera_auto_detect=1\n#dtoverlay=vc4-kms-v3d\n# BEGIN Tiny Jukebox\n")
    assert "dtoverlay=hifiberry-dac\n" in config
    assert "defaults,noatime" in (card / "etc" / "fstab").read_text()
    sudoers = card / "etc" / "sudoers.d"
    assert sorted(path.name for path in sudoers.iterdir()) == [
        "tinyjukebox-avahi", "tinyjukebox-restart", "tinyjukebox-rfid", "tinyjukebox-trigger"]  # fmt: skip
    for rule in sudoers.iterdir():
        assert rule.stat().st_mode & 0o777 == 0o440
        subprocess.run([VISUDO, "-cqf", rule], check=True, capture_output=True)
    assert "/usr/bin/rm -f /boot/firmware/tinyjukebox-revert " in (sudoers / "tinyjukebox-trigger").read_text()
    assert os.readlink(card / "etc" / "systemd" / "user" / "pipewire.socket") == "/dev/null"
    assert "--a2dp-force-audio-cd" in (card / "etc/systemd/system/bluealsa.service.d/override.conf").read_text()
    assert "Softmaster" in (card / "etc" / "asound.conf").read_text()
    base = card / "srv" / "tinyjukebox"
    assert (base / "json" / "tags.json").read_text() == "{}\n"
    assert (base / "identity").stat().st_mode & 0o777 == 0o700
    assert (base / "ssl").stat().st_mode & 0o777 == 0o700
    assert os.readlink(card / "usr/local/bin/rfid-reader") == "/srv/tinyjukebox/current/bin/rfid-reader"
    assert (card / "usr/local/lib/tinyjukebox/tinyjukebox-slot").read_bytes() == (
        REPOSITORY / "deploy" / "slot" / "tinyjukebox-slot").read_bytes()  # fmt: skip
    assert (base / "release.pub").read_bytes() == (REPOSITORY / "deploy" / "release.pub").read_bytes()
    conf = (base / "slot.conf").read_text()
    assert 'RELEASES="https://api.github.com/repos/tgd1975/tinyjukebox"' in conf
    assert "WAIT_DAYS=3" in conf
    assert "SETUP_LEVEL=1" in conf
    assert "\nUser=pi\n" in (card / "etc" / "systemd" / "system" / "tinyjukebox.service").read_text()


def test_a_second_run_changes_nothing(installer, card):
    install(installer, card)
    result = install(installer, card)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Nothing changed" in result.stdout
    assert "changed:" not in result.stdout


def test_the_block_of_the_playbook_is_replaced_without_a_reboot(installer, card):
    config = card / "boot" / "firmware" / "config.txt"
    config.write_text("#dtparam=audio=on\ncamera_auto_detect=1\n#dtoverlay=vc4-kms-v3d\n" + BLOCK)
    result = install(installer, card)
    assert result.returncode == 0, result.stdout + result.stderr  # only comments changed
    assert "(ansible)" not in config.read_text()
    assert config.read_text().count("dtoverlay=hifiberry-dac") == 1


def test_another_system_is_left_unchanged(installer, card):
    (card / "etc" / "os-release").write_text('ID=debian\nVERSION_ID="12"\n')
    result = install(installer, card)
    assert result.returncode == 1
    assert "Nothing was changed" in result.stderr
    assert not (card / "etc" / "sudoers.d").exists()
    assert (card / "boot" / "firmware" / "config.txt").read_text() == FIRMWARE


def test_the_link_to_a_library_and_its_password(installer, card, tmp_path):
    password = tmp_path / "bootstrap"
    password.write_text("bootstrap-password-of-the-test\n")
    install(installer, card, "--library", "https://nas:8866/", "--bootstrap-file", str(password))
    identity = card / "srv" / "tinyjukebox" / "identity"
    assert (identity / "library").read_text() == "https://nas:8866\n"
    assert (identity / "bootstrap").read_text() == "bootstrap-password-of-the-test\n"
    assert (identity / "bootstrap").stat().st_mode & 0o777 == 0o600
    # the box registered and deleted the password; the line of GitHub leaves the link as it is
    (identity / "bootstrap").unlink()
    (identity / "key").write_text("key\n")
    result = install(installer, card)
    assert "the box stays linked to it" in result.stdout
    assert (identity / "library").read_text() == "https://nas:8866\n"
    assert not (identity / "bootstrap").exists()


def test_a_short_bootstrap_password_is_refused(installer, card, tmp_path):
    password = tmp_path / "bootstrap"
    password.write_text("short\n")
    result = install(installer, card, "--library", "https://nas:8866", "--bootstrap-file", str(password))
    assert result.returncode == 1
    assert "at least 16 characters" in result.stderr


def test_the_token_of_a_private_repository(installer, card, tmp_path):
    token = tmp_path / "token"
    token.write_text("github_pat_x\n")
    install(installer, card, "--token-file", str(token))
    stored = card / "srv" / "tinyjukebox" / "identity" / "releases-token"
    assert stored.read_text() == "github_pat_x\n"
    assert stored.stat().st_mode & 0o777 == 0o600
    install(installer, card)
    assert stored.exists()  # the line again keeps it
    install(installer, card, "--no-token")
    assert not stored.exists()


def test_without_slots_only_the_system(installer, card):
    result = install(installer, card, "--no-slots", "--user", "kim")
    assert result.returncode == 10, result.stdout + result.stderr
    rule = (card / "etc" / "sudoers.d" / "tinyjukebox-rfid").read_text()
    assert rule == "kim ALL=(root) NOPASSWD: /usr/local/bin/rfid-reader\n"
    assert not (card / "srv" / "tinyjukebox" / "slot.conf").exists()
    assert not (card / "srv" / "tinyjukebox" / "identity").exists()
    assert not (card / "usr" / "local" / "lib" / "tinyjukebox" / "tinyjukebox-slot").exists()


def test_an_installer_that_was_not_built_refuses(card):
    result = install(REPOSITORY / "deploy" / "install" / "install.sh", card)
    assert result.returncode == 1
    assert "not built" in result.stderr
    assert not (card / "etc" / "sudoers.d").exists()
