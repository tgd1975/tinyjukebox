# Tiny Jukebox – administration

For the person who sets up and runs the boxes and the library at home: with a terminal, a NAS or a PC, and a
browser. What to do, in steps, and what happens then. How the box is operated and what the web page offers is in the
[manual](manual.md); this document points to it instead of repeating it. Every detail – every command, every option,
every file – is in the developer documents ([`docs/developer/`](../../developer/operations.md), in English).

Contents:

1. [Overview](#1-overview)
2. [Setting up a box](#2-setting-up-a-box)
3. [The library](#3-the-library)
4. [Updates](#4-updates)
5. [Users and access](#5-users-and-access)
6. [Data and backups](#6-data-and-backups)
7. [Logs and diagnostics](#7-logs-and-diagnostics)
8. [Configuration](#8-configuration)
9. [If something does not work](#9-if-something-does-not-work)

---

## 1. Overview

### 1.1 What runs where

| What | Where it runs | Its page | Its data |
| --- | --- | --- | --- |
| Box | a Raspberry Pi in the wooden case | `https://<name of the box>.local`, e.g. `https://tinyjukebox.local` | `/srv/tinyjukebox/` on its SD card |
| Library on the NAS | the Synology package `tinyjukebox-library` | `https://<nas>:8866` | `/var/packages/tinyjukebox-library/var/` and the shared folder `tinyjukebox` |
| Library in Docker or on a PC | the image `ghcr.io/tgd1975/tinyjukebox`, or `tinyjukebox-library --data DIR` | `https://<computer>:8866` | the volume `/data`, or the folder `DIR` |
| App | an Android phone: a phone as a box, or the page of the library | – | on the phone ([manual](manual.md#137-the-app-a-phone-as-a-box), section 13.7) |

A box works alone. With a library the boxes share albums, tags, stations and covers, and the library keeps the
progress of every box ([manual](manual.md#13-library), section 13). A box without a library fetches new versions of
its software from GitHub; a box with a library fetches them from the library.

### 1.2 Roles

- `admin` may do everything: albums, tags, stations, users, backups, versions.
- `user` may operate the box – status, player, volume, buttons – and change the own password; on the library also make
  a tag known by scanning it.

A box and the library each have their own users. Users and passwords on the page:
[manual](manual.md#11-users-and-passwords), section 11.

### 1.3 The files of a box

| Folder | Content |
| --- | --- |
| `/srv/tinyjukebox/albums/` | one folder per album: MP3 files and `playlist.m3u` |
| `/srv/tinyjukebox/json/` | tags, albums, stations, progress, volume limits; `images/`: the covers |
| `/srv/tinyjukebox/ssl/` | certificate of the page and `users.yaml`, the users |
| `/srv/tinyjukebox/log/` | `tinyjukebox.log`, the log of the box |
| `/srv/tinyjukebox/slots/`, `current` | the versions of the box software and the one that runs |
| `/srv/tinyjukebox/state/` | which version runs and why; `history.log`, one line per switch |
| `/srv/tinyjukebox/identity/`, `sync/` | the key of the box and the address of its library; what it exchanges with the library |
| `/srv/tinyjukebox/spotify/` | key, player and login of Spotify |
| `/srv/tinyjukebox/config/` | `tinyjukebox.yaml`, if there is one (section 8) |

All of it belongs to the user of the box, `pi` on our box. The complete list:
[box.md](../../developer/box.md#6-file-system-layout), section 6.

---

## 2. Setting up a box

### 2.1 What is needed

- a Raspberry Pi with the hardware of the box ([box.md](../../developer/box.md#1-hardware), section 1) – a box that is
  not wired yet can be installed as well; its page answers, and the log names what is missing
- an SD card and the Raspberry Pi Imager on the PC
- a WLAN with internet: the installer fetches the packages of the operating system
- the password the user `admin` of the page of the box shall have, at least 8 characters
- with a library: its bootstrap password, set when the library was installed (section 3)

### 2.2 Writing the card

1. In the Raspberry Pi Imager choose "Raspberry Pi OS Lite (64-bit)".
2. In the settings of the Imager set the hostname (e.g. `kitchen` – the page is then `https://kitchen.local`), the
   user, the WLAN with its country, the time zone and SSH; the Imager offers "public-key authentication only".
3. Write the card, put it into the box and switch the box on.
4. Log in from the PC: `ssh <user>@<hostname>.local`.

The installer never touches what the Imager set: hostname, user, WLAN, time zone, SSH.

### 2.3 The installer

A box that works alone gets the installer from GitHub:

```bash
curl -fsSL https://github.com/tgd1975/tinyjukebox/releases/latest/download/install.sh | sudo bash
```

A box for a library gets its line from the page of the library: tab "Boxes", section "Add a box", "Copy". The line
names the library and the key of its certificate, so that the box trusts exactly this library.

What happens:

- **All questions come first:** the password of the user `admin` of the page and, with a library, the bootstrap
  password. They are read from the terminal, never from the command line.
- The installer checks that it runs on Raspberry Pi OS Lite (64-bit), Debian 13. On anything else it stops before
  it changes something and says what it found.
- It sets up the system, installs the box software into a slot and prints the address of the page. It restarts the
  box once. On a Pi 3 it takes a few minutes; nothing is compiled.
- A box for a library registers at its first start and appears in the tab "Boxes" of the library by itself.
- What it printed is kept in `/var/log/tinyjukebox-install.log`, also what happened after the restart.

**Running it again** is safe: it changes nothing that is right already, and it never touches albums, tags, progress,
users or the link to a library. It is also the way to bring the system of a box up to date (section 4.4). There is
no uninstaller: a Pi is freed by writing its card anew.

All options of the installer: [deployment.md](../../developer/deployment.md#51-with-the-installer), section 5.1.

### 2.4 After the installation

- The first start should happen with WLAN: the box has no clock, and the certificate of the page is created then.
- Open the page and log in as `admin`. The browser warns about the certificate the first time (section 5.4).
- Choose the language under the login, "Deutsch / English".
- Place a tag: the LED turns yellow, and the tab "Now" offers to link it ([manual](manual.md#72-linking-a-tag),
  section 7.2).
- If there are several boxes, give the box a name: tab "System", section "Library", "Rename".

For a box that was set up anew, the [acceptance checklist](../../developer/deployment.md#8-acceptance-checklist) goes
through start, buttons, LED, tags and headset one by one.

### 2.5 With the playbook

Our own box is set up from the PC with an Ansible playbook, which runs the installer and also writes the config of
the box and its users. That is the way for a developer who keeps the box in the repository:
[deployment.md](../../developer/deployment.md#52-with-the-playbook), section 5.2.

---

## 3. The library

### 3.1 Where it runs

| Where | How | Updated |
| --- | --- | --- |
| Synology NAS | the package `tinyjukebox-library`, installed in the Package Center | by hand, with a newer package (section 4.3) |
| Docker | the image `ghcr.io/tgd1975/tinyjukebox` (`linux/amd64`, `linux/arm64`) | by pulling a newer image |
| PC | `pip install tinyjukebox`, then `tinyjukebox-library --data DIR` | with `pip install --upgrade tinyjukebox` |

The package is built for one platform, `rtd1296` – the DS418j and NAS with the same processor. Another NAS needs
another build, or Docker.

### 3.2 On the NAS

1. **Before:** install the Python 3.12 of SynoCommunity on the NAS (the package `python312`). Choose a bootstrap
   password of at least 16 characters, e.g. with `openssl rand -hex 24`. Every box proves it once, the first time it
   connects.
2. **Fetch the package** (`tinyjukebox-library-<version>-<build>-rtd1296.spk`) from the newest release on GitHub. With
   a clone of the repository on the PC, `./fetch_release.sh` fetches it into `dist/` and checks it.
3. **Install it:** Package Center of DSM, "Manual Install", choose the file, confirm the warning about packages of
   third parties. The dialog asks for the password of the user `admin` of the page (at least 8 characters) and for
   the bootstrap password.
4. **Check:** the Package Center shows the package as running, and `https://<nas>:8866` opens the page. Log in as
   `admin`. If the start failed, the reason is in `var/server.log` (section 7.2).
5. **Users:** the library has the user `admin` only. Add the others on its page, tab "System", section "Users".

The package runs as a user of its own, `tinyjukebox-library`. DSM creates the shared folder `tinyjukebox` at the first
start, with `albums/`, `images/` and `json/` in it; albums can also be put there with SMB or File Station, and Hyper
Backup includes the folder. Certificate, users and config live in
`/var/packages/tinyjukebox-library/var/`, which an update keeps and which DSM never removes – also not when the
package is removed. To start from nothing: [library.md](../../developer/library.md#37-first-installation-on-the-nas),
section 3.7.

### 3.3 In Docker

Two passwords as files, then compose starts a library and a box without hardware to try:

```bash
cd deploy/docker && openssl rand -hex 12 > admin-password && openssl rand -hex 24 > bootstrap-password
docker compose up -d   # https://localhost:8866/ and https://localhost:8443/, user admin, the password in admin-password
```

The library alone:

```bash
docker run -d --name library --restart unless-stopped -p 8866:8866 \
    -v library:/data -v /path/to/albums:/data/albums ghcr.io/tgd1975/tinyjukebox
```

- Everything that must survive lies below `/data`: one volume, and if wanted the albums as a folder of the host. A
  mounted folder must be writable for the user of the image (uid 1000).
- Without `TINYJUKEBOX_ADMIN_PASSWORD` the password of `admin` is generated and printed once: `docker logs library`.
- The bootstrap password comes from `TINYJUKEBOX_BOOTSTRAP_PASSWORD`, or it is set in the tab "Boxes".
- Instead of the passwords themselves, `TINYJUKEBOX_ADMIN_PASSWORD_FILE` and `TINYJUKEBOX_BOOTSTRAP_PASSWORD_FILE`
  name a file that holds them; a variable can be read with `docker inspect`.
- A box joins with the line of the tab "Boxes". Its address must be the one of the host, not the name of the
  container.

More: [library.md](../../developer/library.md#39-the-library-in-docker-and-with-one-folder), section 3.9.

### 3.4 On a PC

```bash
pip install tinyjukebox
tinyjukebox-library --data ~/library        # https://localhost:8866/
```

Everything lies below the folder: `data/`, `albums/`, `images/`, `ssl/`, `log/`, `backups/`. At the first start it
asks for the password of `admin` on the terminal. A computer that is switched off is no library for the boxes: they
play on with what they have and deliver their changes later.

### 3.5 Filling it with the data of a box

The library starts empty. If a box has albums and tags already, a command on the PC brings them over. It keeps the
ids, so that box and library agree from the start, and it can be repeated: what is there already is skipped.

```bash
rsync -a pi@tinyjukebox.local:/srv/tinyjukebox/json/ ~/box-copy/json/
rsync -a pi@tinyjukebox.local:/srv/tinyjukebox/albums/ ~/box-copy/albums/
tinyjukebox-fill-library https://<nas>:8866 --user admin \
    --json ~/box-copy/json --albums ~/box-copy/albums --images ~/box-copy/json/images
```

It asks for the password and shows the fingerprint of the certificate of the library for confirmation (section 5.4).
In a clone of the repository the command is `uv run tinyjukebox-fill-library …`. The progress comes from the box
itself with its first synchronization. A box that is simply connected to the library brings its data as well
([manual](manual.md#132-connecting-a-box-to-the-library), section 13.2); this command is for many albums at once.

### 3.6 Connecting the boxes

- **A box that runs already:** on its page, tab "System", section "Library": the address and the bootstrap password
  ([manual](manual.md#132-connecting-a-box-to-the-library), section 13.2).
- **A new box:** the line of the tab "Boxes", section "Add a box" (section 2.3).
- **A box that was set up again** is a new box. On the page of the library, tab "Boxes", it takes over the progress
  and settings of the old one.

### 3.7 New versions for the boxes

The library fetches the versions of the box software from the releases on GitHub and hands them to its boxes. For
that it needs access to the releases:

- **The repository is public:** an empty file `var/github-token` on the NAS is enough, owned by the user
  `tinyjukebox-library`; then stop and start the package.
- **The repository is private:** create a fine-grained token on GitHub for this one repository with the right
  "Contents: Read-only", and enter it on the page of the library, tab "Versions", section "Access to the releases".
  The library checks it before it keeps it and looks for a release at once.

The Docker image and `tinyjukebox-library --data` ask the releases of the project without a token. Which version a
box gets and how it is tried: section 4.1.

---

## 4. Updates

### 4.1 The box software

A box installs new versions by itself: with a library from the library, without one from GitHub. A new version starts
the next time the box is switched on, on probation. If it does not work – it does not start, or it fails its first
seconds – the box starts the version before after a few seconds by itself. A version stays on probation until a tag
was placed and an album played because of it.

- **Without a library** the box looks once a day for a new release and installs it after a waiting time of three days;
  a correction in that time replaces it before any box got it.
- **With a library** a box gets a new version at once. In the tab "Versions" of the library every version has a state:
  `use` (boxes install it by themselves), `manual` (by hand only), `dontuse` (every box leaves it at once).
- **The update mode** of a box – "automatic", "by hand only" ("ask" without a library) or "off" – is set on its page,
  tab "System", section "Version" ([manual](manual.md#134-version-of-the-box), section 13.4).

On the box the same is a command. It runs as the user of the box, never with `sudo`:

```bash
tinyjukebox-slot status           # the versions on the box, the one that runs, the mode, the last switches
tinyjukebox-slot check            # what the library offers - without a library: GitHub
tinyjukebox-slot update --now     # the newest permitted version, at once
tinyjukebox-slot update 0.45.0    # exactly this version
tinyjukebox-slot mode manual      # automatic, manual or off
```

Every call: [library.md](../../developer/library.md#68-the-command-tinyjukebox-slot), section 6.8.

### 4.2 Going back to the version before

A step back takes effect at once, also while something is playing. No box installs the version that was left by
itself any more.

| Way | For |
| --- | --- |
| page of the box, tab "System", "Back to the previous version" | this box |
| `tinyjukebox-slot revert` on the box | this box |
| an empty file `tinyjukebox-revert` on the boot partition of the SD card (`bootfs` on the PC) | this box, without network: the next start goes back and writes `tinyjukebox-ergebnis.txt` in its place |
| page of the library, tab "Versions": the version to `dontuse` | all boxes |

`revert` switches to the other slot, whatever it holds. After a failed probation that may be the version that failed:
look first with `tinyjukebox-slot status`. If no version starts any more, the box is in emergency mode
([manual](manual.md#134-version-of-the-box), section 13.4;
[library.md](../../developer/library.md#610-fallback-slot-and-recovery-mode), section 6.10).

### 4.3 The library

The library never updates itself.

- **On the NAS:** install the newer package the same way, Package Center, "Manual Install" – without the dialog; the
  data stays. From a clone of the repository on the PC, `./update_library.sh <nas>` does it: it fetches the package of
  the newest release and asks for user and password of DSM, which it does not store.
- **In Docker:** its page shows that a newer version exists. Pull the newer image and start the container again;
  with compose `docker compose pull`, then `docker compose up -d`.
- **On a PC:** `pip install --upgrade tinyjukebox`, then start the library again.

A library that is older than the version of a box makes the box stop: the LED changes between red, white, magenta and
yellow, and the box plays nothing. Then update the library first.

### 4.4 The operating system of the box

The box never updates its operating system by itself. By hand, now and then:

```bash
sudo apt update && sudo apt full-upgrade
```

After an upgrade of Python, ALSA or BlueZ, run the installer line again (section 2.3) – on a box of the playbook
`./update_box.sh` – and check sound, buttons and headset. A version that needs a newer setup of the system says so on
the page of the box: then the installer has to run again as well.

---

## 5. Users and access

### 5.1 Users from the command line

On the page an admin adds and removes users and sets passwords ([manual](manual.md#11-users-and-passwords),
section 11). On the box, as the user of the box:

```bash
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml list
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml add NAME --role admin
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml passwd NAME
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml role NAME admin
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml remove NAME
```

- Only here can a role be changed. The command removes neither the last admin nor makes them a `user`.
- A change takes effect at once, without a restart. A new password ends the other sessions of that user.
- For a library on a PC the file is `ssl/users.yaml` in its folder. In Docker the image runs the command, with the
  volume of the library: `docker run --rm -v library:/data ghcr.io/tgd1975/tinyjukebox tinyjukebox-users -f
  /data/ssl/users.yaml list`.
- The package on the NAS has no command `tinyjukebox-users`: the call with the Python of the package is in
  [operations.md](../../developer/operations.md#4-users-of-the-web-page), section 4.

### 5.2 A forgotten password

1. Another admin sets a new password on the page, tab "System", section "Users".
2. If no admin knows a password any more: log in on the box with SSH.
3. `tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml passwd admin` – it asks for the new password twice.
4. Log in on the page with it.

### 5.3 SSH

The box is reached with `ssh <user>@<hostname>.local`, as the Imager set it up. If the name is not found, the address
of the box in the router helps. A card that was written anew has a new key: if the PC still knows the old one,
SSH refuses until the old entry is removed with `ssh-keygen -R <hostname>.local`.

### 5.4 The certificate of the page

Box and library make their own certificate, at their first start. The browser warns about it the first time.

- Compare the fingerprint the browser shows with the one in the log: the box writes it when the page starts
  (`rest api listening on … fingerprint`), the library into its log as well.
- Or import the certificate of the box as trusted: `/srv/tinyjukebox/ssl/tinyjukebox.crt`.
- The commands on the PC, `tinyjukebox-backup` and `tinyjukebox-fill-library`, show the fingerprint too and ask
  before they go on.

---

## 6. Data and backups

### 6.1 What a backup holds

A backup is one file with the data of a box or of the library, made while it runs:

- a box: tags, albums, stations and entries of Spotify, progress, volume limits and the covers
- the library: the catalog, the progress and settings of every box, the statistics, the states of the versions and
  the covers
- with "MP3s" also the albums – the file is then as large as all albums together

A box with a library needs no backup of its own: the library has everything, and a box that is set up again fetches it
from there.

### 6.2 Making backups

| Way | Box | Library |
| --- | --- | --- |
| page, tab "System", card "Backup" | "Download backup", "Download backup with MP3s" | the same |
| page, "Back up regularly" | a box without a library, into `/srv/tinyjukebox/backups` | into the shared folder `tinyjukebox` on the NAS, which Hyper Backup includes |
| command on the PC | `tinyjukebox-backup https://tinyjukebox.local --user admin [--albums] [FOLDER]` | `tinyjukebox-backup https://<nas>:8866 --user admin [--albums] [FOLDER]` |

Two schedules, one without and one with the albums, with their folder and how many are kept:
[manual](manual.md#112-back-up-regularly), section 11.2. The backups of a box lie on its SD card: against a broken card
only a backup helps that was fetched from it.

### 6.3 Putting a backup back

On the page, tab "System", card "Backup", "Put a backup back" – or `tinyjukebox-backup … --restore FILE` on the PC.
The page asks once, then the box or the library starts again with the data of the backup
([manual](manual.md#113-putting-a-backup-back), section 11.3).

- A backup without the albums leaves the albums folder alone. Albums the data names but whose folder is missing are
  listed after the restore.
- On a box with a library the library wins at the next synchronization. A restore is meant for a box without a
  library and for the library.
- The library on another NAS, or by hand without the program:
  [operations.md](../../developer/operations.md#103-putting-a-backup-back), section 10.3.

### 6.4 What no backup holds

- users, passwords, the certificate, tokens – in the library also `settings.json` with the bootstrap password
- the key of the box and its link to the library
- the key, the player and the login of Spotify: the key is pasted again, the box fetches the player again and is
  connected again ("Connect")
- the config file

---

## 7. Logs and diagnostics

### 7.1 The logs of a box

| Log | Content |
| --- | --- |
| page, tab "Log" (admin) | the last 5000 entries, live, with filters for level and text |
| `/srv/tinyjukebox/log/tinyjukebox.log` (+ `.1` … `.4`) | the complete log; it stays over a restart |
| `journalctl -u tinyjukebox` | warnings and errors, and what the start script says; gone after a restart |
| `/srv/tinyjukebox/state/history.log` | one line per switch of version: when, from, to, why, by whom |
| `/var/log/tinyjukebox-install.log` | what the installer did |

The service itself: `sudo systemctl status tinyjukebox`, `sudo systemctl restart tinyjukebox`. After a crash systemd
starts it again by itself.

### 7.2 The logs of the library

| Log | Content |
| --- | --- |
| page of the library, tab "Log" | the log of the library, and that of every box: warnings, errors and events the boxes sent, with filters; "Save as file" |
| NAS: `/var/packages/tinyjukebox-library/var/library.log` | the log of the library; at the start its fingerprint |
| NAS: `var/server.log` | why a start failed |
| NAS: `/var/log/packages/tinyjukebox-library.log` | what the scripts of the package printed |
| Docker: `docker logs library` | the output of the library, the generated password of `admin` |

The tab "Log" of the library helps when a box is not at hand: the boxes send their warnings and errors to the
library, without passwords, keys or tag UIDs. A box that cannot start reports that too; the tab "Boxes" then shows
"cannot start since …" ([library.md](../../developer/library.md#422-central-diagnostics), section 4.22).

### 7.3 More detail in the log

The box logs at the level INFO. For a search, set it to DEBUG:

1. A box of the installer has no config file of its own. Copy the one of its slot first:
   `cp /srv/tinyjukebox/current/config/tinyjukebox.yaml /srv/tinyjukebox/config/`.
2. In `/srv/tinyjukebox/config/tinyjukebox.yaml` set `log-level: DEBUG`.
3. `sudo systemctl restart tinyjukebox`.
4. After the search, remove the copy again and restart the service: from then on the file would be the config of
   every version, also of every later one.

### 7.4 Buttons, LED and reader

The tab "System" has the card "Hardware" for admins: what the drivers say about the buttons, the LED and the RFID
reader, twice a second. If a button has no effect, it shows whether the button arrives at the box at all. Checking the
hardware by hand on the console: [operations.md](../../developer/operations.md#92-check-the-hardware-by-hand),
section 9.2.

---

## 8. Configuration

### 8.1 Where the file is

| Box or library | Config |
| --- | --- |
| box of the installer | the config of its slot, `/srv/tinyjukebox/current/config/tinyjukebox.yaml`; it comes with every version |
| box of the playbook | `/srv/tinyjukebox/config/tinyjukebox.yaml`, written by every run of the playbook |
| library on the NAS | `/var/packages/tinyjukebox-library/var/library.yaml` |
| `--data DIR` (Docker, PC) | built in; `DIR/config.yaml`, if there is one, changes single values |

Most of what an admin sets is on the page and needs no file: volume limits ([manual](manual.md#10-limiting-the-volume),
section 10), the library of a box, update mode, backups. A change of the file takes effect after
`sudo systemctl restart tinyjukebox`.

### 8.2 Keys an admin changes

| Key | Meaning | On the box |
| --- | --- | --- |
| `logger/log-level` | how much the log says | `INFO` |
| `tinyjukebox/rest-api/session-timeout` | logout after this many seconds without a request | `604800` (7 days) |
| `tinyjukebox/rest-api/idle-timeout` | logout after this many seconds in which nothing was done on an open page; `0`: never | `1800` (30 minutes) |
| `tinyjukebox/rest-api/max-upload-size` | MB per uploaded MP3 file | `500` |
| `tinyjukebox/sync/keep-free` | MB that stay free on the card of a box with a library; when less is free, the albums not played for the longest time go | `500` |
| `tinyjukebox/backups-folder` | where a box without a library keeps its backups, until another folder is set on its page | `backups` beside the albums |

Every key: [configuration.md](../../developer/configuration.md#4-section-tinyjukebox), sections 4 to 6, and on the
documentation site the generated table of all keys (tab "Reference", "Configuration keys"). Unknown keys stop the
start: the message names the path of the key.

---

## 9. If something does not work

The manual lists what a user can do ([manual](manual.md#12-if-something-does-not-work), section 12). For an admin:

| Symptom | Look at | Cause and action |
| --- | --- | --- |
| LED stays red | `sudo systemctl status tinyjukebox`, `journalctl -u tinyjukebox` | the service does not run or fails on start, e.g. an invalid config: the message names the path |
| No tag is recognized | the log: `RFID - …` | the line says why: the reader cannot be started, it does not answer (wiring, supply), or the program of the version is not trusted – then run the installer again |
| Web page not reachable | `sudo systemctl status tinyjukebox` | the service does not run; or the name `….local` is not found: use the address of the box |
| LED red, white, magenta, yellow in turn; nothing plays | tab "System"; `tinyjukebox-slot status` | the library is too old for the version of the box: update the library (section 4.3), or go back on the box (section 4.2) |
| The box does not get a new version | `tinyjukebox-slot status`, `tinyjukebox-slot check`; tab "Versions" of the library | update mode "by hand only" or "off"; the version is not `use`; the box went back from it before; the library has no access to the releases (section 3.7) |
| The box runs an older version again | `/srv/tinyjukebox/state/history.log`, tab "Boxes" of the library | the new version failed its probation and the box went back; the line says why |
| The page of the box says the library is not connected | tab "System", section "Library"; the log | the NAS is off or not in this WLAN: the box plays what it has and delivers its changes later |
| The library does not start | `var/server.log` on the NAS; `docker logs library` | the line says why, e.g. `var/library.yaml` is missing |

More symptoms, with the lines of the log: [operations.md](../../developer/operations.md#93-symptoms), section 9.3.
