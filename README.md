# Tiny Jukebox

RFID-controlled music box for children: put a tag (a disc, a figure, a card – anything with an RFID chip) on the box,
and the linked album continues where it stopped last time. The tag is only ever read, never written; the UID is all
that counts.

## What Tiny Jukebox does especially well

- **One library, any number of boxes.** `tinyjukebox-library` runs on a NAS or the PC and holds the tags, albums,
  radio stations and covers. Every box keeps a copy, fetches the albums it lacks, plays without the library,
  and the library shows what was played.
- **Updates with a way back.** A push to `master` that changes the software becomes a release. The library
  fetches it, a box tries the new version at its next start and goes back to the one before by itself if it fails.
- **Set up by a playbook, run like an appliance.** An Ansible playbook turns a fresh Raspberry Pi OS into a box; the
  library is a package for a Synology NAS. Backups run by a schedule, users and roles are managed on the web page.
- **Everything on one web page.** Link tags (also by scanning them with a phone), upload albums, set radio
  stations and volume limits, change covers – over HTTPS, in English and German, with a REST API behind it.
- **Bluetooth headset as a first-class way to listen.** Pairing with a button gesture, own start volume and maximum,
  pause when the connection is lost, headset buttons.
- **Reads reliably.** The RC522 is read by a small C++ program that recovers by itself after a crash or a reset of
  the chip, and playback keeps its position over a power cut.

## Software and hardware

The software is one Python process (one systemd service) with device drivers, state machines, player and web page
that exchange messages over an in-process queue – no broker, no further services. State machines run tags, album
playback, radio and volume. Playback is `ffmpeg | aplay`: the position is saved, with next/previous, pause and
internet radio stations.

**The software does not need a Raspberry Pi or a particular amplifier.** Our own box happens to be a Raspberry Pi 3
with an RC522, four buttons, an RGB LED and a Pimoroni Audio Amp SHIM; that is the platform we built and tested, not a
requirement of the application.

- **Sound:** any ALSA device and mixer control, set in the config. The Audio Amp SHIM is not mentioned in the code.
- **Core:** state machines, player, web page, REST API and library are plain Python on Linux; they run on a PC
  without any hardware (`--no-hardware`), which is also how the tests run.
- **Drivers:** buttons, LED, RFID reader and Bluetooth are drivers selected and wired in the config
  ([drivers.md](docs/developer/drivers.md)); the pins are config, not code.
- **RFID reader:** the driver starts any program that prints `ready`, `tag <UID>` and `removed`, one per line
  ([rfid-reader.md](docs/developer/rfid-reader.md)). The C++ `rfid-reader` for the RC522 on a Raspberry Pi is the one
  we ship; another reader or another board needs only such a program and the `command` of the driver.
- **What is tied to the Pi today:** the shipped GPIO drivers (`rpi-lgpio`) and `rfid-reader` (SPI through bcm2835).
  Bluetooth uses BlueZ and BlueALSA, which are not specific to the Pi.

The box runs the released version of its slot and works with the library on the NAS. The web page and the user
documentation are English and German; code and developer documentation are English.

## History

The first box was built for Christmas 2019: RFID, presence detection and four buttons, from the start. The
circuit and the printed parts are in [`hardware/`](hardware/README.md); the software has been rebuilt several times
since. The [changelog](CHANGELOG.md) tells the story from 2018 on.

## Documentation

### For users (English and German)

| Document | Content |
| --- | --- |
| Quick guide: [English](docs/user/en/quick-guide.md), [Deutsch](docs/user/de/quick-guide.md) | text of the quick guide card (A6, two sides): tags, buttons, LED, headset; web page |
| Manual: [English](docs/user/en/manual.md), [Deutsch](docs/user/de/manual.md) | the full manual: operating the box, LED, pause, Bluetooth headset, web page, managing albums, tags and radio stations, volume limits, users, troubleshooting |

### For developers, administrators and hardware (English)

| Document | Content |
| --- | --- |
| [Architecture](docs/developer/architecture.md) | components, state machines, volume, player, headset logic, data model, messages, framework, threads, latency measurement |
| [Drivers](docs/developer/drivers.md) | driver framework, buttons, LED, RFID driver, Bluetooth stack and driver |
| [rfid-reader](docs/developer/rfid-reader.md) | the C++ program that reads the RC522: interface, read loop, design decisions, build, tests |
| [REST API and web page](docs/developer/rest-api.md) | server, authentication, roles, endpoints, server-sent events |
| [Configuration](docs/developer/configuration.md) | command line, all keys of the config file |
| [The box](docs/developer/box.md) | hardware, pins, operating system, audio, file layout, service, robustness, decisions |
| [Library, synchronization and self-update](docs/developer/library.md) | the library on the NAS or the PC with its package, synchronization of the boxes, the phone as reader, self-update with slots, play statistics, release pipeline, test bed, tests |
| [Deployment](docs/developer/deployment.md) | how software and setup reach the box, Ansible playbook, roles, variables, first setup, updates, changes on the box, acceptance checklist |
| [Operations](docs/developer/operations.md) | service, logs, users, albums, console, latency, diagnostics, backups |
| [Development](docs/developer/development.md) | setup, the box on the PC, repository layout, dependencies, tests, quality checks, releases |
| [Open points and roadmap](docs/developer/roadmap.md) | what is not verified yet, known gaps, envisioned changes |

