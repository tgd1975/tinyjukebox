# Development

Working on the code: setup, running the box on the PC, repository layout, tests, quality checks, releases.

## 1. Setup

Prerequisites: [uv](https://docs.astral.sh/uv/), `ffmpeg`; optional `g++` and `dbus-daemon` for two groups of tests.
`.python-version` pins Python 3.13 (the system Python of Raspberry Pi OS trixie); the package requires ≥ 3.11.

| Task | Command |
| --- | --- |
| set up the environment | `uv sync --all-groups` |
| tests | `uv run pytest` |
| with coverage and durations | `uv run pytest --cov --durations=10` |
| format / check the format | `uv run ruff format .` / `uv run ruff format --check .` |
| lint | `uv run ruff check .` |
| type check | `uv run mypy` |
| build the package | `uv build` → `dist/tinyjukebox-<version>.tar.gz` and `…-py3-none-any.whl` |
| update the schema file | `uv run python -c "from tinyjukebox.tinyjukebox import TinyJukebox; TinyJukebox.dump_schema('docs/config_schema.json')"` |

All commands run in the root of the repository.

## 2. The box on the PC

```bash
cp docs/config.example.yaml my-config.yaml                      # adjust the paths
uv run tinyjukebox -c my-config.yaml --no-hardware --no-audio   # web page: https://localhost:8443/
```

- `--no-hardware`: no drivers. Tags are placed in the tab "Tags", buttons are on the page, the LED is shown
  next to the logo, and the tab "Bluetooth" has a virtual headset (admins).
- `--no-audio`: tracks run in real time without sound.
- The hardware libraries are missing on the PC. That does not matter: drivers are loaded only when the config asks
  for them and `--hardware` applies.
- `./start_sim.sh [--no-audio]` starts the box with `deploy/config/tinyjukebox-sim.yaml` (data below
  `~/tinyjukebox-sim`) without hardware and with the web page; further options are passed on to `tinyjukebox`.
- `./start_library.sh` starts the library with `deploy/config/library-sim.yaml` (data below `~/tinyjukebox-library`);
  how the box on the PC is connected to it is in [library.md](library.md), section 3.8.

## 3. Repository layout

```text
tinyjukebox/
├── pyproject.toml              metadata, dependencies, build (hatchling), pytest/ruff/mypy/coverage
├── .python-version             3.13
├── CHANGELOG.md                one section per version; a release takes its text from it
├── start_sim.sh                the box on the PC
├── start_library.sh            the library on the PC
├── update_box.sh               runs the playbook for the box (docs/developer/deployment.md)
├── build_package.sh            the package of the library for the NAS
├── fetch_release.sh            the files of a release from GitHub into dist/, checked
├── update_library.sh           installs the package of a release on the NAS, through the web API of DSM
├── release.sh                  checks, push, master forward, waits for the workflow, update_library.sh
├── dsm_vault.sh                address, user and password of DSM from the vault of the playbook, for the two above
├── import_covers.sh            tinyjukebox-import-covers for a folder of covers and the address of the box
├── scripts/                    build_slot.sh, build_slot_aarch64.sh, fetch_python.sh, check_release.sh,
│                               build_installer.sh, build_image.sh, build_app.sh, trust.sh (the keys of DSM
│                               and of the library that the scripts on the PC accept),
│                               ci/ (release_needed, build, release_github, release_notes, test_image,
│                               publish_image, publish_pypi),
│                               pages/ (check.sh: both web pages in a browser),
│                               site/ (assemble.py, build.sh: the documentation site, section 10),
│                               help/ (build.py: the help of the web pages from docs/user, rest-api.md section 5)
├── app/                        the app for Android phones (Gradle, Kotlin): sync, app, probe (section 9)
├── src/tinyjukebox/
│   ├── tinyjukebox.py          service TinyJukebox, command line, signals (console command tinyjukebox)
│   ├── control.py              BoxControl: all operations for REST API and console
│   ├── cli.py                  commands of the interactive console (text output for BoxControl)
│   ├── slots.py                Slots, Health: version of the box software and its health check
│   ├── sync/                   a box and the library: client.py, handshake.py, manifest.py, util.py; the side of a
│   │                           box: data.py, link.py, files.py, box.py (docs/developer/library.md)
│   ├── library/                the library: store.py, journal.py, statistics.py, releases.py, schema.py,
│   │                           diagnostics.py (the logs the boxes deliver), service.py (tinyjukebox-library)
│   ├── backup.py, backups.py   a backup; the backups made by a schedule (docs/developer/operations.md, section 10)
│   ├── restore.py              putting a backup back (docs/developer/operations.md, section 10.3)
│   ├── data_folder.py          box and library with one folder, --data DIR (docs/developer/configuration.md)
│   ├── diagnostics.py          what the box logs for the central diagnostics of the library
│   ├── errors.py               ApiError and subclasses (HTTP status)
│   ├── images.py               the covers of albums, stations and entries of Spotify (the images folder)
│   ├── metadata.py             ID3 data, length, cover (mutagen)
│   ├── latency.py              ReactionTimer
│   ├── messages.py             topics and payload types of all messages
│   ├── rgbled.py               RGBLed: LED patterns, overlay with expiry
│   ├── volumemanager.py        volume: limits, steps, ALSA or virtual mixer, profiles (Volumes)
│   ├── headset.py              Headset: output, volume profile and pause follow the Bluetooth headset
│   ├── core/                   framework: microservice.py, config.py, logging/, pubsub/ (LocalQueue), threads.py,
│   │                           unexpected.py, ui/ (console), schema/
│   ├── devices/                drivers: baseclasses/ (ADriver, AEvents), drivers/ (input, rgbled, rfid, bluetooth),
│   │                           bluetooth/ (bluez, link, avrcp_input, virtual), drivermanager/, schema/
│   ├── stm/                    base.py (SafeMachine, mermaid export), stmoperation.py, stmplayalbum.py,
│   │                           playradio.py, playspotify.py, stmvolume.py
│   ├── albumplayer/            player_interface.py, aalbumplayer.py, simpleplayer.py, radioplayer.py,
│   │                           spotifyplayer.py, dummyplayer.py, clock.py
│   ├── spotify/                Spotify with Soloist: account.py (key, player, login), engine.py (the one Soloist),
│   │                           soloist.py, sound.py (its PipeWire and the pipe), oembed.py (name and cover)
│   ├── data/                   jsondata.py, tag(s).py, target(s).py, activity.py, activities.py, volumelimits.py,
│   │                           targetalbumtools.py, sanitycheck.py, degraded.py, common.py
│   ├── schema/                 JSON schemas: section tinyjukebox, tags/targets/activities/volume
│   ├── web/                    server.py, auth.py, users.py (tinyjukebox-users), certificates.py, uploads.py,
│   │                           logbuffer.py, web/ (index.html, app.js, style.css, favicon.svg,
│   │                           texts/<language>.json: every text of the page)
│   └── tools/                  import_cards.py (tinyjukebox-import-cards), migrate_box01.py
│                               (tinyjukebox-migrate-01), fill_library.py (tinyjukebox-fill-library),
│                               import_covers.py (tinyjukebox-import-covers), backup.py (tinyjukebox-backup)
├── tests/                      section 5
├── cpp/rfid-reader/            RFID reader program (C++): src/main.cpp, Makefile, tests/stub/MFRC522.h; licenses/
│                               (the licence texts of bcm2835 and RPi-MFRC522, which go into the slot)
├── deploy/
│   ├── config/                 tinyjukebox.yaml (box), tinyjukebox-sim.yaml (PC), library-sim.yaml (PC), asound.conf
│   ├── scripts/                tinyjukebox.service, backup_json.sh, install_service.sh
│   ├── slot/                   tinyjukebox-slot, tinyjukebox-start, tinyjukebox-functions, tinyjukebox.service
│   │                           (box with slots)
│   ├── synology/               the package of the library: INFO, conf/, scripts/, WIZARD_UIFILES/, package/
│   ├── release.pub             public key of the releases
│   ├── install/                the installer of a box: install.sh, the setup after the reboot, rfid-reader-launcher
│   │                           (rfid-reader as root, checked); test/ (run.sh: the installer on a stand-in for a box)
│   ├── docker/                 the image: Dockerfile, entrypoint.sh, compose.yaml, example.env
│   └── ansible/                playbook: site.yaml, group_vars/, roles/ (base, tinyjukebox, install, rfid, migrate)
├── docs/
│   ├── user/                   users and administrators: quick-guide.md (one card), manual.md, admin.md; one
│   │                           folder per language (de, en)
│   ├── developer/              developer, admin, hardware (English)
│   │   └── concept/            concepts: what is planned and not implemented yet (merged into the documents and
│   │                           deleted once it is implemented)
│   ├── config.example.yaml     example config (checked against the schema in a test)
│   ├── config_schema.json      complete schema (a test detects deviations from the code)
│   ├── hardware/               data sheets: RC522, Audio Amp SHIM
│   ├── site/                   tinyjukebox.css: the colours of the documentation site (section 10)
│   ├── images/                 photos of the box (2019 and today), the Tinkercad model, the logo: its SVG files are
│   │                           the source of favicon.svg and of the icons of the package for the NAS
│   └── style-guide/            name, logo, colours, type, shapes, LED and writing (README.md, colour charts)
└── hardware/                   circuits/ (the circuit of the box: Fritzing file, picture, parts list),
                                3d-parts/ (STL files with a picture each)
```

The package uses the `src/` layout and is marked as typed (`py.typed`). The repository has one version number for
the whole (`pyproject.toml`), set as git tag `vX.Y.Z`.

Deliberately not in the repository:

| What | Where |
| --- | --- |
| library for the RC522 | repository [RPi-MFRC522](https://github.com/tgd1975/RPi-MFRC522); the box builds against the tag `1.4.5-tgd.1` |
| library bcm2835 | downloaded by the box from airspayce.com; the checksum is in the playbook |
| albums, `tags.json`, `targets.json`, `activity.json` | data of the box, only there below `/srv/tinyjukebox/` |
| WLAN access, users of the web page | on the box and in the vault file of the playbook on the PC |
| covers and titles of albums (`album-<id>.jpg`, `captions.json`: album id → image and title) and covers of radio stations (`radio-<id>.jpg`), set on a box with `tinyjukebox-import-covers` | a folder outside the repository: the images are cover art |

## 4. Dependencies

| Package | Use | Condition |
| --- | --- | --- |
| `tantamount` | state machines (brings `AsyncScheduler`) | – |
| `mutagen` | MP3 length, ID3 tags, cover | – |
| `jsonschema` | validation of config and JSON files (draft 7) | – |
| `PyYAML` | config (SafeLoader, CSafeLoader if available), users file | – |
| `segno` | the QR code for pairing a phone and for the link to the app (pure Python, BSD-3-Clause) | – |
| `rpi-lgpio` | module `RPi.GPIO` (via lgpio; edge detection also from kernel 6.6 on) | extra `box`, ARM only |
| `pyalsaaudio` | volume through the simple mixer API of alsa-lib (source package only, needs `libasound2-dev`) | extra `box`, ARM only |
| `dbus-fast` | BlueZ over D-Bus in the Bluetooth driver (asyncio, without libdbus/GLib) | extra `box`, Linux only |

- The extra `box` holds the hardware libraries of a box (`tinyjukebox[box]`, `uv sync --extra box`). The library on the
  NAS is ARM too and needs none of them, so a marker alone is not enough.
- The RFID reader needs no Python library ([rfid-reader.md](rfid-reader.md)).
- Dependency groups: `test` (pytest, pytest-cov, pytest-timeout, psutil, dbus-fast) and `lint` (ruff, mypy, type
  stubs); `dev` contains both.
- External programs: `ffmpeg` (decoding), `aplay` of alsa-utils (output), `openssl` (certificate); optional
  LibreOffice and `pdftoppm` for `tinyjukebox-import-cards`. For the headset the services `bluetooth` (BlueZ) and
  `bluealsa`.

## 5. Tests

| Level | Location | Properties |
| --- | --- | --- |
| Tiny Jukebox | `tests/unit` | state machines, player, data, web, command line; ffmpeg only for the player tests |
| framework | `tests/core/unit` | config, logging, AbstractMicroservice, pubsub/LocalQueue, schema, threads, UI |
| drivers | `tests/devices/unit` | drivers on fake hardware, DriverManager, schemas, the whole box |
| D-Bus | `tests/devices/integration` | `DbusBluez` with real D-Bus messages on a private `dbus-daemon`; skipped without `dbus-daemon` |
| RFID reader | `tests/devices/integration` | driver `rfid` with the real `rfid-reader`, built against a stub of the RC522 library; skipped without `g++` |
| library and boxes | `tests/sync` | a library and boxes in one process over HTTPS; the slot scripts on a folder ([library.md](library.md), section 10); the tests with the scripts are skipped without `bash`, `jq` and `flock` |
| the app | `app/sync/src/test` | JVM tests of the sync module of the app, one against a real library (section 9); not part of `pytest` |

All tests run without sound card, without Pi hardware and without Bluetooth. They need `ffmpeg`. One test builds a
slot archive and needs the network the first time; it is skipped if that fails. How many there are:
`uv run pytest --collect-only -q | tail -1`, with the folder of a level (e.g. `tests/sync`) for that level.

pytest runs with `--strict-markers`, `--strict-config`, `--import-mode=importlib`, `filterwarnings = error` and a
60 s timeout per test.

**Test doubles and helpers:**

- **`tests/support.py`:** `SyncPubSub` (delivers synchronously in `publish`), `FakePubSub` (records; `deliver()`
  delivers one message synchronously) and `LoopbackPubSub` (also delivers own messages, like the LocalQueue);
  `ManualClock` (clock of the players; `advance()` fires due timers in the test thread); `FakeRGBLed`;
  `create_library`/`box_config` (tags, targets, activities and a valid config in `tmp_path`); `ApiClient`.
- **`tests/conftest.py`:** autouse fixtures for leftover threads, silent audio output (null sink instead of `aplay`)
  and cleaning up the root log handlers; fixtures `pubsub`, `local_queue`, `library`, `clock`. If non-daemon threads
  still run at the end, `pytest_unconfigure` ends the run with exit code 1 – otherwise Python would wait for them
  forever on exit.
- **`tests/devices/fakes.py`:** `FakeGPIO` (`RPi.GPIO`: levels, direction, pull, edge callbacks, cleanup history;
  `set_input(pin, level)` calls the callback like the GPIO callback thread). `tests/devices/conftest.py` puts it
  into `sys.modules` before a driver module is imported – always, also on the Pi. `FakeRfidReader` is `rfid-reader`
  as a process controlled by the test through a FIFO (`ready()`, `place(uid)`, `remove()`, exit code on request).
  `FakeBluez` replaces `DbusBluez` for the driver tests: the test sets devices (like signals of BlueZ, passed
  thread-safely into the loop of the driver), calls are recorded; `FakeMixer` is the control of the headset.
- **`tests/devices/bluezservice.py`:** `FakeBluezService`, a replica of BlueZ (object manager, adapter, devices,
  agent manager, media) on the name `org.bluez` of a private `dbus-daemon`.
  `tests/devices/integration/test_bluez_dbus.py` runs `DbusBluez` against it – signatures, signals, agent and player
  calls are real D-Bus messages. The Bluetooth of the machine is never touched.
- **`tests/core/support.py`:** `ExampleService` for the life cycle tests.

**`tests/devices/unit/test_box.py`** starts the whole box: `TinyJukebox` with the drivers of
`deploy/config/tinyjukebox.yaml` on fake GPIO, fake `rfid-reader` and fake BlueZ, state machines and player on a
`LocalQueue`. It checks that all parts share one queue, that a tag starts and stops its album (including the latency
measurement), that a button changes the volume, that the console offers the driver commands, and that
`--no-hardware` creates no drivers.

**Real processes** stay where they are the subject of the test: player tests (ffmpeg), stress tests of the
concurrency (process count with psutil), command line and signals (subprocess), HTTPS with a real certificate.

**Configuration tests** check `tests/data/config.yaml`, `docs/config.example.yaml` and
`deploy/config/tinyjukebox*.yaml` against the schema, that `docs/config_schema.json` matches the schema in the code,
and that the config of the box has exactly one driver per button and one rfid, rgbled and bluetooth driver each.

**Outside pytest:** two checks are run by hand: the installer on a stand-in for a box
(`deploy/install/test/run.sh`) and both web pages in a browser (`scripts/pages/check.sh`;
[library.md](library.md), section 10). The workflow tries the image on every push (`scripts/ci/test_image.sh`). The
slot scripts of `deploy/slot/` were proven in a test bed with Docker containers, which is not kept;
[library.md](library.md), section 9, describes it and how to build it again.

**Not covered by automated tests:** the hardware (GPIO, SPI, RC522, sound card), real BlueZ/BlueALSA with a real
headset, the Ansible roles, the installer on a real Pi, DSM with the package of the library, and a phone as reader.
These are covered by the acceptance checklist ([deployment.md](deployment.md), section 8) and listed in
[roadmap.md](roadmap.md), section 1, while they are open.

## 6. Quality checks

- **ruff** (format and lint, line length 120) with rule sets for pycodestyle, pyflakes, imports, names, pyupgrade,
  bugbear, builtins, comprehensions, simplifications, pytest, logging, security (bandit), pylint and RUF. Switched
  off: `PLR0913/PLR0917` (constructors get their collaborators injected), `PLR2004`, `S603/S607` (external programs
  as argument lists from the PATH), `RUF001` (German texts), `RUF022`.
- **mypy** `strict` over `src` and `tests`, checked against Python 3.11. Exceptions: tantamount and the hardware
  library `RPi` have no type information; in tests untyped functions are allowed. The untyped parts of mutagen are
  encapsulated in `metadata.mp3_info`.
- Logging uses `%s` placeholders throughout; `print` only in console commands and command line tools.
- **CI** (`.github/workflows/ci.yml`, GitHub Actions): the job `test` installs ffmpeg and dbus, runs `uv sync
  --locked --all-groups`, then the same checks as locally: format, lint, mypy and the tests with coverage (summary in
  the job, `coverage.xml` as artifact), with Python 3.13 and with 3.12, the Python of the NAS. Job timeout: 15
  minutes. The job `app` runs the JVM tests of the app (section 9). The job `build` builds the slot, the app and
  the package on every push – signed with the keys of the releases on `master` only, on another branch with keys made
  for the run – and may only read; the job `publish` publishes a release on `master` from what `build` built
  (section 8; [library.md](library.md), section 8.2). Every action is pinned to a commit; dependabot proposes the next
  ones (`.github/dependabot.yml`).
- The Ansible playbook is checked with `ansible-playbook --syntax-check` and `ansible-lint`
  (`uvx --from ansible-lint ansible-lint`), by hand.

## 7. Conventions

- Code, comments, commit messages and developer documentation are English.
- The user documents exist in German and English: `docs/user/<language>/quick-guide.md` and `manual.md`. Every
  language has the same sections with the same numbers and the same tables, and quotes the labels of the page as
  the page of that language shows them. A change of what the user sees changes all languages in the same commit; a
  test fails if their structure differs (`tests/unit/test_documents.py`).
- The web page is English and German. Its texts are never written into `index.html` or `app.js`: they are in
  `web/web/texts/de.json` and `en.json` ([rest-api.md](rest-api.md), section 5). A new or changed text is written
  in both files in the same commit; a test fails if a key is missing in one of them.
- A further language is: `web/web/texts/<code>.json` with every key and an entry in `LANGUAGES` of `app.js`; the
  folder `docs/user/<code>/` with both documents; and for DSM the keys `displayname_<lng>` and `description_<lng>`
  in `deploy/synology/INFO` and the file `WIZARD_UIFILES/install_uifile_<lng>` (`ger` is German). Everything else
  stays English: log, console, commands, REST API, developer documents, README, CHANGELOG.
- German texts are not germanized by force: a word that is English in daily use stays English – Tag, Box, Upload,
  Backup, Pairing, Log, Token, Slot. English texts use the words of this table:

  | German | English |
  | --- | --- |
  | Tag | tag |
  | Box | box |
  | Bibliothek | library |
  | Album, Titel | album, track |
  | Radiosender, Sender | radio station, station |
  | Titelbild | cover |
  | Kopfhörer | headset |
  | verknüpfen, Verknüpfung lösen | link, unlink |
  | auflegen, abnehmen | place, take off |
  | Fortschritt | progress |
  | Hochladen | upload |
  | Notfall-Version | emergency version (fallback version in the developer documentation) |
  | Notfall-Modus | emergency mode (recovery mode in the developer documentation) |
  | Jetzt (tab) | Now |

- The state machine diagrams in [architecture.md](architecture.md) are generated with `stm.base.mermaid_notation`;
  regenerate them when a machine changes.
- `docs/config_schema.json` is generated (section 1); a test fails if it drifts from the code.
- What the user sees is documented in the user manual and the quick guide; everything else in the developer
  documents. A change of behaviour updates the affected ones.
- Features are not removed as a side effect of a refactoring.

## 8. Versions and releases

The version is only in `pyproject.toml`; `tinyjukebox.__version__` reads it from the package metadata. It is always
`X.Y.Z`: the boxes compare versions with `sort -V`, which would put `0.21.0rc1` after `0.21.0`, and the workflow
refuses any other form.

1. Raise the version in `pyproject.toml`, run `uv lock`.
2. Add a section for the version to `CHANGELOG.md`.
3. Push to `master`. The workflow creates the tag `vX.Y.Z` and the release with the slot for the boxes, the package
   for the NAS, the installer of a box, the app (`tinyjukebox-app-<version>.apk`), wheel and source archive, and
   `SHA256SUMS` with the checksums of these files; from a public repository also the image on `ghcr.io` and the
   package on PyPI ([library.md](library.md), section 8); nobody tags by hand. A push to `master` that changes the
   software without a higher version and a section in the changelog fails; on another branch it is a warning, and the
   branch is built without a release.
4. The library finds the release and fetches the slot; a box with slots installs it by itself and tries it on its
   next start. A box without slots runs `master`, not a tag: `./update_box.sh` ([deployment.md](deployment.md),
   section 6).
5. The library on the NAS is updated by hand when it has to be: `./update_library.sh [<nas>]` fetches the package
   of the newest release and installs it, or `./fetch_release.sh` and the Package Center of DSM
   ([library.md](library.md), section 3.7).

`./release.sh [<nas>]` does steps 3 to 5 for the commits of the current branch: it stops before the push if version
or changelog are missing, runs the checks of the workflow, pushes the branch, moves `master` forward to it
(fast-forward, no merge commit), pushes `master`, waits for the workflow and installs the package of the new
release on the NAS – the one given, otherwise the DSM of the vault of the playbook ([library.md](library.md),
section 3.7). It takes the user and the password of DSM from the vault or asks for them at the start – the first
time also whether the certificate DSM shows is the right one; its key is kept and the password sent to no other
([library.md](library.md), section 8.2). The password reaches `update_library.sh` only, not the checks and pushes
before it. With `--no-library`, or without `<nas>` and without the DSM in the vault, the library stays as it is; a
push that is no release installs nothing.

A push that changes only documentation, tests or hardware files needs none of this and leads to no release
([library.md](library.md), section 8.1).

**Which part of the version is raised** follows from what the update asks of the devices and of the people. The
number is all that the tab "Versions" of the library shows before the boxes install a version:

| Part | When | Examples |
| --- | --- | --- |
| Patch (`0.15.1` → `0.15.2`) | the software behaves as documented before: a fix, an internal change, comments or documentation in `src/` or `deploy/`. Nobody has to learn anything new | 0.9.2, 0.11.1 |
| Minor (`0.15` → `0.16`) | somebody sees or can do something new: a tab, a button, a command, a request, a key of the config with a default. Everything that was there keeps working, and box and library fit together without anybody doing anything | 0.13.0, 0.15.0 |
| Major (`0.x` → `1.0`) | somebody has to do something so that it keeps working | below |

A change is major if one of these holds:

- The interface between box and library gets a new version (`INTERFACE` in `sync/__init__.py`): the library on the
  NAS has to be updated by hand before the boxes get the version ([library.md](library.md), section 4.10).
- The box needs a run of the installer (or the playbook): a higher level of the setup or a newer slot script
  (`system` in the description of the slot; `SETUP_LEVEL`, `SCRIPT_VERSION`), a new package of the operating system.
- A format changes in a way that the version a box goes back to cannot read any more – which the rule of
  [library.md](library.md), section 4.21, avoids.
- Something is removed or renamed: a command, a request, a required key of the config.

Two rules on top:

- In doubt, the higher part.
- A fix with a consequence that somebody notices is minor – e.g. one after which the library reads every track
  once on its first start.

There is no migration path backwards: data files are migrated forward only.

## 9. The app

The app for Android phones ([app.md](app.md)) is one Gradle project in `app/` with the modules
`sync` (plain Kotlin, tested on the PC), `app` (Android) and `probe`. Needs Java 17; the Android modules need the
Android SDK (`ANDROID_HOME`, or `app/local.properties` with `sdk.dir=…`) – without it only `sync` is built. The
version comes from `pyproject.toml`.

| Command | |
| --- | --- |
| `app/gradlew -p app :sync:test` | the JVM tests; `LibraryTest` starts `uv run tinyjukebox-library --data DIR` of this repository |
| `app/gradlew -p app :sync:run --args="DIR pair 'tinyjukebox:pair?…'"` | the sync module on the command line: `pair`, `pass`, `catalog`, `fetch ALBUM`, `position …`, `journal EVENT`, `unpair`, all below the folder `DIR` |
| `app/gradlew -p app :app:assembleDebug` | the app, `app/app/build/outputs/apk/debug/app-debug.apk`; `adb install -r` puts it on a phone or the emulator |
| `scripts/build_app.sh` | the app of a release, `dist/tinyjukebox-app-<version>.apk`, signed with `APP_SIGNING_KEY` if set |
| `app/gradlew -p app :probe:assembleDebug` | the probe (section 10 of app.md) |

**In the emulator** there is no NFC and no camera: the tiles are tapped, and a debug build takes the content of the
QR code from the intent – `adb shell "am start -n io.github.tgd1975.tinyjukebox/.MainActivity --es pair '<content>'"`,
the content from `POST /api/library/pairings` with the address `https://10.0.2.2:<port>`, under which the emulator
reaches the PC. The image of Android 34 needs about 7.5 GB free for its data partition (`ANDROID_AVD_HOME` may point
to another disk).


## 10. The documentation site

The documents of the repository as one site: <https://tgd1975.github.io/tinyjukebox/>, built by MkDocs with the theme
Material. The Markdown files stay where they are and stay readable on GitHub; the site is a second view of them, not a
second source. Modelled on the site of edakit (<https://tgd1975.github.io/edakit/>).

| Command | |
| --- | --- |
| `scripts/site/build.sh` | assembles the pages into `build/site-src/` and builds the site with `--strict` into `build/site/` |
| `scripts/site/build.sh serve` | the same as a preview on <http://127.0.0.1:8000> |
| `python3 scripts/site/assemble.py` | only the assembly, plain Python without dependencies |

MkDocs 1.6.1 and Material 9.6.23 run with `uvx`, pinned in `build.sh` and updated by hand; they are no dependency of
the box or the library and not in `uv.lock`.

### 10.1 What the script does

`scripts/site/assemble.py` holds the navigation as data (`NAV`): tabs by reader – "Start", "Use" and "Bedienen" (the
quick guide and the manual, English and German), "Administer" and "Verwalten" (the administration, English and
German), "Hardware", "Develop", "Reference" (the configuration keys and the complete English documents for
administrators: configuration, operations, deployment, library), "Concepts" –, at most eight pages a tab. Users and
administrators read in both languages, developers in English (style guide, section 8.1). Every Markdown file of
the repository is on the site or named in `LEFT_OUT` (today `TODO.md`).

- **The manual and the administration are cut.** In the repository each is one file per language; on the site it is
  one page per entry of the menu, after the tables of `CUT`. The manual (`MANUAL_PAGES`): "The box" (chapters 1–5),
  "Web page and users" (6, 10, 11), "MP3 albums" (7), "Radio" (8), "Spotify" (9), "If something does not work" (12),
  "Library" (13). The administration (`ADMIN_PAGES`): "Overview" (1), "A box and the library" (2, 3), "Updates" (4),
  "Users, access and backups" (5, 6), "Configuration" (8), "Logs and troubleshooting" (7, 9). The headings and their
  numbers stay, so "section 7.4" means the same on GitHub and on the site. A new chapter needs a place in its table.
- **Links:** to a page of the site its address there, also into another page of a cut manual; an image a copy under
  `files/`; anything else the file on GitHub (`master`). Anchors are those of GitHub: `pymdownx.slugs.slugify` with
  lower case keeps umlauts (`#5-bluetooth-kopfhörer`), MkDocs' own slugs drop them.
- **German pages** are wrapped in `<div lang="de">`, so that browsers and screen readers read them as German; every page
  of "Use" links to its page of "Bedienen" and back, every page of "Administer" to its page of "Verwalten". The search
  knows English and German.
- **Generated pages:** "Configuration keys" (tab "Reference") from `docs/config_schema.json`, and the list of the
  concepts; every concept carries the note "A plan".
- **Look:** the colours of the style guide in `docs/site/tinyjukebox.css` (style guide, section 3.5); no web fonts.
  Mermaid diagrams are drawn in the browser by Material, which loads Mermaid from unpkg.com – only pages with a
  diagram: in "Develop", "Reference" and "Concepts".

### 10.2 Checks and publishing

`tests/unit/test_site.py` runs with the other tests, without MkDocs: every document on the site or left out on
purpose, no tab over eight pages, every chapter of a manual on exactly one page, every link of the assembled pages to
a page, an anchor or a file of the site. The workflow `.github/workflows/site.yml` builds the site with `--strict` on
every push and pull request and publishes it from `master`, in a job of its own with the only write permissions
(`pages`, `id-token`). The site shows `master`, so the documents of the latest release; its footer names the
version of `pyproject.toml`.

Decided on 2026-11-04: everyone may read the site, all documents; the repository becomes public for it, after a check
of its whole history for keys, tokens, passwords, private addresses and photos with location data (none found). Once,
by hand, in the repository's settings: Pages → Build and deployment → Source "GitHub Actions".
