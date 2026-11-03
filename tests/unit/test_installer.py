"""The installer of a box (deploy/install/install.sh, docs/developer/deployment.md, section 5) on a folder that stands
for a fresh Raspberry Pi OS: with --root it writes only files and runs no command of the system. What needs the real
system - packages, services, the reboot, the first version - is tried on the stand-in of deploy/install/test/."""

import hashlib
import os
import re
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
    assert (card / "usr/local/lib/tinyjukebox/tinyjukebox-slot").read_bytes() == (
        REPOSITORY / "deploy" / "slot" / "tinyjukebox-slot").read_bytes()  # fmt: skip
    assert (base / "release.pub").read_bytes() == (REPOSITORY / "deploy" / "release.pub").read_bytes()
    conf = (base / "slot.conf").read_text()
    assert 'RELEASES="https://api.github.com/repos/tgd1975/tinyjukebox"' in conf
    assert "WAIT_DAYS=3" in conf
    assert "SETUP_LEVEL=1" in conf
    assert "PLATFORM=aarch64-debian-trixie " in conf  # --root on a card in the PC does not take the one of the PC
    assert "\nUser=pi\n" in (card / "etc" / "systemd" / "system" / "tinyjukebox.service").read_text()
    # the rule allows the link, which only root can change, to the launcher of root - not the program of a slot
    assert (sudoers / "tinyjukebox-rfid").read_text() == "pi ALL=(root) NOPASSWD: /usr/local/bin/rfid-reader\n"
    lib = card / "usr" / "local" / "lib" / "tinyjukebox"
    assert os.readlink(card / "usr/local/bin/rfid-reader") == "/usr/local/lib/tinyjukebox/rfid-reader"
    assert (lib / "rfid-reader").stat().st_mode & 0o777 == 0o755
    lines = (lib / "rfid-reader").read_text().splitlines()
    paths = [line for line in lines if re.match(r"(BASE|LIB|CHECK|PROGRAM)=", line)]
    assert paths == ["BASE=/srv/tinyjukebox", "LIB=/usr/local/lib/tinyjukebox", "CHECK=/run/tinyjukebox-rfid",
                     "PROGRAM=/var/lib/tinyjukebox-rfid"]  # fmt: skip
    assert (lib / "release.pub").read_bytes() == (REPOSITORY / "deploy" / "release.pub").read_bytes()
    assert (lib / "rfid-reader.trusted").read_text() == ""  # slot a of this card has no rfid-reader


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
    # rfid-reader is the program the playbook builds as root into /usr/local/bin (role rfid): no launcher
    rule = (card / "etc" / "sudoers.d" / "tinyjukebox-rfid").read_text()
    assert rule == "kim ALL=(root) NOPASSWD: /usr/local/bin/rfid-reader\n"
    assert not (card / "usr" / "local" / "bin" / "rfid-reader").is_symlink()
    assert not (card / "usr" / "local" / "lib" / "tinyjukebox" / "rfid-reader").exists()
    assert not (card / "srv" / "tinyjukebox" / "slot.conf").exists()
    assert not (card / "srv" / "tinyjukebox" / "identity").exists()
    assert not (card / "usr" / "local" / "lib" / "tinyjukebox" / "tinyjukebox-slot").exists()


def test_an_installer_that_was_not_built_refuses(card):
    result = install(REPOSITORY / "deploy" / "install" / "install.sh", card)
    assert result.returncode == 1
    assert "not built" in result.stderr
    assert not (card / "etc" / "sudoers.d").exists()


# --- rfid-reader runs as root: the launcher (docs/developer/box.md, section 7) ----------------------------------------


def make_key(folder: Path, name: str) -> Path:
    """An EC key (P-256) as the one of the releases; its public part lies next to it as NAME.pub."""
    key = folder / f"{name}.key"
    subprocess.run(["openssl", "genpkey", "-algorithm", "EC", "-pkeyopt", "ec_paramgen_curve:P-256", "-out", key],
                   check=True, capture_output=True)  # fmt: skip
    subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-out", folder / f"{name}.pub"], check=True)
    return key