Reference files: [example config](docs/config.example.yaml), [config of the box](deploy/config/tinyjukebox.yaml),
[config schema](docs/config_schema.json). Hardware: [circuit and printed parts](hardware/README.md),
[key data and data sheets](docs/hardware/README.md).

## Quick start

On the PC, without hardware:

```bash
uv sync --all-groups
cp docs/config.example.yaml my-config.yaml                      # adjust the paths
uv run tinyjukebox -c my-config.yaml --no-hardware --no-audio   # web page: https://localhost:8443/
```

Checks:

```bash
uv run pytest --cov                     # tests (need ffmpeg; no sound card, no Pi hardware)
uv run ruff format --check . && uv run ruff check . && uv run mypy
```

Library and box on the PC: `./start_library.sh` and `./start_sim.sh`
([library.md](docs/developer/library.md), section 3.8).

Bring a change to the box: raise the version, add a section to `CHANGELOG.md`, push to `master`. The workflow
publishes the release and the box installs it by itself ([development.md](docs/developer/development.md),
section 8). System setup and config of the box come from the playbook:

```bash
./update_box.sh
```

Prerequisite: [uv](https://docs.astral.sh/uv/). The project uses Python 3.13 (package: ≥ 3.11).

## Licence

The code of this repository is under the MIT licence ([`LICENSE.txt`](LICENSE.txt)); for the circuit see
[`hardware/README.md`](hardware/README.md).

**The files of a release contain software of others** that is not in the repository: the slot archive for the boxes
(`tinyjukebox-box-…`) and the package of the library for the NAS (`tinyjukebox-library-…`), which brings a slot along.

| Software | In | Licence |
| --- | --- | --- |
| Python ([python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | slot | PSF-2.0; the libraries built into it have their own licences |
| mutagen | slot, package | **GPL-2.0-or-later** |
| jsonschema with attrs, referencing, rpds-py and jsonschema-specifications; PyYAML | slot, package | MIT |
| tantamount, asyncscheduler (our own) | slot, package | MIT |
| typing-extensions | package | PSF-2.0 |
| dbus-fast | slot | MIT |
| rpi-lgpio | slot | BSD-3-Clause |
| lgpio | slot | Unlicense (public domain) |
| pyalsaaudio | slot | PSF-2.0 |
| bcm2835, linked into `rfid-reader` | slot | **GPL-3.0** |
| RPi-MFRC522, compiled into `rfid-reader` | slot | Unlicense (public domain) |

Python and the Python packages of others bring their licence texts along (`python/lib/python3.13/LICENSE.txt`, the
folders `*.dist-info/licenses/`); those of bcm2835 and RPi-MFRC522 are in the slot next to `rfid-reader`
(`bin/rfid-reader-licenses/`). The sources: `uv.lock` names the Python packages and their versions,
`deploy/ansible/group_vars/all/main.yaml` the versions of bcm2835 and RPi-MFRC522 and where they come from,
`cpp/rfid-reader/` is the source of `rfid-reader` itself. This is a description, not a legal opinion.

## Repository

| Folder | Content |
| --- | --- |
| `src/tinyjukebox/`, `tests/` | the application and its tests |
| `cpp/rfid-reader/` | the RFID reader program of the box (C++), built with the slot of a release; `licenses/` holds the licence texts of the two libraries it is built with |
| `deploy/` | config, service units, the slot scripts of a box, the Ansible playbook that sets up the box, the package of the library for the NAS |
| `scripts/` | builds the slot of a release, the steps of the workflow, checks of both web pages |
| `*.sh` in the root | `start_sim.sh`, `start_library.sh` (box and library on the PC), `update_box.sh` (playbook), `build_package.sh`, `fetch_release.sh`, `update_library.sh` (the package onto the NAS), `release.sh` (push, merge, install), `import_covers.sh` |
| `docs/` | documentation; `docs/hardware/` holds the key data of the components and the links to their data sheets |
| `hardware/` | the circuit of the box and printed parts |

The repository holds everything that belongs to the box and changes together. It has one version number
(`pyproject.toml`), set as git tag `vX.Y.Z`. What is deliberately not in the repository is listed in
[development.md](docs/developer/development.md), section 3.
