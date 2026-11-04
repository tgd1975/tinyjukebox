# Operations

Administration of the running box: service, logs, users, data, console, diagnostics. Setup and updates are in
[deployment.md](deployment.md), the system itself in [box.md](box.md). The library on the NAS – its installation,
its files and its log – is described in [library.md](library.md), section 3.

## 1. Access

| | |
| --- | --- |
| Shell | `ssh pi@tinyjukebox.local` – key only on a box of the playbook, otherwise as the Imager set it |
| Web page | `https://tinyjukebox.local/` – self-signed certificate: compare the fingerprint in the browser with the one in the log (`rest api listening on … fingerprint`), or import `/srv/tinyjukebox/ssl/tinyjukebox.crt` as trusted |
| Files | `/srv/tinyjukebox/` ([box.md](box.md), section 6) |

## 2. Service

```bash
sudo systemctl status tinyjukebox
sudo systemctl restart tinyjukebox
sudo systemctl stop tinyjukebox        # SIGTERM: regular stop, LED red, position saved
journalctl -u tinyjukebox -f           # warnings and errors of the current boot
```

- systemd restarts the service after a crash or a failed start: first after 2 s, then with growing intervals, at
  most every 30 s, without ever giving up.
- The service starts the start script `tinyjukebox-start`, which starts the application of the active slot; what the
  start script says is in the journal.
- The service does not wait for the network.
- On a box without slots the service is the application itself, and before every start `backup_json.sh` copies the
  JSON files to `/srv/tinyjukebox/json/backup/<time stamp>/` – only if they changed since the last copy.

**Version of the box software:**

```bash
tinyjukebox-slot status                # slots with version and state, active slot, update mode, last switches
tinyjukebox-slot check                 # what the library offers - without a library: the releases
tinyjukebox-slot update --now          # the newest permitted version, at once
tinyjukebox-slot revert                # back to the version before, at once
```

The same is in the tab "System" of the web page (admin). All calls, and the ways back without network:
[library.md](library.md), sections 6.8 and 6.9. They run as `pi`, never with `sudo`: the files of the slots belong
to `pi`, and the scripts refuse to run as root.

## 3. Logs

| Log | Content |
| --- | --- |
| `/srv/tinyjukebox/log/tinyjukebox.log` (+ `.1` … `.4`) | the complete log at `log-level` (box: INFO); persistent |
| `journalctl -u tinyjukebox` | warnings and errors, and what the start script says; in RAM, lost on reboot |
| `/srv/tinyjukebox/state/history.log` | one line per switch of version: when, from, to, why, by whom |
| web page, tab "Log" (admin) | the last 5000 entries, live, with level and text filter |

For troubleshooting set `log-level: DEBUG` in `/srv/tinyjukebox/config/tinyjukebox.yaml` and restart the service. A
box of the installer has no such file and runs with the config of its slot: copy that first
(`cp /srv/tinyjukebox/current/config/tinyjukebox.yaml /srv/tinyjukebox/config/`) and change the copy – a file with the
log level only is no valid config, and no version starts with it. From then on the file is the config of every
version, also of every later one: remove it after the troubleshooting and restart the service. On a box of the
playbook the next run of the playbook writes the config anew (INFO).

## 4. Users of the web page

Roles: `admin` may do everything; `user` may operate the box (status, player, volume, buttons) and change the own
password, on the library also make a tag known by scanning it; nothing else. A box and the library each have their
own users.

| Task | Where |
| --- | --- |
| add, remove, set a password | web page, tab "System", section "Users" (admin) |
| change the own password | web page, tab "System" (every user; needs the current password) |
| change a role; the first admin | `tinyjukebox-users` on the box |

```bash
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml add NAME --role admin
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml add NAME                  # role user
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml list
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml passwd NAME
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml role NAME admin
tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml remove NAME
```

For the library on the PC the commands are the same, with its users file `~/tinyjukebox-library/ssl/users.yaml`. The
package on the NAS has no command `tinyjukebox-users`: there the module runs with the Python of the package, as the
user of the package – the file is written anew with mode 600, and the library must still be able to read it:

```bash
sudo -u tinyjukebox-library env PYTHONPATH=/var/packages/tinyjukebox-library/target/lib \
    /var/packages/python312/target/bin/python3 -m tinyjukebox.web.users \
    -f /var/packages/tinyjukebox-library/var/ssl/users.yaml list
```

