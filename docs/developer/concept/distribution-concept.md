# Concept: installing without the repository – installer, Docker image, PyPI

Three ways to get Tiny Jukebox without a clone of the repository, without Ansible and without a PC that prepares
anything: one line on a fresh Raspberry Pi sets up a box; one Docker image runs the library, or a box without
hardware; `pip install` does the same on a PC. All three are further forms of the same release, next to the slot for
the boxes and the package for the NAS. How a box updates itself does not change. **Nothing is implemented yet.**
Open points: section 11.2; the plan is in section 10. State: 2026-10-10.

Related documents:

- [deployment.md](../deployment.md): the playbook, its roles and variables, how a box is set up today
- [box.md](../box.md): the system a box needs, with reasons
- [library.md](../library.md), section 3: the library and its package; section 4.14: how a box registers; section 6:
  slots; section 6.16: a box without a library; section 8: the release pipeline
- [development.md](../development.md), section 8: versions and releases

## 1. What exists, and what is missing

| Exists | Where |
| --- | --- |
| A box holds its software in slots, fetches a version itself – from the library, or from the releases – verifies its signature, tries it and goes back if it fails | [library.md](../library.md), section 6 |
| A box without a library fetches even its first version itself | [deployment.md](../deployment.md), section 3 |
| The slot brings its own Python, all dependencies, `rfid-reader` and the config of its version: nothing is compiled on a box | [library.md](../library.md), section 6.14 |
| The playbook sets up the system of a box from the PC: packages, firmware settings, sound, Bluetooth, slot scripts, service | [deployment.md](../deployment.md) |
| The library is a package for one model of Synology NAS, and runs on the PC from the clone (`start_library.sh`) | [library.md](../library.md), sections 3.2 and 3.8 |
| A box without hardware runs on the PC from the clone (`start_sim.sh`) | [development.md](../development.md), section 2 |
| A push to `master` that changes the software becomes a release with the slot and the package | [library.md](../library.md), section 8 |

Whoever is not the developer cannot use any of this today:

- **A box** needs a PC with the clone, Ansible, a vault file and an SSH key.
- **The library** needs this one NAS, or the clone with `uv` and a config and a users file made by hand.
- **Trying it** – the page, figures, the interplay of box and library – needs the clone too.

```text
                        today                          new
a box                   playbook from the PC           one line on the box: the installer
library on this NAS     package                        unchanged
library elsewhere       clone and start_library.sh     Docker image, or pip
trying it on a PC       clone and start_sim.sh         Docker image, or pip
```

## 2. One release, five forms

| Form | For | State |
| --- | --- | --- |
| slot archive, signed | the software of a box | exists |
| package `.spk` | the library on the Synology NAS | exists |
| installer `install.sh` | the system of a box, and its first version | new, section 3 |
| Docker image | the library; a box without hardware | new, section 5 |
| Python package on PyPI | the library and a box without hardware on a PC | new, section 6 |

- **All forms are built from the same commit by the same workflow and carry the same version** (section 7).
- **The slot stays the only way software reaches a real box.** Neither the image nor PyPI is a way to run or update
  a box with hardware: the slot holds exactly the versions that were tested, brings `rfid-reader`, and the box can
  go back from it.

## 3. The installer of a box

### 3.1 The line

On a Raspberry Pi with a fresh card, logged in over SSH:

```bash
curl -fsSL https://github.com/tgd1975/tinyjukebox/releases/latest/download/install.sh | sudo bash
```

The installer sets up the system, asks two questions (section 3.4), fetches the newest release into a slot and
starts the box. At the end it prints the address of the web page. On a Pi 3 it takes a few minutes, most of it the
packages; nothing is compiled.

There are two sources, and the source decides where the box belongs:

| The line comes from | The box afterwards |
| --- | --- |
| GitHub | stands alone and updates itself from the releases ([library.md](../library.md), section 6.16) |
| the page of a library (section 3.9) | is linked to this library: catalog, albums and versions come from there |

### 3.2 What it expects

- Raspberry Pi OS Lite (64-bit), Debian 13 "trixie", on aarch64 – the platform the slot is built for. On anything
  else the installer stops before it changes something and says what it found.
