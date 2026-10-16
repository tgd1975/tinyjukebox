# Configuration

Command line and config file of `tinyjukebox`, the application of a box. The config of the library
(`tinyjukebox-library`) is described in [library.md](library.md), section 3.8.

| File | Use |
| --- | --- |
| [`deploy/config/tinyjukebox.yaml`](../../deploy/config/tinyjukebox.yaml) | the config of the box, with drivers; the playbook copies it to `/srv/tinyjukebox/config/tinyjukebox.yaml`. Every slot brings the file of its version and uses it only if the box has none |
| [`deploy/config/tinyjukebox-sim.yaml`](../../deploy/config/tinyjukebox-sim.yaml) | the box on the PC without hardware (`./start_sim.sh`) |
| [`deploy/config/library-sim.yaml`](../../deploy/config/library-sim.yaml) | the library on the PC (`./start_library.sh`) |
| [`docs/config.example.yaml`](../config.example.yaml) | commented example without drivers |
| [`docs/config_schema.json`](../config_schema.json) | the complete JSON schema, e.g. for completion in the editor |

Tests check the configs of the box against the schema in the code, and that `config_schema.json` matches it.

## 1. Command line

```bash
tinyjukebox -c /srv/tinyjukebox/config/tinyjukebox.yaml            # with drivers and interactive console
tinyjukebox -c /srv/tinyjukebox/config/tinyjukebox.yaml --no_gui   # as the service runs it
tinyjukebox -c config.yaml --no-hardware [--no-audio] [--webserver | --no-webserver]
tinyjukebox --data DIR --no-hardware [--no-audio]                    # without a config file of its own
tinyjukebox --version
```

| Option | Effect |
| --- | --- |
| `-c CONFIG` | config file; one of `-c` and `--data` is needed |
| `--data DIR` | a box without a config file of its own: everything below `DIR` (below) |
| `-v` / `-q` | more console output (INFO) / none; mutually exclusive |
| `--no_gui` | no interactive console |
| `--hardware` (default) | drivers of the section `drivers` and the ALSA mixer. Without the section the box starts without drivers, with a warning |
| `--no-hardware` | no drivers, no ALSA mixer; virtual volume, virtual Bluetooth headset. Tags, buttons, headset and LED are operated and shown by the web page |
| `--no-audio` | tracks run in real time but without sound (no sound card needed; ffmpeg is still required) |
| `--webserver` / `--no-webserver` | web page and REST API on/off, regardless of `rest-api/enabled`. Without a section `rest-api`, certificate and users are in `~/.config/tinyjukebox/` |

**Starting with one folder** (`--data DIR`, also `tinyjukebox-library --data DIR`): what the Docker image and the
package from PyPI use. The config is built in, with everything below `DIR` – in the folders box and library use
anyway: `json/` with empty data files, `albums/`, `ssl/`, `log/`, for the library `data/`, `images/` and `backups/`.
The web page is on (box: port 8443; library: 8866). A file `DIR/config.yaml`, if there is one, changes single values;
`-c` and `--data` exclude each other. Environment variables count at the first start only – afterwards the folder is
the truth, and passwords are changed on the page:

| Variable | At the first start |
| --- | --- |
| `TINYJUKEBOX_ADMIN_PASSWORD` | the password of the user `admin` of the page, if it has no user yet. Without it: asked on the terminal, or – without a terminal – generated and written to the log once |
| `TINYJUKEBOX_BOOTSTRAP_PASSWORD` | library: the bootstrap password new boxes prove (otherwise set in the tab "Boxes"). Box: the one it proves to its library |
| `TINYJUKEBOX_LIBRARY` | box: the address of its library; later it is changed on the page |
| `TINYJUKEBOX_SLOTS` | library: the folder with the slot it brings along (the Docker image) |

A library started this way asks the releases of the project for the versions of its boxes (`aarch64-debian-trixie`).
A box started this way has no slots and never updates itself. The Synology package, `start_library.sh` and
`start_sim.sh` keep their config files.

End: `Ctrl-C`, `exit` in the console, SIGTERM or SIGHUP. All of them stop the service regularly (LED red, position
saved).

Further commands of the package (on the box in `/usr/local/bin`, except the two marked "PC"):

| Command | Task | Described in |
| --- | --- | --- |
| `tinyjukebox-users` | users of the web page | [operations.md](operations.md), section 4 |
| `tinyjukebox-import-cards` | cut the pictures of cards out of a presentation | [operations.md](operations.md), section 6 |
| `tinyjukebox-import-covers` (PC) | set titles and covers of albums and covers of radio stations from a folder on the PC | [operations.md](operations.md), section 5 |
| `tinyjukebox-migrate-01` | convert the data of the box 0.1 | [deployment.md](deployment.md), section 7 |
| `tinyjukebox-fill-library` | send tags, albums, stations and images to a library that is empty | [library.md](library.md), section 3.5 |
| `tinyjukebox-backup` (PC) | fetch a backup of a box or of the library; set the folders of the backups that are made by a schedule | [operations.md](operations.md), section 10 |
| `tinyjukebox-library` (PC, NAS) | the library, a program of its own: `-c CONFIG` or `--data DIR` | [library.md](library.md), sections 3, 3.8 and 3.9 |