def reader_slot(card: Path, name: str, version: str, key: Path | None = None) -> Path:
    """A slot whose rfid-reader says who it is and how it was started; signed as a release signs it, if ``key``."""
    folder = card / "srv" / "tinyjukebox" / "slots" / name
    (folder / "bin").mkdir(parents=True, exist_ok=True)
    (folder / "bin" / "tinyjukebox").write_text("#!/bin/sh\n")
    (folder / "bin" / "tinyjukebox").chmod(0o755)
    program = folder / "bin" / "rfid-reader"
    program.write_text(f'#!/bin/sh\necho "reader of {version}, started as $0 $*"\n')
    program.chmod(0o755)
    (folder / "VERSION").write_text(f"{version}\n")
    sums = subprocess.run(["sha256sum", "./VERSION", "./bin/rfid-reader", "./bin/tinyjukebox"], cwd=folder,
                          check=True, capture_output=True, text=True)  # fmt: skip
    (folder / "SHA256SUMS").write_text(sums.stdout)
    (folder / "SHA256SUMS.sig").unlink(missing_ok=True)
    if key is not None:
        subprocess.run(["openssl", "dgst", "-sha256", "-sign", key, "-out", folder / "SHA256SUMS.sig",
                        folder / "SHA256SUMS"], check=True)  # fmt: skip
    return folder


def point_to(card: Path, target: str) -> None:
    current = card / "srv" / "tinyjukebox" / "current"
    current.unlink()
    current.symlink_to(target)