- Written with the Raspberry Pi Imager, which sets what the installer never touches: hostname, user, WLAN and its
  country, time zone, SSH.
- Internet, for the packages of the operating system – also when the line comes from a library.
- The hardware of [box.md](../box.md), section 1: Audio Amp SHIM, RC522 reader, four buttons, RGB LED. The installer
  writes the firmware settings for exactly this hardware. A box that is not wired yet can be installed: it starts,
  its page answers, and the log names what is missing.

### 3.3 What it does

The steps are those of the playbook roles `base`, `audio`, `bluetooth` and `slots`, in this order:

| Step | Content | Today in role |
| --- | --- | --- |
| Check | operating system, processor, free space; root rights | `base` |
| Packages | what a box needs to run: `ffmpeg`, `alsa-utils`, `libasound2`, `liblgpio1`, `openssl`, `curl`, `jq`, `util-linux`, `avahi-daemon`, `bluez`, `bluez-alsa-utils`, `rfkill`. Never an upgrade | `base`, `bluetooth`, `slots` |
| User | the groups `gpio`, `audio`, `bluetooth`; the sudoers rule for `rfid-reader` | `base`, `bluetooth`, `rfid` |
| Robustness | no automatic updates, journal in RAM, `noatime`, the services that only slow the start down | `base` |
| Firmware | the block in `config.txt`: SPI on, onboard audio and `vc4` off, `hifiberry-dac` | `base` |
| Sound | `/etc/asound.conf`; the volume control `Softmaster` (section 3.5) | `audio` |
| Bluetooth | options of BlueALSA, services, `rfkill unblock`, no `bluealsa-aplay` | `bluetooth` |
| Slots | folders below `/srv/tinyjukebox`; slot scripts in `/usr/local/lib/tinyjukebox`; `slot.conf`; `release.pub`; the service unit | `slots` |
| Source | linked to a library: its address and the bootstrap password. Otherwise the address of the releases | `slots` |
| First version | `tinyjukebox-slot update`: from the library that handed out the line, or from the releases. It becomes the fallback version too | `slots` |
| Users | the admin of the web page, with `tinyjukebox-users` of the slot | `tinyjukebox` |
| Start | enables and starts the service, waits until the page answers, prints its address | play |

What the installer leaves alone:

- **Everything the Imager sets:** hostname, WLANs, time zone, locale.
- **SSH.** The playbook switches off the login with a password after it checked that a key is in place. The
  installer does not: whoever logged in with a password to run the line would lock themselves out.
- **Compilers and development packages** (`build-essential`, `swig`, `python3-dev`, the `-dev` libraries, `git`,
  `uv`). A box with slots compiles nothing.
- **The config of the box.** Without a file `/srv/tinyjukebox/config/tinyjukebox.yaml` a box runs with the config of its
  slot, which is the config of the hardware of [box.md](../box.md). The installer writes none.

### 3.4 What it asks

| Question | When | Without a terminal |
| --- | --- | --- |
| Password of the user `admin` of the web page, twice, at least 8 characters | if the box has no user yet | `--admin-password-file FILE` |
| Bootstrap password of the library | if the line came from a library and the box has no key for it yet | `--bootstrap-file FILE` |

- The questions are read from the terminal, not from the pipe the script arrives through.
- **No secret in the command line:** a password on the line would stay in the history of the shell.
- **Options** for everything that has a default: `--user NAME` (default: the user who called `sudo`; it runs the
  service), `--version X.Y.Z` (default: the newest), `--library URL`, `--no-start`, `--yes` (no question before the
  reboot of section 3.5). With all answers given as options the installer runs without a person – this is how the
  playbook calls it (section 3.10).

### 3.5 Reboot

A change of `config.txt` needs a reboot, and only after it the sound card exists. The installer does everything
else first, including the first version, then says that it restarts the box and does so.

- **What needs the sound card runs after the reboot:** creating the volume control `Softmaster` and storing it. A
  unit `tinyjukebox-setup.service` that runs once before `tinyjukebox.service` does it and does nothing from then on.