Not part of the package: `tinyjukebox-slot` and `tinyjukebox-start`, the shell scripts that decide which version of the
box software runs ([library.md](library.md), sections 6.8 and 6.11).

## 2. Config file: general rules

A config file has the sections `logger`, `tinyjukebox` and – on the box – `drivers`.

- All keys are converted to lower case on reading.
- Only `true`/`false` are booleans; `ON`, `OFF`, `yes`, `no` stay strings (YAML 1.2).
- Unknown keys inside the sections are rejected on start. Error messages name the path, e.g.
  `'hold_timeout' is a required property (at 'drivers/1')`.
- Topics and messages are not configurable; they are constants in the code (`tinyjukebox.messages`).

Check a config without starting the box:

```bash
uv run python -c "
from tinyjukebox.core.config import read_config, validate_config
from tinyjukebox.tinyjukebox import TinyJukebox
print(validate_config(read_config('/srv/tinyjukebox/config/tinyjukebox.yaml'), TinyJukebox.get_schema()) or 'ok')"
```

## 3. Section `logger`

```yaml
logger:
    log-level: INFO               # DEBUG | INFO | WARNING | ERROR | CRITICAL
    log-file: /srv/tinyjukebox/log/tinyjukebox.log
    log-rotation:                 # optional
        maxbytes: 1048576
        backupcount: 4            # tinyjukebox.log.1 ... tinyjukebox.log.4
```

The latency measurements are INFO messages; they need `log-level` `INFO` or `DEBUG`. The box runs with `INFO`:
it is switched off by cutting the power and should write little.

A path may begin with `~`, the home folder of the user who runs the box: `log-file`, and in the section
`tinyjukebox` the keys `albums-folder`, `backups-folder`, those below `json`, and the files and the folder of `rest-api`
(`deploy/config/tinyjukebox-sim.yaml`).

## 4. Section `tinyjukebox`

| Key | Required | Meaning | Box |
| --- | --- | --- | --- |
| `albums-folder` | yes | root folder of the albums; `album-folder` in `targets.json` is relative to it | `/srv/tinyjukebox/albums` |
| `backups-folder` | no | folder of the backups that a box without a library makes by itself, until another folder is set on its page (default: `backups` beside the albums folder; [operations.md](operations.md), section 10.2) | – (`/srv/tinyjukebox/backups`) |
| `playlist-filename` | yes | name of the playlist in every album folder | `playlist.m3u` |
| `volume/max-volume` | yes | maximum volume of the speaker in % | 82 |
| `volume/default-volume` | yes | volume of the speaker on start in % | 50 |
| `volume/volume-step` | yes | change per button press in % (speaker and headset) | 5 |
| `volume/volume-step-hold` | no | change in % per `BUTTON_HELD` of a volume button that is kept pressed (default 1). How often is set by `hold_timeout` and `hold_repeat` of its `input` driver | 1 (every 0.2 s) |
| `volume/mixer-control` | no | ALSA control of the default sound card (default `Master`; list: `amixer scontrols`) | `Softmaster`, the software control of `asound.conf` – the Audio Amp SHIM has none of its own |
| `bluetooth/default-volume` | no | volume of the headset on every connect in % (default 40) | 40 |
| `bluetooth/max-volume` | no | maximum volume of the headset in % (default 75; 100: the box does not limit) | 75 |
| `albumplayer/rewind-max-time` | yes | seconds within which "prev" goes to the previous track | 10 |
| `albumplayer/update-schedule` | yes | save the position every n seconds (> 0) | 5 |
| `albumplayer/pause-between-tracks` | yes | pause between two tracks in seconds (≥ 0) | 0 |
| `albumplayer/buffer-time` | no | ALSA buffer of `aplay` in ms (20–2000); without it the default of `aplay` (mostly 500 ms). Smaller: the sound starts sooner; if the sound stutters, raise it | 200 |
| `json/tags`, `json/targets`, `json/activities` | yes | data files | `/srv/tinyjukebox/json/…` |
| `json/volume` | no | start values and maxima changed at run time (default: `volume.json` next to `json/tags`) | default |
| `rest-api` | no | web page and REST API, section 5 | |
| `sync/folder`, `sync/identity` | no | synchronization with the library ([library.md](library.md), section 4.2): copy, outbox and journal; id and key of the box, address of the library (default: folders `sync` and `identity` next to the folder of `json/tags`) | default: `/srv/tinyjukebox/sync`, `/srv/tinyjukebox/identity` |
| `sync/retry-seconds` | no | how often the library is tried again while it cannot be reached (default 30) | default |
| `sync/keep-free` | no | MB that stay free on the card of a box that works with a library (default 500; 0: nothing is deleted). When less is free, the audio files of the albums not played for the longest time are deleted; the library has them | default |
| `sync/stage1-seconds`, `sync/stage2-seconds` | no | health check of a new version on a box with slots (defaults 10 and 60, [library.md](library.md), section 6.4) | default |
| `sync/look-hours` | no | a box with slots and without a library: hours between two looks for a new version at the releases (default 24, [library.md](library.md), section 6.16) | default |