class Launcher:
    """The launcher as the installer wrote it onto the card, with the folders of the test in place of those of a box,
    run as sudo runs it: with an environment of its own."""

    def __init__(self, installer: Path, card: Path, folder: Path) -> None:
        self.installer, self.card, self.folder = installer, card, folder
        self.lib = card / "usr" / "local" / "lib" / "tinyjukebox"
        self.check, self.program = folder / "run", folder / "var-lib"
        self.install()

    def install(self) -> None:
        """The installer runs (again): launcher, key and the list of the programs that are trusted."""
        result = install(self.installer, self.card)
        assert result.returncode in (0, 10), result.stdout + result.stderr
        text = (self.lib / "rfid-reader").read_text()
        paths = {"BASE": self.card / "srv" / "tinyjukebox", "LIB": self.lib, "CHECK": self.check,
                 "PROGRAM": self.program}  # fmt: skip
        for name, path in paths.items():
            text = re.sub(rf"^{name}=.*$", f"{name}={path}", text, count=1, flags=re.MULTILINE)
        (self.folder / "launcher").write_text(text)
        (self.folder / "launcher").chmod(0o755)

    def trust(self, public_key: Path) -> None:
        """The key of the releases that the installer copied next to the launcher."""
        shutil.copy(public_key, self.lib / "release.pub")

    def __call__(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run([self.folder / "launcher", *arguments], env={"PATH": "/usr/bin:/bin"},
                              capture_output=True, text=True, check=False, timeout=30)  # fmt: skip


@pytest.fixture
def launcher(installer: Path, card: Path, tmp_path: Path) -> Launcher:
    if shutil.which("openssl") is None:
        pytest.skip("the launcher verifies with openssl")
    return Launcher(installer, card, tmp_path)


def test_the_program_of_a_signed_slot_runs_as_it_was_checked(launcher, card, tmp_path):
    key = make_key(tmp_path, "release")
    launcher.trust(tmp_path / "release.pub")
    reader_slot(card, "a", "0.45.0", key)
    started = launcher("--pin-rst", "26", "--pin-cs", "8")
    assert started.returncode == 0, started.stderr
    # what runs is the checked copy, which only root can change - not the file of the slot
    assert started.stdout == f"reader of 0.45.0, started as {launcher.program}/rfid-reader --pin-rst 26 --pin-cs 8\n"
    assert (launcher.program / "rfid-reader").stat().st_mode & 0o777 == 0o700
    assert launcher.check.stat().st_mode & 0o777 == 0o700
    assert sorted(path.name for path in launcher.check.iterdir()) == ["lock"]  # nothing is left in memory
    assert launcher().returncode == 0  # again and again: one copy, replaced


def test_a_changed_or_unsigned_program_is_refused(launcher, card, tmp_path):
    key = make_key(tmp_path, "release")
    launcher.trust(tmp_path / "release.pub")
    slot = reader_slot(card, "a", "0.45.0", key)
    (slot / "bin" / "rfid-reader").write_text("#!/bin/sh\necho changed\n")
    refused = launcher()
    assert (refused.returncode, refused.stdout) == (2, "")
    assert "not the one its signed checksums name" in refused.stderr
    assert refused.stderr.startswith("rfid-reader: not started - ")  # the driver writes it to the log
    # a list of checksums that fits the program, but nobody signed it
    reader_slot(card, "a", "0.45.0")
    refused = launcher()
    assert refused.returncode == 2
    assert "the slot brings no signature of its checksums" in refused.stderr
    # signed, but not with the key of the releases
    reader_slot(card, "a", "0.45.0", make_key(tmp_path, "another"))
    refused = launcher()
    assert refused.returncode == 2
    assert "not signed by the key of the releases" in refused.stderr
    assert not (launcher.program / "rfid-reader").exists()
    assert sorted(path.name for path in launcher.check.iterdir()) == ["lock"]


def test_the_programs_of_the_slots_on_the_box_are_trusted_by_the_installer(launcher, card):
    """Versions up to 0.44 have no signature of their checksums: the installer lists the programs that are on the box
    when it runs. A program that comes later is refused until the installer runs again."""
    reader_slot(card, "a", "0.44.0")
    assert launcher().returncode == 2
    launcher.install()
    digest = hashlib.sha256((card / "srv/tinyjukebox/slots/a/bin/rfid-reader").read_bytes()).hexdigest()
    assert (launcher.lib / "rfid-reader.trusted").read_text() == f"{digest}  version 0.44.0\n"
    started = launcher()
    assert started.returncode == 0, started.stderr
    assert started.stdout.startswith("reader of 0.44.0")
    assert "Nothing changed" in install(launcher.installer, card).stdout  # the list stays as it is
    # ... also when the version moved from slots/next into its slot, and the fallback slot holds it as well
    slots = card / "srv" / "tinyjukebox" / "slots"
    shutil.copytree(slots / "a", slots / "fallback")
    assert "Nothing changed" in install(launcher.installer, card).stdout


def test_a_link_current_that_points_elsewhere_only_chooses_among_trusted_programs(launcher, card, tmp_path):
    key = make_key(tmp_path, "release")
    launcher.trust(tmp_path / "release.pub")
    reader_slot(card, "a", "0.45.0", key)
    reader_slot(card, "b", "0.46.0", key)
    # the user of the service owns the link: it may pick another slot ...
    point_to(card, "slots/b")
    assert launcher().stdout.startswith("reader of 0.46.0")
    # ... but not a program of its own, wherever it lies
    own = tmp_path / "own"
    shutil.copytree(card / "srv/tinyjukebox/slots/b", own)
    (own / "bin" / "rfid-reader").write_text("#!/bin/sh\nid\n")
    point_to(card, str(own))
    refused = launcher()
    assert (refused.returncode, refused.stdout) == (2, "")
    # nor a link to a trusted program: the copy is never made through a link
    (own / "bin" / "rfid-reader").unlink()
    (own / "bin" / "rfid-reader").symlink_to(card / "srv/tinyjukebox/slots/a/bin/rfid-reader")
    refused = launcher()
    assert refused.returncode == 2
    assert "cannot be read, or it is no file" in refused.stderr
    point_to(card, "slots/nothing")
    assert "there is no active slot" in launcher().stderr


def test_the_launcher_of_the_repository_is_not_started():
    """The paths are written into it by the installer: sudo resets the environment, no variable counts."""
    refused = subprocess.run([REPOSITORY / "deploy" / "install" / "rfid-reader-launcher"],
                             env={"PATH": "/usr/bin:/bin"}, capture_output=True, text=True, check=False,
                             timeout=30)  # fmt: skip
    assert refused.returncode == 2
    assert "the launcher was not written by the installer" in refused.stderr


def test_the_line_of_a_library_with_its_pin(installer, card):
    result = install(installer, card, "--pin", "sha256//not a pin")
    assert result.returncode == 1
    assert "--pin sha256//" in result.stderr
    result = install(installer, card, "--pin", "sha256//BMXwOMDU4p76jipUPSMCtnHjTtYTuPnHDAZ0ogWeWUI=")
    assert result.returncode == 10, result.stdout + result.stderr