- **A box whose `config.txt` was right already** – the second run of the installer – does not reboot.

### 3.6 Running it again

The same line, any number of times. What is right already stays unchanged, as with the playbook.

- **It never touches the data:** albums, figures, positions, users, the key of the box for its library.
- **It never changes which version runs.** A box that has a slot keeps it; versions come from the library or the
  releases, as always.
- **It brings the system up to date:** the slot scripts, the service unit, the public key and the settings, in the
  state of the release the installer belongs to. A version that needs a newer setup or slot script is not installed
  by the box today, and its page says that the box needs the playbook ([library.md](../library.md), section 6.14).
  From now on the page names the line of section 3.1.
- **The slot scripts stay outside the slots,** and no version of the software replaces them by itself: a version
  with a defective updater must not be able to lock the box in. Running the installer is a step a person takes.

### 3.7 One file per release

`install.sh` is a file of every release, built by `scripts/build_installer.sh` from the files of the repository:

| In the installer | Source |
| --- | --- |
| the steps of section 3.3 | `deploy/install/install.sh` |
| `tinyjukebox-slot`, `tinyjukebox-start`, `tinyjukebox-functions`, the service unit | `deploy/slot/` |
| the public key of the releases | `deploy/release.pub` |
| `/etc/asound.conf` | `deploy/config/asound.conf` |
| the level of the setup, the version it belongs to | `deploy/ansible/group_vars/all/main.yaml`, `pyproject.toml` |

- **One file, about 60 kB, that needs nothing else from the repository.** The line of section 3.1 can only pipe one
  file, and the library can hand out the same file unchanged.
- **The address `releases/latest/download/install.sh` always names the installer of the newest release.** A push
  that changes only documents is no release and changes nothing there.
- **The installer installs the version it belongs to,** unless `--version` names another one. Installer and slot of
  one release fit each other by construction.
- **The body is one function that is called in the last line.** A download that breaks off in the middle runs
  nothing.

### 3.8 Trust

| What | How it is trusted |
| --- | --- |
| the installer, from GitHub | HTTPS with the certificate authorities of the system. Whoever runs the line trusts the repository – as with every script that is installed this way |
| the installer, from a library | the line pins the certificate of the library (section 3.9) |
| the slot | its checksum and its signature against the public key inside the installer, before anything is unpacked – unchanged ([library.md](../library.md), section 6.14) |

- **Looking first** is always possible: download the file, read it, run it with `sudo bash install.sh`.
- **A private repository:** `--token-file FILE` – the installer then fetches through the interface of the git
  server and leaves the token on the box for the later versions (`identity/releases-token`). The line itself needs
  the token too, as a header of `curl`.

### 3.9 The line of a library

The tab "Boxes" of the library gets the section "Add a box", for admins. It shows the line for this library:

```bash
curl -fsSLk --pinnedpubkey 'sha256//…' https://nas:8866/install.sh | sudo bash -s -- --library https://nas:8866
```

- **The pin is the public key of the certificate of the library.** The library has a self-signed certificate; with
  the pin `curl` accepts exactly this one, so the installer cannot come from somebody else in the network. The
  admin reads the line on the page of the library and pastes it into the terminal of the box.
- **The installer asks for the bootstrap password.** The page shows where it is set; it does not put it into the
  line.
- **What the library hands out without credentials** is new and limited to three things: the installer, the slot of
  one version with its signature, and the description of that slot. They are the files of a release, which are
  public, and the box verifies the slot by its signature. Everything else of the interface answers only with the
  key of a box, as today ([library.md](../library.md), section 4.14).
- **Which version:** the newest one in the state `use` – what the library would offer this box anyway. The library
  hands out the installer of that same version: it keeps the installer of every version next to its slot, fetched
  with the release, and package and image bring the one of their version. A version that was fetched before the
  library kept installers is not offered for an installation; the section says so and names the newest one that is.
- **After the installation** the box registers at its first start with the bootstrap password, receives its key and
  deletes the password – unchanged. It appears in the tab "Boxes" by itself; nothing is confirmed there.