Changes take effect at once, without a restart. Minimum password length: 8 characters; `--password-stdin` reads it
from stdin and comes before the command: `tinyjukebox-users -f FILE --password-stdin add NAME`. A new password – set on
the page or with `passwd` – ends every session of that user at its next request; only the session on the page in which
it was set stays. `tinyjukebox-users` neither removes the last admin nor makes them a `user` ("add another admin
first"); on the page an admin cannot remove their own user. The playbook adds the users of its vault file if they are
missing and never changes existing ones.

## 5. Albums

An album is a folder below `/srv/tinyjukebox/albums` with MP3 files and a playlist `playlist.m3u` (one file per line,
relative to the album folder; `#` lines are ignored). A box that works with a library fetches an album from it
when its tag is placed, and passes an album that was added on the box on to the library
([library.md](library.md), section 4.8). Albums are added to a box in three ways:

1. **Upload on the web page** (tab "Upload"): the playlist is created from the ID3 track numbers, otherwise from
   the file names (natural order: 2 before 10). The title is entered there, a cover image may be chosen.
2. **Copy folders** (`rsync -a ALBUM pi@tinyjukebox.local:/srv/tinyjukebox/albums/`), then "Look for new folders" in the
   tab "Albums" or `add_new_albums` in the console. The search looks for folders with a playlist and shows the title
   every album will get: the album that its first track names, otherwise the name of the folder. A missing or wrong
   playlist of a known album is regenerated with "Rebuild playlist from the MP3 files" in the album dialog.
3. **Single folder:** "Add folder as album" in the tab "Albums", `add_album FOLDER TITLE …` in the console or
   `POST /api/albums`. The title is required.

**Title and cover.** Every album has a title and may have a cover image:

- The title is stored with the album in `targets.json`. An album that was added before albums had a title shows the
  album that its first track names, otherwise the name of its folder, until a title is set.
- The cover is an image of the images folder, named in the entry of the album in `targets.json` (section 6).
  Without it the page shows an image that came with the album: `cover.jpg`, `folder.jpg`, `front.jpg` (or `.png`)
  in its folder, or the image inside the first track that has one.
- Both are changed in the album dialog (tab "Albums": click the album, edit the title, "Change cover"), in the
  console (`set_album_title`, `set_album_cover`) and with the REST API. With a library they reach it and the other
  boxes with the catalog; the image of the cover is fetched like the cover of a station.
- **Many albums at once:** `tinyjukebox-import-covers` sets titles and covers from a folder on the PC:

  ```bash
  uv run tinyjukebox-import-covers https://tinyjukebox.local /path/to/covers --user admin
  ./import_covers.sh /path/to/covers        # the same, in the root of the clone
  ```

  The folder holds the images and `captions.json`, which names image and title of an album under its id:
  `{"14": {"image": "album-14.jpg", "title": "Artist - Album (2010)"}}`. The cover of a radio station is the image
  named after its id, `radio-115.jpg`. The command shows what it will change and asks; an id that the box does not
  have is skipped, and every title that differs from the one in `captions.json` is replaced. The address may be
  the one of a box or of the library. The ids must be those of this box: against a box with other data the command
  would put the titles on the wrong albums, which is what the question before the change is for.

"Check consistency" (tab "System") or `sanity_checks` lists albums without folder, playlist or MP3 files and broken
references between tags, albums and positions.

## 6. Covers

The picture of a tag is the cover of its album, station or entry of Spotify; a tag has no picture of its own. The covers
are in the images folder of the box, `/srv/tinyjukebox/json/images/`; a cover is set on the web page or with
`tinyjukebox-import-covers` (section 5):

| File | Content |
| --- | --- |
| `album-<8 characters>.jpg` (`.png`, `.webp`) | the cover of an album, named in its entry of `targets.json` (`cover`) |
| `station-*.jpg` (`.png`, `.webp`) | the cover of a radio station, named in its entry of `targets.json` |
| `spotify-*.jpg` (`.png`, `.webp`) | the cover of an entry of Spotify, from Spotify or chosen by hand |

- Only these files count: `station-…`, `spotify-…`, or `album-` and the eight characters `0`–`9`, `a`–`f` of a generated
  name, with the extension of an image. Whatever else lies in the folder is neither shown nor sent – also not
  `album-14.jpg`, as the images for `tinyjukebox-import-covers` are named.
- With a library the covers reach the library and the other boxes: a box fetches all of them, also the cover of an
  album whose files it does not have.
- Every backup holds the images folder, so it holds the covers – with or without the albums (section 10).
- An album that names no cover shows an image that came with it (section 5). Such an image is a file of the album:
  it travels with the album and is only part of a backup with the albums.

**The cover of an album was a file of the album until 0.31**, `cover.jpg` or `cover.png` in its folder, and so
missing in every backup without the albums. 0.32 moves these stored covers into the images folder and names them in
`targets.json`:

- **The library** moves them at its start, for every album that names no cover. The file leaves the list of the
  files of the album, so the album has a new checksum once.
- **A box with a library** moves nothing. It takes over the new list of the files, removes its own `cover.jpg` and
  fetches the image; no track is fetched again. The library needs 0.32 first.
- **A box without a library** moves the stored covers of its albums at its start.
- Only `cover.jpg`, `cover.jpeg` and `cover.png` are moved. `folder.jpg`, `front.jpg` and the images inside the
  MP3 files stay where they are.
- A folder that is copied to the box with a `cover.jpg` in it shows that image at once; the next start moves it.

**The folder of the covers was `cards` until 0.28.** A box moves its covers from `json/cards/` to `json/images/` at
the first start of 0.29; what else is in `json/cards/` (card images of tags from before 0.27) stays there, is not
used any more and can be deleted. In the config of a library the key was `cards-folder`: the upgrade of the package
on the NAS renames the key to `images-folder` and leaves the folder where it is, so on that NAS the covers are still
in `shares/tinyjukebox/cards`. A library that is started by hand needs the key renamed in its config.

Images of several cards per slide are cut out of a PowerPoint file (needs LibreOffice and `pdftoppm` of
poppler-utils; better on the PC):

```bash
tinyjukebox-import-cards cards.pptx OUTPUT-FOLDER --grid 2x2 --slides 1,3,5,7 [--prefix b]
```

Every grid cell with at least one picture becomes `<slide>-<cell>-<title>.jpg`. The texts of the card become the
caption in `captions.json` (single letters – the cell marks of the template – do not count). `--prefix` keeps the
image names of several files apart (`b01-a-….jpg`). To make them covers, name each one `album-<id>.jpg` and list it in
`captions.json` as described in section 5.

## 7. Console

The service runs without a console (`--no_gui`). To use it, stop the service and start tinyjukebox in the foreground:

```bash
sudo systemctl stop tinyjukebox
/srv/tinyjukebox/current/python/bin/python3 -B -m tinyjukebox.tinyjukebox -c CONFIG -v --no-webserver
sudo systemctl start tinyjukebox                            # -v: INFO output; end with 'exit'
```

`CONFIG` is the file the service uses: `/srv/tinyjukebox/config/tinyjukebox.yaml` if it exists – on a box of the
playbook it does –, otherwise the config of the slot, `/srv/tinyjukebox/current/config/tinyjukebox.yaml`.
`--no-webserver`: port 443 needs `CAP_NET_BIND_SERVICE`, which only the service unit gives; without it the start
fails. The command `tinyjukebox` of a slot always adds `--no_gui`, hence the call of its Python. On a box without
slots it is `tinyjukebox -c /srv/tinyjukebox/config/tinyjukebox.yaml -v --no-webserver`.

`help` lists all commands, `help COMMAND` explains one. Everything except the driver commands, `show_config`,
`pubsub_stats` and `debug_threads` is also available on the web page or through the REST API
([rest-api.md](rest-api.md)).

| Command | Effect |
| --- | --- |
| `system_state` | state of the box |
| `album_info` | running album: folder, track, position, track list – or running station: name, address, state, error |
| `list_tags`, `unmapped_tags` | all tags / tags linked to neither album nor station |
| `set_tag_name UID NAME …` | name a tag |
| `link_tag_album UID\|AUTO ALBUM-ID` | link a tag to an album (asks back); `AUTO`: the placed, unlinked tag. If the tag is on the box, the album starts at once |
| `link_current_tag [ALBUM-ID]` | without ID: show free albums; with ID: link the placed tag |
| `unlink_tag UID\|CURRENT` | remove the link to album or station (asks back; the position stays); for the placed tag playback stops |
| `remove_tag UID` | delete a tag with its position (asks back) |
| `list_albums`, `unmapped_albums` | all albums / albums without a tag |
| `list_folders`, `unadded_folders` | all folders with a playlist in the albums folder / only those that are no album yet |
| `add_album FOLDER TITLE …`, `add_new_albums` | add an album with its title (a folder with blanks in quotes) / find and add all new folders with the titles shown (asks back) |
| `set_album_title ALBUM-ID TITLE …`, `set_album_cover ALBUM-ID FILE` | change the title / make an image file (JPEG or PNG) on the box the cover |
| `list_radios` | all stations with address and linked tags |
| `add_radio URL NAME …` | add a station (stream or m3u/pls playlist) |
| `set_radio_name RADIO-ID NAME …`, `set_radio_url RADIO-ID URL` | rename a station / new address; a playing station restarts |
| `set_radio_cover RADIO-ID FILE` | make an image file (JPEG, PNG or WebP) on the box the cover of a station |
| `remove_radio RADIO-ID` | delete a station (asks back; only without linked tags and while it is not playing) |
| `test_radio RADIO-ID\|URL`, `stop_radio_test` | test a station or address for one minute (only without a placed tag) / end the test |
| `link_tag_radio UID\|AUTO RADIO-ID`, `link_current_tag_radio [RADIO-ID]` | as for albums |
| `list_spotify` | all entries of Spotify with kind, link and linked tags |
| `add_spotify LINK [NAME …]` | add an entry from a link of the Spotify app; name and cover come from Spotify unless a name is given |
| `set_spotify_name SPOTIFY-ID NAME …`, `set_spotify_link SPOTIFY-ID LINK`, `set_spotify_cover SPOTIFY-ID FILE` | rename an entry / new link (a playing entry restarts) / cover from an image file on the box |
| `remove_spotify SPOTIFY-ID` | delete an entry (asks back; only without linked tags and while it is not playing) |
| `test_spotify SPOTIFY-ID\|LINK` | play an entry for one minute (only without a placed tag); `stop_radio_test` ends it |
| `link_tag_spotify UID\|AUTO SPOTIFY-ID`, `link_current_tag_spotify [SPOTIFY-ID]` | as for albums |
| `spotify_account`, `set_spotify_key KEY`, `connect_spotify`, `disconnect_spotify`, `set_spotify_speaker ON\|OFF` | the account of Spotify of the box: state, the Soloist API key, wait five minutes to be chosen in the Spotify app, remove the login, the speaker while no tag is on the box |
| `spotify_player`, `update_spotify_player` | state, build and last day of the player of Spotify / ask Spotify for a new build at once |
| `track_next`, `track_prev`, `restart_album` | as the buttons / album from the beginning |
| `track_pause`, `track_play` | pause / resume |
| `get_volume`, `set_volume 0-100` | volume (limited to the maximum) – of the headset while it is connected |
| `get_volume_limits`, `set_volume_limits speaker\|headphones START MAX` | start value and maximum (kept over restarts in `volume.json`) |
| `bluetooth_info` | paired headset: connected, battery, volume, start value, maximum |
| `bluetooth_pair`, `bluetooth_unpair` | search and pair a headset in pairing mode (replaces the previous one) / remove the pairing (asks back) |
| `library_info`, `set_library https://HOST:PORT [BOOTSTRAP-PASSWORD]`, `unlink_library YES` | synchronization with the library: address, connection, changes not confirmed yet / enter the address ([library.md](library.md), section 4.2) / leave the library ([library.md](library.md), section 4.20); without `YES` it says what would be lost |
| `system_info` | version of the box software, slots, update mode; changes are made with `tinyjukebox-slot` in a shell ([library.md](library.md), section 6.8) |
| `backup FOLDER [ALBUMS]` | write a backup of the data into a folder on the box, with `ALBUMS` also the albums (section 10) |
| `restore FILE`, `restore confirm`, `restore cancel` | put a backup back: unpack it beside the data / replace the data by it, the box starts again / leave the data as it is (section 10.3) |
| `list_activities` | saved positions |
| `rebuild_activity_json` | reset all positions (asks back) |
| `sanity_checks` | check tags, albums and positions; lists problems |
| `show_config` | current configuration (credentials masked) |
| `pubsub_stats` | subscriptions, sent and received messages per topic |
| `debug_threads` | all running threads |
| `exit`, `e`, `q`, `Ctrl-D` | end tinyjukebox |

With drivers (`--hardware` and a section `drivers`):

| Command | Effect |
| --- | --- |
| `list_drivers` | all drivers with id, type and state |
| `stop_driver ID`, `start_driver ID`, `restart_driver ID` | stop/start a driver (asks back) |
| `reload_driver ID` | replace a driver by a new instance, e.g. after hardware problems (asks back) |
| `<id>_<command>` | commands of the drivers: `rgbled_gpio_color BLUE`, `rgbled_gpio_blink GREEN BLACK 0.5`, `volume_up_gpio_state`, `bluetooth_state` (devices that BlueZ knows) |

Without hardware (`--no-hardware`):

| Command | Effect |
| --- | --- |
| `virtual_headset on\|off` | switch the virtual headset on or off (connect / connection lost); it is paired with `bluetooth_pair` |
| `virtual_headset_button play\|pause\|next\|prev\|volume-up\|volume-down` | button on the virtual headset |

## 8. Latency measurement

For every reaction the box writes one INFO line per step with the time since the trigger (logger `….latency`):

```bash
grep latency /srv/tinyjukebox/log/tinyjukebox.log | tail -40
```

| Line | Measured from … to … |
| --- | --- |
| `tag: NEW_TAG after X ms` | line of `rfid-reader` read → state machine |
| `tag: ffmpeg started after X ms` | line of `rfid-reader` read → ffmpeg runs |
| `volume button: handler after X ms` | edge of the button → handler |
| `volume button: volume set after X ms` | edge of the button → mixer set |

Not included in the chain `tag`: up to 0.2 s sampling interval of the reader, 100 ms debouncing on removal, and the
buffer of `aplay` (`albumplayer/buffer-time`, 2000 ms on the box). The box runs on battery with WLAN power saving
on, and the WLAN then delivers a radio stream with gaps of several seconds; with 200 ms Ö3 had a short pause about
once a minute. The 2 s fill at once for albums (local files decode much faster than real time) and for stations
that send a burst on connect, such as Ö3 (about 0.5 MB in the first 0.5 s); a station without such a burst starts
up to 2 s later. Stop and volume do not wait for the buffer: `stop()` ends `aplay` first, the volume is the mixer.

## 9. Diagnostics

### 9.1 The hardware on the web page

The tab "System" has the card "Hardware" for admins: what the drivers say about the buttons, the LED and the RFID
reader, read twice a second ([drivers.md](drivers.md), section 1). For a button that has no effect it tells where
to look:

| The card shows | Cause |
| --- | --- |
| the level stays "open" while the button is pressed | the button does not arrive at the pin: button, wiring, the pin in the config |
| the level changes, but the last event and the counter do not | the edges get lost: the driver or the GPIO library |
| level, event and counter follow the button | the driver does its part: look for the receiver in the log (`_handle - received`, `volume NN`) |
| "cannot be read", driver "stopped" | the driver does not run: `list_drivers` in the console, the log of the start |

### 9.2 Check the hardware by hand

1. `sudo systemctl stop tinyjukebox`, then start in the foreground with `-v` (section 7).
2. Press buttons and place tags: every message of a driver appears as
   `ADriver._publish_value - button/pressed: volume-up`; `pubsub_stats` shows the counters per topic. The box reacts
   normally meanwhile.
3. Switch the LED from the console: `rgbled_gpio_blink GREEN BLACK 0.5`.
4. `exit`. Check the reader alone (tinyjukebox must not run – two readers disturb each other):
   `sudo -n /usr/local/bin/rfid-reader --pin-rst 26 --pin-cs 8` writes `ready`, on placing `tag …`, on removing
   `removed`; end with Ctrl-C. On a box with slots this is the launcher, which starts the program of the active slot
   if it is trusted ([box.md](box.md), section 7).
5. `sudo systemctl start tinyjukebox`.

The RC522 registers can be read without root while `rfid-reader` runs, because `pi` is in the group `spi`
(`/dev/spidev0.0`, address byte `0x80 | reg << 1`; e.g. `TxControlReg` 0x14 → send `0xA8`). All registers at their
reset values (`TxControlReg` = 0x80, antenna off) mean that the reader was reset.

### 9.3 Symptoms

| Symptom | Log / check | Cause and action |
| --- | --- | --- |
| LED stays red | `systemctl status tinyjukebox`, `journalctl -u tinyjukebox` | the service does not run or fails on start (e.g. invalid config: the message names the path) |
| No tag is recognized | `RFID - … - trying again every 5 s` | `rfid-reader` cannot be started or ends: the reason is in the line (missing program, sudoers rule, SPI off) |
| No tag is recognized, `RFID - rfid-reader: rfid-reader: not started - …` | the line names the reason | the launcher does not trust the program of the active slot: a version up to 0.44 that came after the installer ran, or a program changed or built on the box. Run the installer again – it trusts the programs of the slots on the box ([box.md](box.md), section 7) |
| No tag is recognized, no error | `RFID - rfid-reader: … the reader has been reset and cannot be initialized` | the RC522 does not respond: wiring, supply |
| Occasional `the reader had been reset - initialized again` | – | the reader lost its setup and was restored. At INFO the log shows it once, and again only after a tag was placed or taken off or `rfid-reader` started anew; the repeats in between are DEBUG (section 3). If it comes with many tags, or often at DEBUG: check wiring and supply of the reader (reset line GPIO 26, 3.3 V) |
| Playback stops although the tag is on the box | `RfidState(uid=None` in the log | the tag was not read twice in a row: position of the tag on the reader, supply |
| A tag reacts only after half a minute, then the album goes on | `RFID - rfid-reader is stuck: no output for 30 s` | `rfid-reader` wrote nothing for three of its `alive` intervals (a stall in SPI, a stopped process): the driver ended it and started a new one. If it happens again: wiring and supply of the reader |
| A button has no effect | tab "System", card "Hardware" | see "The hardware on the web page" above |
| LED red, blinking | album dialog on the web page shows the problems | folder, playlist or MP3 files of the album are missing or broken |
| No sound on the speaker | `aplay -l` shows `sndrpihifiberry`? `amixer sget Softmaster` | `config.txt` (`dtoverlay=hifiberry-dac`), `/etc/asound.conf`, control missing (`aplay -q -d 1 -f cd /dev/zero`) |
| Sound stutters, radio has short pauses | `/proc/asound/card0/pcm0p/sub0/status` shows `state: XRUN` | the box has the largest `albumplayer/buffer-time` already (2000); with a smaller value raise it. Otherwise the WLAN (signal, power saving) or the load of the Pi (section 8) |
| `Bluetooth - not available: org.bluez.Error.Failed` | `rfkill list bluetooth` | adapter blocked: `sudo rfkill unblock bluetooth` |
| `Bluetooth - not available` | `systemctl status bluetooth bluealsa` | services not running, or `dtoverlay=disable-bt` in `config.txt` |
| Headset connected but the box says it is not | journal of `bluealsa`: `Couldn't select codec` | BlueALSA runs without `--a2dp-force-audio-cd` ([drivers.md](drivers.md), section 5.1) |
| Headset buttons have no effect | log shows no `player - Pause` etc. | try `avrcp: input` (group `input` for `pi`); same button for play and pause: `play-pause-toggle: true` |
| Web page not reachable | `ss -tlnp \| grep 443` | service not running; port occupied; `tinyjukebox.local` not resolved (use the IP address) |
| LED red, white, magenta, yellow in turn; nothing plays | tab "System"; `tinyjukebox-slot status` | the library is too old for the version of the box: install a newer package of the library, or give the box another version ([library.md](library.md), section 4.15) |
| The box does not get a new version | `tinyjukebox-slot status`, `tinyjukebox-slot check`; tab "Versions" of the library | update mode "manual"; the version is not `use`; the box has left this version before; the library has no token for the releases or has not looked yet ([library.md](library.md), sections 6.2 and 8); the git server answered 403: the token lacks the right "Contents: Read-only" ([library.md](library.md), section 6.16) |
| The box runs an older version again | `/srv/tinyjukebox/state/history.log`, tab "Boxes" of the library | the new version failed its health check and the box went back; the reason is in the line ([library.md](library.md), section 6.5) |
| Tab "System" says the library is not connected | `library_info` in the console, log: logger `….sync` | NAS off or not in this WLAN: the box plays what it has and delivers its changes later |

### 9.4 Bluetooth by hand

`XX:…` = address from the tab "Bluetooth" or `bluetooth_info`.

```bash
bluetoothctl show | grep -E "Powered|Discoverable|Pairable"   # yes / no / yes (invisible; the box refuses strangers)
rfkill list bluetooth                                         # 'Soft blocked: yes' -> sudo rfkill unblock bluetooth
bluetoothctl devices Paired
bluetoothctl info XX:XX:XX:XX:XX:XX                           # Connected, UUIDs (0000110b = A2DP sink, 0000110c = AVRCP target)
aplay -D bluealsa:DEV=XX:XX:XX:XX:XX:XX,PROFILE=a2dp /usr/share/sounds/alsa/Front_Center.wav
amixer -D bluealsa:XX:XX:XX:XX:XX:XX scontents                # control 'A2DP' - only while the headset is connected
grep -E "Bluetooth|HeadsetLink|DbusBluez|Headset" /srv/tinyjukebox/log/tinyjukebox.log | tail -40
```

BlueALSA remembers the volume per headset (`/var/lib/bluealsa/`); the box nevertheless sets its start value on every
connect.

## 10. Data and backups

| Data of a box | Where | Without a backup |
| --- | --- | --- |
| tags, targets, volume limits, positions | `/srv/tinyjukebox/json/*.json` | the library has them: catalog, and positions and settings per box |
| albums | `/srv/tinyjukebox/albums` | the library has all albums; a box only those it needed so far |
| covers of albums, stations and entries of Spotify | `/srv/tinyjukebox/json/images` | the library has them |
| the journal: what was played, for the statistics | `/srv/tinyjukebox/sync/journal` | the library has the lines of a box that works with it. A box without a library keeps the last 1000 lines itself, and its backup holds them |
| key, player and login of Spotify | `/srv/tinyjukebox/spotify` | never part of a backup: the key is pasted again, the box fetches the player again and is connected again ("Connect") |
| identity of the box, what the library has not confirmed yet | `/srv/tinyjukebox/identity`, `sync` | never part of a backup. A box set up again is a new box and takes over positions and settings of the old one on the page of the library ([library.md](library.md), section 4.7) |
| users, certificate | `/srv/tinyjukebox/ssl` | never part of a backup; users can be recreated from the vault file |
| config | `/srv/tinyjukebox/config/tinyjukebox.yaml` on a box of the playbook; otherwise the config of the slot, `/srv/tinyjukebox/current/config/tinyjukebox.yaml` | written by the playbook from the repository; the config of a slot comes with its version |

### 10.1 A backup

A backup is one archive of the data of a box or of the library, made while it runs (`tinyjukebox.backup`).

| | Without the albums | With the albums ("with MP3s") |
| --- | --- | --- |
| File | `tinyjukebox-box-<date>-<time>.tar.gz`, `tinyjukebox-library-….tar.gz` | `tinyjukebox-box-<date>-<time>-albums.tar`, `tinyjukebox-library-…-albums.tar` – not compressed, audio files do not get smaller |
| Box | `json/`: `tags.json`, `targets.json`, `targets-last-id.json`, `activity.json`, `volume.json`, and of a box without a library `journal.jsonl`, the lines of its journal; `images/`: the covers of albums and stations | and `albums/`: the albums folder |
| Library | `data/`: the data folder – catalog, boxes with their positions and settings, id table, manifests, broken files, the states of the box versions, the journal (a copy made by SQLite), the schedules of the backups; `images/`: the covers of albums and stations | and `albums/` |
| Size | small | as large as all albums together |

- **Nothing secret is part of a backup:** no certificate, no user of the page, no password, no token – in the
  library not `settings.json` (bootstrap password, tokens). The key of a box is in it as the library stores it, the
  hash of a long random number, which lets nobody in.
- Left out as well: what is generated (the working files `json/` of the library), the archive of the box versions
  (their entries say that the files are to be fetched again), albums that are just being uploaded, hidden files and
  the folders `@eaDir` of the NAS.
- The archive begins with `backup.json` (what it is a backup of, version, time) and ends with `complete.json`
  (number and size of the files). An archive without `complete.json` was cut off; `tinyjukebox-backup` does not keep
  such a file.
- Only admins can make a backup.

| Way | Box | Library |
| --- | --- | --- |
| Web page, tab "System", card "Backup" | "Download backup", "Download backup with MP3s": the browser saves the file; and backups that a box without a library makes by itself (section 10.2) | the same; and backups that the library makes by itself (section 10.2) |
| REST API | `GET /api/backup[?albums=1]` ([rest-api.md](rest-api.md)), and `/api/backups` | `GET /api/backup[?albums=1]`, and `/api/library/backups` |
| Command on the PC | `uv run tinyjukebox-backup https://tinyjukebox.local --user admin [--albums] [FOLDER]` | `uv run tinyjukebox-backup https://<nas>:8866 --user admin [--albums] [FOLDER]` |
| Console of the box (section 7) | `backup FOLDER [ALBUMS]` – writes into a folder on the box | – |

`tinyjukebox-backup` puts the file into `FOLDER` on the PC (default: the current folder), asks for the password like
`tinyjukebox-import-covers` and reads the file to its end before it gets its name. `--password-file FILE` reads the
password from a file, `--fingerprint` names the fingerprint of the certificate instead of showing it and asking, and
`--yes` puts a backup back (`--restore`) without asking.

### 10.2 Backups that are made by a schedule

The library, and a box that works without a library, write backups into a folder on their computer, by a schedule
or at once (`tinyjukebox.backups`). There are **two schedules**, set apart from each other: one for the backups
without the albums and one for those with them. All of it is set on the page, tab "System", card "Backup", section
"Back up regularly":

| Setting | Meaning | Default without MP3s | Default with MP3s |
| --- | --- | --- | --- |
| regularly | the schedule is on | off | off |
| every … at … | every how many days, weeks or months, and at what time of day (clock of the NAS or of the box) | every day, 03:00 | every month, 03:00 |
| … backups are kept | the oldest backups of this kind beyond this number are removed | 7 | 2 |
| folder | where these backups are kept, an absolute path that the program can write to; it is created if it is missing. Empty: the folder of the config | `backups-folder` of the config, else `backups` beside the albums folder | the same |

An example: without MP3s every week and 52 kept, with MP3s every month and 2 kept – the current one and the one
before.

- **How many are kept** counts only the backups of one kind, which the file name says (`….tar.gz` or
  `…-albums.tar`), also when both kinds are written into the same folder.
- **Months:** the same day of the month as the last backup of the schedule; a day that the month does not have is
  its last day.
- If both are due at the same time, the one without MP3s is made first.
- "Back up now" and "Back up now with MP3s" make a backup at once. It goes into the folder of its kind and counts
  for its number. The table lists the backups of both folders with their kind; each can be fetched and deleted
  there.
- The folders can also be set from the PC: `uv run tinyjukebox-backup https://<nas>:8866 --user admin
  --library-folder /volume1/tinyjukebox/backups` for the backups without MP3s, `--library-folder-albums FOLDER` for
  those with them.
- After a folder was changed, the backups of the old folder stay where they are; the page lists those of the
  folders that are set.
- A time that passes while the program does not run is not made up for: the next backup is made at the next time
  of day that is set. A backup that fails leaves no file; the page says when and why.
- **Until 0.33 there was one schedule** with a tick "with MP3s". It becomes the schedule of its kind, with its
  folder; the other one is off with the defaults.

**The library:** the default folder is on the NAS the shared folder `tinyjukebox`, which Hyper Backup includes. The
package runs as a user of its own. It can write to its shared folder `tinyjukebox`; for another shared folder this
user (`tinyjukebox-library`) needs the permission in DSM. The schedules are kept in `backups.json` of the data
folder.

**A box without a library** has the same section on its page. Its default folder is `/srv/tinyjukebox/backups`
(`backups-folder` of its config, else `backups` beside the albums folder), its schedules are kept in
`json/backups.json`, and the clock is the one of the box – without WLAN it is wrong, and a backup is then made at
another time than the one set. The backups lie on the SD card of the box like the data: against a broken card only
a backup helps that was fetched from it, or a folder on another drive.

**A box with a library** makes no backups by itself: the library has the data and makes them. The section is not
shown on its page and no schedule runs; the settings are kept and count again when the box is unlinked.

### 10.3 Putting a backup back

Putting a backup back replaces the current data by the data of the archive (`tinyjukebox.restore`). Only admins.

| Way | Box | Library |
| --- | --- | --- |
| Web page, tab "System", card "Backup", "Put a backup back" | "Choose backup …": the file is sent; without a library also "Put back" at a backup in the table of the backups (section 10.2) | "Choose backup …", and "Put back" at a backup in the table of the backups |
| Command on the PC | `uv run tinyjukebox-backup https://tinyjukebox.local --user admin --restore FILE` | `uv run tinyjukebox-backup https://<nas>:8866 --user admin --restore FILE` |
| Console of the box | `restore FILE`, then `restore confirm` or `restore cancel` | – |
| REST API | `GET`, `PUT`, `POST`, `DELETE /api/restore`, `POST /api/restore/confirm` ([rest-api.md](rest-api.md)) | the same |

What happens:

1. **The archive is read to its end and unpacked** into new folders beside the current ones: `json.new` (with the
   images, which lie in the json folder of a box), `images.new`, `albums.new`, `data.new`. An upload is unpacked
   while it arrives and is not kept. Refused, and what was unpacked is removed again: a file that is no backup or
   was cut off, a backup of the other kind (a box backup on the library and the other way round), a `format` newer
   than this program knows. Nothing of the current data is touched, and the box plays on meanwhile.
2. **The page asks once:** "This replaces all data by those of <date>. With MP3s: all albums are deleted and
   replaced." No backup is made automatically before; the question reminds to make one. A backup that is not put back
   is discarded.
3. **The program stops** – playback stops, the box takes no tag; the library answers the boxes `503` from the
   confirmation on.
4. **The folders are swapped:** every current folder becomes `….before`, the new one takes its place. Only now is
   anything of the current data touched.
5. **The program starts again** with the restored data: a box ends with the status 75 and systemd starts it again,
   as after an update (`start_sim.sh` does the same on the PC); the library starts itself again in the same process,
   since nothing on the NAS would start it. The page waits and loads anew.
6. **The `….before` folders are removed** once the program runs with the restored data. A swap that a power cut
   interrupted is finished at the next start; new folders that were never put back are removed then.

- **With MP3s** the albums need their space twice for a moment: the current albums and the unpacked ones. If the
  space is not there, the backup is refused before anything is unpacked, and the answer says how much is missing.
- **What is not replaced:** nothing secret is in a backup, so nothing secret is replaced – certificate, users,
  passwords, tokens and in the library `settings.json` stay. The identity of a box and what it exchanges with the
  library (`identity`, `sync`) stay. The schedules and the folders of the backups stay (`backups.json`; in the
  library it is in `data/` of a backup, but a restore does not change where backups go), and so do the copies of the
  json files of a box (`json/backup/`).
- **The journal:** the lines of a box without a library come back from `json/journal.jsonl`; its numbers go on after
  them. A box with a library leaves them to the library.
- **A backup without MP3s** replaces the data only; the albums folder stays. Afterwards the data can name an album
  whose folder is not there. For every such album one line is written to the log,
  `ERROR restore: album '<title>' (<id>) is in the data, but its folder '<album-folder>' is missing.`, and the page
  names them after the restore. Album folders that are there but not in the data stay untouched.
- **The library after a restore:** the boxes hold a newer state. The library is the record: the boxes take over the
  restored catalog at the next synchronization ([library.md](library.md), section 4.12). Albums the library no
  longer has are fetched from nowhere, as above.
- **A box with a library:** the library wins at the next synchronization – its JSON files are even generated from
  what the library delivered at every start – so a restore on such a box changes little more than the covers and
  the albums. The page says so before. A restore on a box is meant for a box without a library.
- What the last restore did – when, which backup, which albums are missing – is kept in `restore.json`: on a box in
  `sync/`, on the library beside its data folder.

**By hand** – on another NAS, or without the program: the program is stopped, the archive is unpacked into the
folders it was taken from, and the program is started. The names in the archive are the folders: `json/`,
`images/`, `albums/` of a box, `data/`, `images/`, `albums/` of the library. `backup.json`, `complete.json` and
`json/journal.jsonl` are not needed.

**The library on the NAS** (as an administrator, with SSH; the package is stopped in the Package Center):

```bash
cd /var/packages/tinyjukebox-library
sudo mv var/data var/data.before
sudo tar -xf BACKUP -C var data
sudo cp -p var/data.before/settings.json var/data/        # bootstrap password and tokens, if they are still there
sudo cp -p var/data.before/backups.json var/data/         # schedules and folders of the backups, if still there
sudo tar -xf BACKUP -C shares/tinyjukebox images
sudo tar -xf BACKUP -C shares/tinyjukebox albums          # only a backup with the albums has them
sudo chown -R tinyjukebox-library var/data shares/tinyjukebox/images shares/tinyjukebox/albums
```

Then start the package.

- **The same NAS:** certificate and users are still in `var/ssl`, and the boxes go on as before. Without
  `settings.json` the bootstrap password is the one of the installation again, and the token for the releases and
  the tokens of the scripts are entered again (tabs "Versions" and "Boxes").
- **Another NAS, or `var` is lost:** install the package first ([library.md](library.md), section 3.7), then put
  the backup back as above. The library has a new certificate, which the boxes refuse: on the page of every box, tab
  "System", enter the address of the library and the bootstrap password again. A box stays the box it was, with its
  positions. The users of the page are created again.
- A backup without the albums does not touch the albums folder. Files of albums that the library lacks
  afterwards are sent by the boxes that have them ([library.md](library.md), section 4.8), or uploaded again.
- What changed after the backup: what the boxes still know wins or is fetched again
  ([library.md](library.md), section 4.12).
- The box versions come back by themselves: the slot of the package at the start, another version from the git
  server when a box asks for it – for which the library needs its token for the releases.

**A box without a library:**

```bash
sudo systemctl stop tinyjukebox
tar -xf BACKUP -C /srv/tinyjukebox json
tar -xf BACKUP -C /srv/tinyjukebox/json images
tar -xf BACKUP -C /srv/tinyjukebox albums                    # only a backup with the albums has them
sudo systemctl start tinyjukebox
```

**A box with a library** needs nothing put back: its JSON files are generated on every start from what the library
delivered, and a box that is set up again fetches everything from the library. Its backup is a copy to look into.

**Besides that,** a box keeps the previous version of each JSON file as `*.json_` and, without slots, a copy per
start with changed data in `json/backup/<time stamp>/` – all on the same card, and `json/backup/` is never cleaned up
automatically.

## 11. Operating system updates

By hand only, never automatically:

```bash
sudo apt update && sudo apt full-upgrade
```

After an upgrade of Python, ALSA or BlueZ packages run `./update_box.sh` on the PC and go through the relevant part
of the acceptance checklist ([deployment.md](deployment.md), section 8). The Python of the system matters only for
the environment of the repository on the box; a slot brings its own.