Spotify has no key here: the key, the player, the login and the switch of the speaker are in the folder `spotify` next
to the folder of `json/tags` (`/srv/tinyjukebox/spotify`) and are set on the page ([architecture.md](architecture.md),
section 4.1).

The speaker values 50 % and 82 % reproduce the levels of the previous box (127 and 210 of 255 on the same software
control).

**Start value and maximum at run time:** `default-volume` and `max-volume` of speaker and headset can be changed on
the web page (role `admin`) and in the console (`set_volume_limits`). Changed values are stored in `volume.json` and
from then on apply instead of the config, also after a restart. Constraint: 0 ≤ start value ≤ maximum ≤ 100. A
lowered maximum lowers a higher current volume at once; a new start value takes effect on the next start or connect.

## 5. Section `tinyjukebox/rest-api`

| Key | Default | Meaning |
| --- | --- | --- |
| `enabled` | `true` | web page and API on/off |
| `host` | `0.0.0.0` | address to listen on |
| `port` | `8443` | HTTPS port. The box uses `443`; ports below 1024 need `AmbientCapabilities=CAP_NET_BIND_SERVICE` in `tinyjukebox.service` |
| `certificate`, `private-key` | `~/.config/tinyjukebox/tinyjukebox.crt/.key` | PEM files; if missing, a self-signed certificate is created |
| `hostnames` | `[]` | additional names/IPs for the self-signed certificate |
| `users-file` | `~/.config/tinyjukebox/users.yaml` | users and password hashes |
| `images-folder` | folder `images` next to `tags.json` | the covers of the radio stations ([operations.md](operations.md), section 6) |
| `session-timeout` | `604800` (7 days) | logout after this many seconds without a request |
| `idle-timeout` | `1800` (30 minutes) | logout after this many seconds in which the user of the page did nothing; `0`: never. An open page asks all the time, so it tells with every request when something was done on it last ([rest-api.md](rest-api.md), section 1) |
| `max-upload-size` | `500` | MB per uploaded MP3 file |

## 6. Section `drivers`

A list of drivers; used only with `--hardware`.

```yaml
drivers:
    -   name: volume_up
        type: input
        active: true
        button: volume-up
        pin: 6
        …
    -   name: rgbled
        type: rgbled
        active: false          # not loaded
        …
```

- Every entry needs `type` (`input`, `rgbled`, `rfid`, `bluetooth`), `name` and `active`.
- The id of a driver is its `name` in lower case without spaces; for duplicates `_0`, `_1` … is appended. It is the
  prefix of its console commands.
- All keys of a driver are required, except `command` of the RFID reader, `hold_repeat` of `input` and the keys of
  the Bluetooth driver. Further keys are rejected.

Behaviour and internals of the drivers: [drivers.md](drivers.md).

### `input`

```yaml
-   name: volume_up
    type: input
    active: true
    button: volume-up               # volume-up | volume-down | next | prev (also play | pause - the box has none)
    pin: 6                          # BCM; internal pull-up, pressed = low
    hold_timeout: 0.2               # seconds until BUTTON_HELD
    hold_repeat: true               # optional (default false): repeat BUTTON_HELD every hold_timeout seconds
```

### `rgbled`

```yaml
-   name: rgbled
    type: rgbled
    active: true
    pin-red: 22
    pin-green: 27
    pin-blue: 17
    initial-color: RED              # BLACK, WHITE, RED, GREEN, BLUE, YELLOW, AQUA, MAGENTA
    physical-closed-red: low        # level that switches the color on
    physical-closed-green: high
    physical-closed-blue: high
```

### `rfid`

```yaml
-   name: rfid
    type: rfid
    active: true
    pins:                           # BCM numbers
        pin_rst: 26
        pin_cs: 8                   # SPI0 CE0
    command: [sudo, -n, /usr/local/bin/rfid-reader]   # optional, this is the default
```

The driver appends the pins to `command` as `--pin-rst 26 --pin-cs 8`.

### `bluetooth`

```yaml
-   name: bluetooth
    type: bluetooth
    active: true
    adapter: hci0                   # optional (default hci0)
    avrcp: player                   # optional: player (default) | input
    play-pause-toggle: false        # optional (default false)
```

| Key | Meaning |
| --- | --- |
| `adapter` | Bluetooth adapter |
| `avrcp` | path of the headset buttons. `player`: the box registers as a media player with BlueZ and receives the buttons as calls. `input`: BlueZ passes them to the Linux input device "… (AVRCP)", which the driver reads (the user of the service then needs the group `input`) |
| `play-pause-toggle` | `true`: play and pause of the headset both switch between playing and pause – for headsets that send the same button for both |

The box runs with the defaults (`player`, `false`).