- The address in the line is the one the admin's browser uses for the library. The section says that the box must
  reach the library under this address.

### 3.10 The playbook

The playbook stays. It is the way from the PC, for the developer, and it does more than the installer: the git
repository on the box into which the PC pushes, the tools to build on the box, further WLANs, SSH with key only, the
data of the previous box.

**The playbook calls the installer for what both do** (open point, section 11.2): there is then one description of
how the system of a box is set up, and the box of the developer is set up exactly like everybody else's.

| Role | Afterwards |
| --- | --- |
| `base` | hostname, time zone, locale, WLANs, SSH, and the packages for building; the rest moves into the installer |
| `tinyjukebox` | unchanged: repository on the box, `uv`, users from the vault, config with overrides |
| new: `install` | builds the installer from the clone, copies it to the box and runs it with `--yes` and the answers from inventory and vault; with `tinyjukebox_slot_archive` it installs from that file |
| `audio`, `bluetooth`, `slots` | gone: their steps are in the installer |
| `rfid` | unchanged: builds `rfid-reader` on the box for a box without slots, and for trying a change |
| `migrate` | unchanged |

- The installer comes from the clone as it is, like the slot scripts today: a change of the setup is tried from the
  PC before it is released.
- A box without slots (`tinyjukebox_slots: false`) is still possible: the installer gets `--no-slots` and then sets up
  the system only.
- The variables of the playbook stay; those of the moved steps become options of the installer.

## 4. Starting with one folder

The image and the package from PyPI both need what the clone provides today: a config file with paths, a users file
made with `tinyjukebox-users`, a bootstrap password. New: library and box start with nothing but a folder.

```bash
tinyjukebox-library --data ~/library          # https://localhost:8866/
tinyjukebox --data ~/box --no-hardware        # https://localhost:8443/
```

| | With `--data DIR` |
| --- | --- |
| Config | built in: everything lies below `DIR` – `json/`, `albums/`, `cards/`, `ssl/`, `log/`. A file `DIR/config.yaml`, if there is one, changes single values. `-c FILE` works as today and excludes `--data` |
| Certificate | created at the first start, as today |
| Admin of the page | at the first start, if there is no user: asked on the terminal; without a terminal taken from `TINYJUKEBOX_ADMIN_PASSWORD`, or generated and written to the log once |
| Bootstrap password of a library | from `TINYJUKEBOX_BOOTSTRAP_PASSWORD`, or generated; shown and changed in the tab "System" of the library |
| Library of a box | `TINYJUKEBOX_LIBRARY` and `TINYJUKEBOX_BOOTSTRAP_PASSWORD` link a box at its first start; later on its page, as today |

- **The environment variables count at the first start only.** Afterwards the folder is the truth, and passwords
  are changed on the page.
- **The Synology package keeps its dialog and its config;** `start_library.sh` and `start_sim.sh` keep theirs. They
  can be changed to `--data` later.
- A box started this way has no slots and never updates itself (`Slots.available` is false,
  [library.md](../library.md), section 6.15).

## 5. The Docker image

### 5.1 One image, two roles

`ghcr.io/tgd1975/tinyjukebox`, with the tags `<version>` and `latest`, for `linux/amd64` and `linux/arm64`.

| Command | Runs | Port |
| --- | --- | --- |
| none, or `library` | the library | 8866 |
| `box` | a box without hardware and without sound: figures, buttons and LED are on its page | 8443 |
| anything else | that command, e.g. `tinyjukebox-users`, `tinyjukebox-fill-library` | |

- **One image,** because library and box are one Python package with two commands. Two images would be the same
  layers under two names.
- **The registry of GitHub:** no further account, free for public images, and the workflow that makes the release
  may push there with its own token. Docker Hub can be added later with a second push of the same image
  (open point, section 11.2).

### 5.2 The library

```bash
docker run -d --name library -p 8866:8866 \
    -v library:/data -v /path/to/albums:/data/albums \
    -e TINYJUKEBOX_ADMIN_PASSWORD=… ghcr.io/tgd1975/tinyjukebox
```

