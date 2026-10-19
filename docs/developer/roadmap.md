# Open points and roadmap

What is not verified yet, what is envisioned, and which gaps are known. Everything not listed here is implemented;
the box runs the version of its slot and works with the library on the NAS ([library.md](library.md)).

## 1. Not yet verified on the box

Items of the acceptance checklist ([deployment.md](deployment.md), section 8) that have not been run on the current
box:

| Item | Expected |
| --- | --- |
| Cut the power while an album plays | the box starts again and continues at the saved position |
| Reboot without WLAN | the box plays, and not later than with WLAN |
| `kill -9` of the service | restart after about 2 s |
| Failed start (port 443 occupied) | restarts with growing intervals up to 30 s, no new JSON backup folders |
| 10 minutes without a tag | the LED blinks green |
| Volume after start and at the maximum (50 % / 82 %) | as loud as intended; otherwise adjust the limits on the web page |

Library, synchronization and self-update ([library.md](library.md)) run on the real devices: the package on the NAS,
filled from the PC, and the box, set up with slots by the playbook. Besides that they are tested on the PC: with a
library and boxes in one process, in a browser, and with the real slot archive for aarch64 on a stand-in for a box
with systemd ([library.md](library.md), section 10). Not seen on the real box yet:

| Item | Expected |
| --- | --- |
| A version that fails | the version before runs again within seconds |
| The playbook on our box, which was set up without a fallback version | the table of the slots shows "Emergency" with the version the box runs ([library.md](library.md), section 6.10) |
| An album that is not on the box | its tag makes the LED flow green, aqua, blue; the album plays as soon as the first track arrived |
| A box without a library and the releases on GitHub ([library.md](library.md), section 6.16): our box unlinked on its page | the section "Version" names the releases; a release shows up there with its day and is installed after it, or with "Install now"; in the mode "ask" the banner is on every tab until it is answered with "Install" or "Skip"; linked again, the box asks the library only |
| A new box set up by the playbook without a library and without an archive | the installer fetches its first version, and the box starts it |
| `./update_library.sh <nas>` and `./release.sh <nas>`, tried with stand-ins for DSM and GitHub only | DSM takes the upload and upgrades the package; the page of the library answers with the new version |

The installer, the Docker image and PyPI ([deployment.md](deployment.md), section 5; [library.md](library.md),
sections 3.9 and 8) are tested on the PC: the installer on a folder that stands for a fresh Raspberry Pi OS and on the
stand-in for a box with systemd (packages, sudoers rules, the first start, a second run, defective versions), the
library that hands it out with a library in one process, library and box with one folder, the image as library and
box (also in the workflow). `./update_box.sh` ran through the installer on our box.

The phone as reader of the library ([library.md](library.md), section 5.1) is tested on the PC: the request with a
library in one process, the page in a browser with a stand-in for the reader of a phone. With a phone, a tag
was read on the page of the library on the NAS (Pixel 9a, Chrome, the self-signed certificate accepted by hand).
Which of the two makers the tag was from, and whether a tag that the box knows is shown with its name, was
not noted; the page answers both itself:

| Item | Expected | If not |
| --- | --- | --- |
| The page of the library, its self-signed certificate accepted by hand | the tab "Tag management" has the button "Scan tag" in the card "Create a new tag", and the scan starts after Chrome asked for NFC (seen with a Pixel 9a, and with Chrome 113 in an Android emulator) | the tap on the button says why |
| A tag of each maker at the phone (UID begins with `04`, with `1D`) | the page shows the tag with its UID, not "Diesen Tag kann das Telefon nicht lesen" | there is no way for these tags: the browser gives no UID for them |
| A tag that the box knows | is shown with its name and album and not as "new": the UID from the phone is the UID the box stored | the page has to convert the UID; the rule is then known. The tag that was added by mistake is removed in "Tag management" |

The app for phones ([app.md](app.md)) is tested on the PC – the library side with a stand-in,
the sync module against a real library, the page with a phone paired in the browser – and tried in the Android
emulator: pairing, a tile that loads and plays an album, positions and journal in the library, the login of expert
mode, the settings, the logout back to simple mode. The emulator has no NFC and no camera. Not seen on a real phone:

| Item | Expected |
| --- | --- |
| The probe (`app/probe/`) with tags of both makers, with and without a case | its log answers section 10 of app.md: how fast and how reliably a tag is found, how late a removal is reported; the result decides presence or touch |
| Install from "Add a phone" (the QR code of the link) | the browser warns once about the certificate, Android asks once whether it may install apps; the app starts with "Pair" |
| Pair with the QR code of the page | the phone is in the tab "Boxes" as a phone with its model as name |
| A tag on the back of the phone | its album plays from the saved position; the band is blue |
| The tag taken away, put on again; lifted for a moment | ends and continues at the position; a lift under 1 s changes nothing |
| Screen off while it plays, then on with the tag still there / taken away | plays on; on the screen again: nothing changes / the playback ends within 2 s |
| An unknown tag | yellow band and the sentence; the tag is on the page of the library, not linked |
| Headset buttons, lock screen, headphones pulled | play/pause, next, previous; the playback pauses |
| The volume limits set on the page | the phone never plays louder than the share set for speaker or headphones |
| Without the library (away from home) | the albums in the cache play; positions and journal arrive later |
| Expert mode: "Scan tag", an upload of an album, a backup saved, a link to Spotify | the UID appears on the page; the files are chosen; the backup is in "Downloads"; the link opens in the browser |
| A release with a new version | the phone installs it over the older one, signed with `APP_SIGNING_KEY` |
| Spotify: the registration with an account of the family subscription, the client ID in the tab "Spotify", a tag of Spotify on a phone with the Spotify app | plays in the Spotify app from the saved track and second, stops when the tag is taken away, the volume stays within the limit, the statistics count it; without the Spotify app: "This tag plays on a box" |

Verified on the box: RFID reading of the existing tags, tag on/off/change, a whole album without dropouts, recovery
after `pkill rfid-reader` and after a reset of the RC522; pairing, playback on the headset, silent speaker, headset
buttons, volume limit, pause on loss of the headset and resume, reconnect after a reboot. With the library: the
package on the NAS with its page, the first filling, the handshake of the box, its changes and its journal delivered;
the first slot installed by the playbook, started by `tinyjukebox-start` under systemd and confirmed by the health
check after an album played because of a tag – with `rfid-reader` and the sound of the slot; two later releases
that the box installed by itself.

The web page and the user documents in two languages are tested on the PC: the text files and the documents by
tests, both pages in a browser in German and in English (`scripts/pages/check.sh`). Not seen yet:

| Item | Expected |
| --- | --- |
| The English page on a phone | no text that is cut off or does not fit its button |
| The package of the library in a DSM that is set to English, and in one set to German | name, description and the dialog of the installation in that language (`displayname_ger`, `description_ger`, `install_uifile_ger`) |
| The English manual, read against the English page | every label it quotes is on the page |

The folder of the covers and the logout of a page that is left alone (0.29) are tested on the PC, the upgrade of the
package with its scripts in a stand-in for DSM. Not seen yet:

| Item | Expected | If not |
| --- | --- | --- |
| The upgrade of the package on the NAS from 0.28 | `var/library.yaml` has `images-folder` instead of `cards-folder`, with the same folder; the library starts and the stations have their covers | the library does not start and `var/server.log` names the key: rename it by hand in `var/library.yaml` |
| The first start of 0.29 on the box | the covers of the stations are in `json/images/`, the tab "Radio" shows them | they come again from the library |
| A page left open for more than 30 minutes, on the PC and on a phone | the login page with "You were logged out: nothing was done on this page for 30 min." | – |

Spotify ([architecture.md](architecture.md), section 4.1) is built and tested on the PC with a stand-in for Soloist,
but has not played on a box yet. The trial needs the Soloist API key, the Spotify account of the box and a phone
logged in to it. Reports of Soloist: <https://github.com/spotify/soloist/issues>.

| Assumed while building | If the trial shows otherwise |
| --- | --- |
| PipeWire with WirePlumber, started by the box with a setup of its own: one output into a named pipe, no sound card, no Bluetooth | PulseAudio (`module-pipe-sink`), or the stand-in for libpulse of report no. 2 |
| The box reads the pipe and hands the sound to `aplay`; no sound for 15 s while playing restarts the track | – |
| Soloist is started per playback and ended afterwards (with the speaker on: while no tag is on the box) | kept running after the first use |
| Commands with `soloist ctl`, events from `soloist ctl trace` | a WebSocket client, if the calls are too slow for the skipping |
| "Connect" stops `avahi-daemon` for its five minutes, so that Soloist gets port 5353; otherwise avahi keeps the port | avahi is left alone |
| Every kind of link is accepted: album, playlist, track, episode, podcast, audiobook, artist | the page refuses the kinds that do not start |
| The login counts as there until Soloist reports none (`auth_state`) | – |