- **Everything that must survive lies below `/data`** (section 4): one volume, and if wanted the albums as a folder
  of the host, so that they can be put there directly.
- **The image brings the slot of its version,** as the package does, so the library can hand a version to a box
  before it has asked the releases. It fetches newer versions by itself, as on the NAS.
- **Updating is pulling a newer image.** Nothing does that by itself, as on the NAS: boxes update themselves, the
  library is updated by a person. The page shows that a newer version exists.
- A box is added with the line of section 3.9. The address in it must be the one of the host, not the name of the
  container.

### 5.3 A box without hardware

```bash
docker run -d --name box -p 8443:8443 -v box:/data ghcr.io/tgd1975/tinyjukebox box
```

For trying: the whole page of a box, figures placed with a click, albums that "play" in real time without sound.
It is not a way to run a real box (section 2), and it does not update itself.

### 5.4 Both together

A `compose.yaml` in `deploy/docker/` starts a library and a box that is linked to it, with one command. It is the
quickest way to see what the project does, and the example in the README.

### 5.5 What is in the image

| | |
| --- | --- |
| Base | the slim Debian image of Python 3.13 |
| Added | `ffmpeg` (the player of a box needs it, also without sound), the wheel of this version with its dependencies from `uv.lock`, the slot of this version |
| Not in it | the extra `box` (GPIO, ALSA, D-Bus), compilers, the repository |
| User | not root; `/data` belongs to it |
| Health | the image declares a check that asks the page |

The `Dockerfile` lies in `deploy/docker/`. It installs the wheel that the workflow built, not the source tree: the
image and PyPI hold the same files.

## 6. PyPI

The project is published under its name, as source archive and wheel.

```bash
pip install tinyjukebox        # or: uvx --from tinyjukebox tinyjukebox-library --data ~/library
```

- **What one gets:** all commands of the project, with the web page. With section 4 the library and a box without
  hardware start with one line. A box needs `ffmpeg` on the computer.
- **What it is not:** the way to a real box. `pip install tinyjukebox[box]` installs the hardware libraries and
  compiles them on a Pi, but brings no `rfid-reader`, no tested set of versions and no way back.
- **Why at all:** it costs one step of the workflow, it is the usual place to look for a Python project, and the
  name is taken by us and not by somebody else.