| Try | Expected |
| --- | --- |
| Key entered | "Player" shows "Ready" with the day of the build and its last day |
| "Connect", choose the box in the Spotify app of the account of the box | "Connected since …"; the login survives a restart and a power cut |
| A playlist, an album, an audiobook, a podcast on a tag | plays in order, LED aqua then blue, on the speaker and on the headset |
| Take the tag off in the middle of track 20, put it on again | continues at track 20 at the position, silent while it skips; how long it takes |
| A playlist that was changed meanwhile | finds the track at its new place, or starts from the beginning if it is gone |
| The last track ends | finished (LED blue, blinking); what Spotify would add is not played |
| Pause, next, back, back held; headset off and on | as with an album |
| On the phone with the account of the box: pause, skip, shuffle, volume while a tag plays | undone by the box; music started on the phone pauses the box until "play" |
| Without WLAN, WLAN lost while it plays | LED aqua, plays on when the WLAN is back |
| Speaker switched on, no tag: play from the phone | the box plays; its buttons steer it; a tag ends it; 10 minutes paused: LED green |
| Another account in the WLAN: is the box offered to it, can it take the login? | not offered while avahi holds the port – then "Connect" is the only way in |
| Memory and processor of the Pi 3 while it plays and while Soloist only waits; size of the cache | nothing has to be limited |
| A new build of Soloist from Spotify while the login stays | plays on with the new build |

## 2. Open measurements and observations

| Topic | State |
| --- | --- |
| Start time | not measured on trixie. The previous system was ready about 15 s after power-on. Measure with `systemd-analyze` and `systemd-analyze blame` |
| Sound start and stutter | `albumplayer/buffer-time: 2000` is in use since 2026-10-19 (radio paused with 200 ms on WLAN with power saving); latency values ([operations.md](operations.md), section 8) and stutter under load (tag change, upload) have not been recorded |
| WLAN while playing over Bluetooth | WLAN and Bluetooth share one radio chip on the Pi 3; the effect on uploads has not been measured |
| Power supply | the kernel of the previous system reported under-voltage on its last start; power bank and cable have not been checked. A reset of the RC522 (2026-10-04) may have the same cause – not proven |

## 3. Known gaps

No plan exists for these yet.

| Gap | Consequence |
| --- | --- |
| A box without a library makes no backup by itself | its data leaves the SD card only when somebody fetches a backup ([operations.md](operations.md), section 10). With a library everything a box needs to be rebuilt is there, and the library makes backups by a schedule |
| A backup is put back by hand | stop, unpack, start ([operations.md](operations.md), section 10.3); tried on the PC, not on the NAS and not on a box |
| The slot scripts in `deploy/slot/` have few automated tests: the test bed that proved them is not kept | power cuts while unpacking and switching, trigger files, a link changed by hand, the fallback slot and `--root` are untested since. Before a change of the scripts that goes beyond a correction, build the test bed again ([library.md](library.md), section 9.8) |
| The build of the slot sets `UV_LEGACY_TAR_BACKEND=1` for uv (`scripts/build_slot_aarch64.sh`), because the emulator of Docker Desktop on the PC is older than QEMU 10.0.8 and gets a system call wrong that uv uses since 0.13.0 ([library.md](library.md), section 6.15) | the switch is uv's own, meant for a transition: uv will drop it one day, and a variable it does not know is ignored without a word. **To be checked from time to time, and whenever the build on the PC fails with `failed to inspect … Invalid argument (os error 22)`:** which emulator Docker Desktop has – `docker run --rm --platform linux/arm64 -e QEMU_VERSION=1 debian:trixie /bin/true` prints it. From QEMU 10.0.8 on the error is corrected: take the line with the switch and its comment out of the script, build the slot once on the PC, and remove this entry and the paragraph in library.md. If uv drops the switch before Docker Desktop has such an emulator, the build on the PC needs another way: a newer Docker Desktop, or the emulator of the workflow (`docker run --privileged --rm tonistiigi/binfmt --install arm64`, to be repeated after every start of Docker Desktop). The workflow on GitHub is not concerned in either case |
| The process of the library on the NAS is not restarted after a crash | the package is started again by hand in the Package Center; a systemd unit in the package would do it ([library.md](library.md), section 3.6) |
| `json/backup/` of a box without slots is never cleaned up | one folder per start with changed data. A box with slots does not write it; what is there from before stays |
| Hardware, real BlueZ/BlueALSA and the Ansible roles have no automated tests | covered only by the acceptance checklist |
| `deploy/scripts/install_service.sh` installs the unit of a box without slots to `/lib/systemd/system`, the playbook to `/etc/systemd/system` | use the playbook; the script is a leftover of the manual installation |
| The role of a web user can only be changed with `tinyjukebox-users` on the box | – |

## 4. Envisioned

Not scheduled, in no particular order.

| Idea | Precondition / note |
| --- | --- |
| Read-only root file system (overlayfs) | needs a separate data partition for `/srv/tinyjukebox`; the system partition would then not be written during operation |
| Samba share for the albums, as an Ansible role of its own | only if upload through the web page and `rsync` prove insufficient |
| Shorter start time | measure first (section 2); candidates beyond the services already disabled: swap small or off |