- **Uploading without a stored password:** PyPI trusts the workflow of this repository directly ("Trusted
  Publishing"). This is set once in the account on PyPI; the repository holds no secret for it.
- **A version on PyPI is final.** It cannot be replaced, only withdrawn ("yanked") and followed by the next one.
  That fits the releases: a tag is never moved either ([library.md](../library.md), section 8.1).
- `README.md` is the page of the project on PyPI. Its links to the documents become full addresses, because
  relative ones lead nowhere there.

## 7. The release workflow

| Form | Built on every push | Published by a release |
| --- | --- | --- |
| slot, package | yes, as today | files of the release, as today |
| installer | yes | file of the release: `install.sh` |
| wheel and source archive | yes | PyPI |
| image | yes, for both platforms | `ghcr.io`, tags `<version>` and `latest` |

- **Built on every push,** so that a change that breaks one of the forms is noticed at once – the rule that holds
  for slot and package ([library.md](../library.md), section 8).
- **The order of publishing:** the release on GitHub with slot, package and installer first, then the image, then
  PyPI. The boxes depend on the first step only.
- **Every step can be repeated.** If a later step fails, the job is run again: a tag, a release, an image or a
  version on PyPI that is there already is skipped.
- **A change of the image is a change of the software:** `deploy/docker/` and `deploy/install/` are below `deploy/`,
  which the check of [library.md](../library.md), section 8.1, counts already. Such a push needs a new version.
- **The workflow stays thin:** `scripts/build_installer.sh`, `scripts/ci/build.sh` and new scripts beside
  `release_github.sh` do the work. The job gets the rights to write packages and to prove its identity to PyPI.

## 8. Tests

| What | How |
| --- | --- |
| The installer on a system | on the stand-in for a box of `deploy/ansible/test/` – Debian trixie for aarch64 with systemd in a container: a first run from an empty system up to a service that runs its slot; a second run that changes nothing; a run with a library |
| The steps of the installer one by one | each step is a function that says whether it changed something; tested with files in a temporary folder in place of `/etc` and `/boot/firmware` (`--root`, as the slot scripts have it) |
| The library hands out installer and slot | `tests/sync`: without credentials these three answers and no other; the pin of the line fits the certificate |
| Starting with one folder | unit tests for library and box: an empty folder, a second start, each environment variable |
| The image | the workflow starts the library and a box from the image it built and waits until the box appears in the library |
| The wheel | the workflow installs it into an empty environment and starts the library with `--data` |

What no container shows – firmware settings, the sound card, Bluetooth, the reboot in the middle – is tried once on
a Pi with a fresh card, with the acceptance checklist of [deployment.md](../deployment.md), section 8.

## 9. Documents

Where this concept goes once it is implemented; the concept is deleted then.

| Document | Gets |
| --- | --- |
| user manual and quick guide, English and German | "Setting up a box" with the line; "The library with Docker"; in the chapter "Library": adding a box |
| page texts, `en.json` and `de.json` | the section "Add a box"; the hint that a box needs the installer |
| `README.md` | the three ways at the top: the line, `docker compose`, `pip` |
| [deployment.md](../deployment.md) | the installer, what the playbook still does, the roles |
| [box.md](../box.md) | who sets what: Imager, installer, playbook |
| [library.md](../library.md) | section 3: the library in Docker and with `--data`; 4.13 and 4.14: what answers without credentials; 6.14: installer instead of playbook; 8: the forms of a release |
| [development.md](../development.md) | section 8: what a release publishes; the accounts and the one-time settings |

## 10. Plan

Each step is a release of its own and useful without the next.

| Step | Content | Proves |
| --- | --- | --- |
| 1 | Starting with one folder (section 4) | library and box start from the wheel, without the clone |
| 2 | The installer: script, build, file of the release; tests on the stand-in; `--token-file` | a fresh card becomes a box with one line – tried on a real Pi |
| 3 | The playbook calls the installer (if decided so) | our box is set up by the installer and passes the checklist |
| 4 | The library hands out the installer; section "Add a box" | a second box joins the library without a PC |
| 5 | The image and `compose.yaml`; the workflow publishes it | library and box run from the image |
| 6 | PyPI | `pip install` gives the same version |
| 7 | The documents of section 9 | |

Steps 5 and 6 publish to places everybody can read. They wait until the repository is public.

## 11. Decisions

### 11.1 Taken

| Decision | Reason |
| --- | --- |
| A real box gets its software from a slot only – not from the image, not from PyPI | tested versions, `rfid-reader`, and the way back |
| The installer is a shell script that runs on the box | a fresh Raspberry Pi OS has `bash` and `curl` and nothing else; Ansible on the box would be a large installation for a few dozen steps |
| One file per release with everything embedded | one line can fetch one file; installer and slot of a release always fit |
| The installer asks for passwords and takes none on the command line | the history of the shell |
| The installer leaves hostname, WLAN and SSH alone | the Imager sets them before the first start; changing SSH could lock out the person who runs the line |
| The slot scripts change only when a person runs the installer | a defective version must not be able to replace its own updater |
| A library hands out installer and slot without credentials | public files of a release, and the slot is verified by its signature; a box without a slot has nothing to register with |
| One image for library and box | the same package |
| Registry of GitHub | no further account, no stored secret |
| No Docker for the real box | GPIO, sound and Bluetooth would have to be passed into the container, and the slots already do what an image would be for |
| PyPI in addition | cheap, expected, and it keeps the name |

### 11.2 Open

| Point | Proposal |
| --- | --- |
| Does the playbook call the installer, or do both describe the setup? | the playbook calls it (section 3.10): one description, and our box is set up like every other |
| The user that runs the service on a box | the user who runs the installer, as today (`pi` on our box); `--user` for another one |
| Docker Hub in addition to the registry of GitHub | not now; a second push of the same image whenever it is wanted |
| Removing a box installation | none: the card is written anew |
