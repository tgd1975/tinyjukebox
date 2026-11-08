# Changelog

One section per version. A push to `master` that changes the software needs a section for its version here; the
release takes its text from that section ([docs/developer/library.md](docs/developer/library.md), section 8).

The project is older than this file: work on it started in 2018, and the first box has played since Christmas 2019.
The sections before 0.9.0, at the end of this file, were written afterwards from the commits of that time.

## 0.48.2 – 2026-11-08

- **A box connected to Spotify stays connected.** After "Connect" the first playback of Spotify said that the login of
  the box was gone, and the box had to be connected again – every time. Soloist reports "not logged in" for a moment
  when it starts, before it has signed in with the login it keeps; the box took that for a lost login. Only a "not
  logged in" that lasts 10 s counts now.
- **"Connect" tells what the Spotify app shows.** The app may say that it cannot connect once the box has taken over
  the login; the card "Account" and the manual say that this is fine, and where the app lists the box.

## 0.48.1 – 2026-11-08

- **A tag on the phone no longer starts its album again and again.** On a real phone the album began to flicker and
  stutter after the tag was put on: a second later Android reported it lost while it lay there, and from then on found
  and lost it ten times a second – each time the app ended the playback and started it anew. A tag now counts as taken
  away only when it was not found again within a second, however Android lost it, and only the watch of the tag found
  last counts.
- **The buttons at the top of the app can be pressed.** Since Android 15 the app is drawn behind the status bar, and
  "Expert", "Simple mode" and "App settings" lay under it. Every screen of the app now keeps clear of the status bar,
  the navigation bar, the cut-out of the camera and the keyboard.

## 0.48.0 – 2026-11-08

- **Radio starts within about a second while the headset is switched off.** The box looked for a paired headset that
  was switched off every 15 s, and each search held the radio chip that the Pi 3 shares with the WLAN for up to 5 s:
  the WLAN lost 7–17 % of its packets, and a station started after 4–9 s instead of 1 s. Now the box looks for the
  headset for a minute after it is switched on, after a pairing and when the headset has lost the connection; then
  it waits for the headset, which connects itself when it is switched on.

## 0.47.0 – 2026-11-07

- **The tab "Boxes" of the library shows where each box is:** under the last contact the address the box asked from,
  for a box as a link to its web page, for a phone as text. A box names the port of its page with every request
  (`X-Box-Page`); a box with an older version shows the address only until it has updated itself.
- **The own log of a box and of the library can be saved as a file,** tab "Log", "Save as file": what the page keeps
  of it (the last 5000 entries) with the chosen level and filter, one line per entry. Before, only what a box had
  delivered to the library could be saved.

## 0.46.0 – 2026-11-04

The web page takes the colours of the logo, and every pair of text and ground on it reaches the contrast of WCAG AA
(4.5 : 1). What the colours are and why: [docs/style-guide/](docs/style-guide/README.md). The documentation is a
site, and the manuals have a chapter for each kind of playing.

- **The web page of the library is berry, as the logo, the favicon and the app.** Buttons, tabs, the progress and the
  lid next to "Tiny Jukebox" were coral (`#d9463b`), the favicon next to them berry (`#8e2f66`). In the dark theme the
  accent is the light berry of the logo (`#d77aae`).
- **Text on the page is easier to read in the light theme.** The primary button had 4.29 : 1, the badges and the
  warnings 3.2–3.6 : 1, secondary text 4.37 : 1; secondary text, success, warning and error are darker now, and every
  pair reaches at least 4.5 : 1.
- **The colours of a box keep that contrast for every hue.** Its accent is a little darker (`oklch(.49 .15 hue)`, was
  `.52`) and its soft ground a little lighter; before, a box with a hue around cyan had 3.98 : 1 on its active tab.
- **The LED on the page has the colours of the app.** The page drew it in other shades of green, blue, yellow and red
  than the phone; both use the colours of the logo now.
- **The red message at the bottom of the page can be read in the dark theme too**: its text is dark on the light red
  there (6.6 : 1, was white with 2.65 : 1).
- **Links on the page are berry too, and every control shows the focus in it.** Links had the blue of the browser; links
  and the sortable column headings had the focus ring of the browser instead of that of the page.
- **Under the mouse, grey text keeps its contrast.** A tab and a tag card under the mouse put their grey text on the
  colour of the lines (4.38 : 1); now a tab turns dark on the light ground, and a tag card gets a frame. In the dark
  theme the grey text is a little lighter (`#a8a095`), so that it reaches 4.5 : 1 on a notice and on a tag that lies on
  the box as well.
- **The lid next to "Tiny Jukebox" has the colours of the logo on the page of the library**, also in the dark theme;
  only the page of a box gives it the colour of the box.
- **The LED on the page names its colours in the language of the page**: the tip under the mouse said "GREEN / AQUA"
  in German too.
- **The German page says "Album von vorn" and "Titel von vorn"**, as the quick guide.
- **The box and the library bring their help along: "Help" at the top of the page** opens the manual at the chapter
  of the open tab – "Spotify" at chapter 9, "Log" at the logs of the administration –, in the language of the page,
  without the internet and in the version the box runs. The quick guide, the manual and the administration are there
  in English and German. After a login a hint points to the quick guide, until "Do not show it again" is ticked.
  Developers: the pages are made from `docs/user` by `scripts/help/build.py` and checked in; `test_help.py` fails if
  they are stale.
- **The administration of boxes and library is a manual of its own, in English and German**
  (`docs/user/<language>/admin.md`): setting up a box and the library, updates and going back, users and access,
  backups, logs, configuration, troubleshooting – in steps, with links to the complete details in the developer
  documents, which stay English. Everything for users and administrators is in both languages now, everything for
  development in English.
- **The documentation is a site as well: <https://tgd1975.github.io/tinyjukebox/>.** One menu by reader – "Use" and
  "Bedienen" for the manuals, "Administer" and "Verwalten" for the administration, "Hardware", "Develop", "Reference",
  "Concepts" –, a search in English and German, the colours of the logo, light and dark. The files stay where they are;
  the site is built from them on every push and published from `master` (`scripts/site/`,
  [development.md](docs/developer/development.md), section 10).
- **The manuals have a chapter for each kind of playing: 7 MP3 albums, 8 radio, 9 Spotify.** They were three parts
  of "Managing albums, tags and radio stations"; each chapter now says first what it plays, what it needs and who may
  set it up. The chapters after them moved from 8–11 to 10–13; the terms name Spotify too.
- **A generated table of every key of the config file**, from `docs/config_schema.json`, on the site under
  "Reference".
- **Developers: the style guide is in `docs/style-guide/`.** A change of a colour changes it in the same commit. The
  documents follow it: every section is numbered, lines end at 120 characters, the dash has spaces.

## 0.45.0 – 2026-11-03

What a review of the whole code, and then of its documentation, found, corrected. What can only be tried with the
hardware, the NAS or a phone is listed in [roadmap.md](docs/developer/roadmap.md), section 1.

**Library and synchronization**

- **The folder of an album never leads out of the albums folder.** The key of a box could read and overwrite every
  file of the library through the folder of an album (`../`), e.g. `settings.json` with the bootstrap password. The
  library refuses such a folder, and neither side uses a folder it received as a path before it passed the rule.
- **An entry of Spotify created on a box reaches the library.** The library refused it every time, and it
  disappeared from the box together with the link of its tag.
- **A download that broke off goes on where it stopped,** on a box and on a phone. Before, a continued download
  overwrote the beginning of the file with its end, and every interruption cost a try and a warning "wrong checksum".
- **The library goes on after an answer it does not expect:** the search for releases no longer ends on a page of a
  proxy or a full disk, and what a box sends in another shape is refused with 422 instead of failing with 500.
- **A box can no longer take itself over** – it removed itself. The one-time password of a phone never takes over a
  box, and the bootstrap password never a phone.
- **The clock counts as set with chrony, ntpd and in Docker too:** the box and the library ask the kernel, as systemd
  does. The statistics of a box without timesyncd counted no days.
- **A bootstrap password that the config names anew replaces the one before** – the dialog of a new installation of
  the package over the data of the old one, a changed secret of Docker. Before, the library kept the old one and
  ignored the new one. A bootstrap password set on the page stays until the config names another one.
- **Smaller.** Handshakes that are never finished are forgotten; a logged-in page needs the header against other sites
  on every changing request; a cover replaced by one of the same size reaches the boxes; the page of a box reads only
  its own journal for its statistics; the log a box sends names a Bluetooth address as such; user names follow one rule
  (at most 64 printable characters, no `:`); a password generated at the first start is printed once on the error output
  and no more written to the log; `TINYJUKEBOX_ADMIN_PASSWORD_FILE` and `TINYJUKEBOX_BOOTSTRAP_PASSWORD_FILE` take a
  password from a file, e.g. a secret of Docker.

**The box**

- **A lost headset never sounds on the speaker for a moment.** The speaker takes over only once the playback has
  paused. A headset lost while the box waits for a file from the library pauses the album as well.
- **The box stops completely,** also when the SD card has become read-only while an album plays: the album plays on
  without saving its position, the stop takes every step, and no thread keeps the process alive. A track that cannot
  be started any more pauses the album instead of standing still in "play".
- **The messages of the box keep flowing when the journal cannot be written** (a full or read-only card): before, an
  unexpected error then ended the thread that delivers tags, buttons and LED.
- **A tag placed while a radio test was about to start plays its album once,** not twice.
- **"back" after the end of an album plays the last track again,** as the manual says. Before, it played the track
  before the last one.
- **A headset that was louder the last time starts at the start value of the box,** also when it reports its old
  volume late.
- **Smaller.** The timers of the buttons, the LED and the headset do nothing after the stop; the Bluetooth driver tries
  again after an error nobody expected and does not hold up the stop; the console ends with the service, and the end
  of an input that is no terminal ends only the console; after `reload_driver` the console commands of a driver reach
  the new one.

**Spotify**

- **The buttons and the headset never wait for Soloist.** In speaker mode a slow or hung Soloist used to block every
  button of the box, for up to 10 s. A lost headset silences the box at once.
- **A tag of Spotify on a box without internet keeps trying, for hours** – before, it gave up after about 500 tries
  with a red LED. A paused entry stays paused when Soloist starts again, and the position is kept.
- **"Connect" runs alone:** no second Soloist starts on the same login meanwhile. A playing entry waits and goes on
  afterwards, and the speaker comes back.
- **Every tag ends what the phone plays on the speaker of the box** – also a tag the box has never seen and a tag on a
  box that is out of service. Before, those two let the phone play on.
- **A hung `aplay` no longer holds up the box** when a tag is removed or the headset changes. A new build of Soloist is
  unpacked in pieces, with limits: an oversized archive can no longer exhaust the memory or the card.

**RFID reader**

- **A tag that takes the place of another one is noticed.** Of two stacked tags the reader sees one; when that one is
  taken away, the box plays the album of the other one – before, the first album played on. A quick swap is noticed
  too. Like a removal, another tag counts only when it is seen twice in a row.
- **A tag on the box is no longer reported as removed and placed again** after a garbled answer, or when the reader
  was reset in the moment of its second look: the album no longer starts again.
- **A hanging `rfid-reader` is started again.** It writes `alive` at its start and every 10 s; after 30 s of silence
  the driver ends it and starts a new one.
- **Smaller.** Stopping the driver no longer waits for, or leaves behind, a reader that sudo could not end; the health
  check notices a reader that does not run; wrong pins are refused; the error of bcm2835 is logged once; a tag placed
  on the web page is not ended by a new start of the reader.

**Web page**

- **A long upload of a backup to restore no longer logs the page out** before the question "restore?".
- **A password set with `tinyjukebox-users passwd` ends the sessions of that user,** as one set on the page does.
  Before, they stayed valid for up to 7 days. The command no longer removes the last admin or makes them a user.
- **A page that is opened again goes on with its login** within the 7 days, as the manual says. Before, any login
  ended the sessions in which nothing had been done for 30 minutes, also those of pages that were closed.
- **The QR codes of the library are images, the page keeps no token of the login.** The state of the Spotify speaker,
  the end of an album and a size that is not known have their texts.

**Setup of a box, releases and scripts**

- **`rfid-reader` runs as root only if the box trusts it.** On a box with slots the rule of sudo allowed the program of
  the active slot, which the user of the service could replace. Now it allows a launcher of root, which starts the
  program only if the release signed its checksums or the installer listed it. It takes effect with the next run of
  the installer (`./update_box.sh`); a version up to 0.44 installed after that reads tags only after another run.
- **The slot scripts survive a power cut in the middle of a note,** and a link `current` that points nowhere starts
  the confirmed version instead of recovery mode.
- **A box no longer refuses a release for good** because its key was missing or a signature arrived broken; a new
  key from the installer lets it try again. A stalled download ends after a minute, the key and the token of a box no
  longer appear in the list of processes, and the slot scripts refuse to run as root.
- **Release pipeline:** the key of the releases never enters the build container. Only `master` signs with the real
  keys, and a job of its own in the environment "release" publishes. The actions are pinned to commits and kept up
  to date by dependabot. A draft left by a cancelled job is published or made anew.
- **The scripts on the PC** (`update_library.sh`, `release.sh`, `check_release.sh`) send passwords and tokens only to
  the public key of DSM or of the library that was accepted the first time.
- **The package for the NAS** takes any bootstrap password, and no longer takes another program for the library after
  a restart of the NAS.
- **Docker:** the image takes the passwords from files as well. `compose.yaml` of the repository passes them as
  secrets of compose: **create the files `admin-password` and `bootstrap-password` next to it** before
  `docker compose up` (its head says how; they count at the first start only). The base image is pinned to its
  digest, and the dependencies are installed with their checksums.
- **The installer** writes the platform of the box into `slot.conf`, replaces `config.txt` in one step and takes
  `--pin`: the line of "Add a box" passes it, so the first version comes from exactly this library.

**Documentation**

- **Checked against the code and corrected.** What changes what an administrator does:
  - The library on the NAS asks for new versions only with a token, also for a public repository – or with an empty
    file `var/github-token`.
  - Recovery mode is ended with `tinyjukebox-slot update --now`; without `--now` the version waits for the next
    start.
  - A file `/srv/tinyjukebox/config/tinyjukebox.yaml` replaces the config of every slot; for troubleshooting it is a
    copy of the one of the slot.
  - To start the library on the NAS from nothing, `var/data` goes too.
  - The console of a box is started with `--no-webserver`: as `pi` the box cannot open port 443, only the service can.
  - The box uses `buffer-time` 2000.
- **Smaller corrections in every document.** The user manuals name the LED of a tag of Spotify.

**App for phones**

- **The app no longer stops when it is swiped away while an album plays,** or when the storage of the phone is full or
  a file of the app is damaged: a damaged line of the journal or the outbox is left out, and lost lines reach the
  library as gaps.
- **A tag put on while the app was in the background replaces the one before and keeps playing.**
- **Other apps – lock screen, headset, watch, car – can only press play/pause, next, previous and stop;** they can no
  longer go past the volume limit.
- **A download that broke off goes on where it stopped,** also when the app was ended just before a file got its
  name. The albums on the phone leave 200 MB free, and what no album names any more is removed.
- **After "Unpair" and pairing again, the statistics of a phone go on.**
- **Smaller.** A station whose playlist does not end no longer holds up the app; an entry of Spotify counts as switched
  on; the app listens to the library from its first contact, and rarely while the two do not fit together.

## 0.44.0 – 2026-10-19

- **The LED shows that a radio station is getting ready.** From placing its tag until its sound is heard, the LED
  flows green, aqua, blue, as while an album is fetched; it turns blue only when the sound starts. Before, it turned
  blue while the box was still silent, collecting the first two seconds of the stream.
- **The start of a radio stream is measured to the moment it is heard** (`audible_ms` in `radio.startup`) instead of
  estimated. The estimate of 0.41.0 to 0.43.0 and its `buffer_ms` were 1000 times too large: the buffer of the
  configuration, in milliseconds, was read as seconds.

## 0.43.0 – 2026-10-19

- **Breaks of the radio sound are counted on a box in any language.** aplay reports them in the language of the
  system – on a German one `Unterlauf!!! (mindestens 2844,463 ms)` – and 0.41.0 recognized only the English text.

## 0.42.0 – 2026-10-19

- **An app for Android phones and tablets: a phone as a box.** A tag held to the back of the phone plays its album
  from where it stopped on this phone, taken away it stops – or a tile is tapped. The band at the top of the screen
  is the light of the box. Albums are loaded when they are played and stay on the phone (at most 4 GB at first), so
  they play without the library; the screen may go off, headset buttons and the lock screen work. Each phone has its
  own progress and its row in the statistics, like a box.
- **A tag of Spotify plays on the phone in its Spotify app,** from the saved track and second, as on a box. It needs
  the app registered once at Spotify; the tab "Spotify" of the library says how and takes its client ID.
- **"Expert" in the app is the page of the library,** after its login, with "Scan tag" by the NFC of the phone; a
  logout, also after 30 minutes without a touch, leads back to the box. The settings of the app – name, albums on the
  phone and their limit, versions, licences, "Unpair" – are reached there only.
- **The tab "Boxes" of the library has "Add a phone":** the link to the app, also as a QR code, and "Pair", which
  shows a QR code for the app, valid once and for 10 minutes. A phone is listed as a phone, with "Volume" for the most
  it plays with on speaker and headphones. A phone may change its progress and report new tags, nothing else.
- **Every release holds the app** (`tinyjukebox-app-<version>.apk`), signed with the key of the repository; the
  library fetches it with the slot and hands it out at `/app.apk`, the package brings the one of its version.
- New dependency of the package: `segno` for the QR codes.

## 0.41.0 – 2026-10-19

- **The library keeps what the boxes log.** A box sends its warnings and errors to the library with every pass of
  the synchronization, an error at once; without a connection they wait, bounded. Passwords, keys, tokens,
  certificates, queries of addresses, tag UIDs and ids of Spotify are removed before a record leaves the box. The
  library keeps them for 30 days, at most 20,000 per box (`diagnostics` in its config), apart from the journal.
- **The tab "Log" of the library shows the log of a box,** with filters for level, part, version, code, time and
  text; the records can be copied or saved as a file. For administrators only.
- **A box that cannot start says why.** Its start script counts the failed starts and sends the last error to the
  library before the next start – also in emergency mode, over a request that never changes. The tab "Boxes" shows
  "cannot start since …", the tab "Log" the error; when the box runs again, the report becomes part of its history.
  Run the installer on a box (`./update_box.sh`) to get it.
- **The start of a radio station is measured** phase by phase: playlist, ffmpeg and aplay, the first audio, and an
  estimate when it is heard; breaks of the sound in the first 30 s and later are counted, and a connection that
  ends says which part failed. The numbers go to the library with the log.

## 0.40.0 – 2026-10-19

- **A damaged or unknown piece of data costs that piece, not the box.** Tags, targets and positions are checked entry
  by entry: an entry that is damaged, or of a kind that only a newer version knows, is left out, kept in the file and
  written back unchanged. A field that only a newer version knows is kept, also in what goes to the library. Going
  back to an older version therefore no longer ends in a box that does not start – from this version on; older ones
  still refuse such a file as a whole.
- **If neither a json file nor its backup can be read**, the box starts without that file instead of not at all, and
  keeps both as `.damaged-<time>` for repair. A file of the copy of the library that cannot be read is fetched again.
- **A damaged journal no longer keeps the box from starting.** The box keeps every complete line and writes a line
  `recovery_gap` for every lost number – a damaged line, or a number whose line never reached the card. The library
  takes the lines after it again and counts no listening for the marks.
- **The box says what it left out:** in the log, under "Data left out" in the section "Version" of the tab "System",
  and to the library, whose tab "Boxes" shows a note.
- **Emergency mode instead of the emergency version.** If neither version of a box can run, the fallback slot no
  longer starts its old application, which could not read the data of today. The start script installs the newest
  version the box has not gone back from, verifies it and starts it; if there is none, the box plays nothing and asks
  again every 10 minutes, without a restart loop. With updates "off" or "by hand only" it waits for an administrator.
  The button on the page is now "Start emergency mode". It lives in the start script: run the installer on a box
  (`./update_box.sh`) to get it.
- **A backup no longer misses a json file that is being written.** For a moment while it is saved, the file is not
  there; the backup takes its backup copy then, which is complete.

## 0.39.2 – 2026-10-19

- **A line of the journal survives switching the box off.** The box wrote the number of a new line to the card at
  once, but the line itself only some seconds later. Cutting the power in between – which is how the box is switched
  off – left numbers without their lines and a damaged last line, and the box then restarted again and again while
  reading its journal. Every line is now on the card before the box goes on, like the outbox.
- **Switching the box off loses at most about 6 s of the log.** The installer lets the kernel write what is not
  written with `fsync` after 5 s instead of 30 s (`/etc/sysctl.d/90-tinyjukebox.conf`).

## 0.39.1 – 2026-10-16

- **The Spotify test endpoint reads its request body only once**, avoiding a connection-reset error that could fail
  the box's health check and cause an unnecessary fallback.

## 0.39.0 – 2026-10-16

- **Tiny Jukebox has a logo.** It is drawn from the photos of the box: the box seen from the front at an angle, the
  berry purple of its lid, the round window for the tag, the LED and the buttons along the rim of the lid
  (`docs/images/`). The README shows it at the top, the photos of the box from 2019 and of today follow there and in
  the hardware documents and the user manuals.
- **The web page shows the lid of the box in place of the dot**, next to "Tiny Jukebox" in the top bar and on the
  login page. The lid takes the colors of the box, as the dot did; the library keeps the colors of the project. The
  favicon and the icons of the package for the NAS are the lid in the colors of the logo.

## 0.38.0 – 2026-10-16

- **A box is set up with one line.** On a Raspberry Pi with a fresh card (Raspberry Pi OS Lite, 64-bit):
  `curl -fsSL https://github.com/tgd1975/tinyjukebox/releases/latest/download/install.sh | sudo bash`. The installer
  asks for the password of the page, sets up packages, sudoers rules, robustness, firmware, sound, Bluetooth and the
  slots, installs the first version and restarts the box once. It runs again without changing what is right, never
  touches the data, and leaves alone what the Raspberry Pi Imager sets. Every release has its `install.sh`.
- **A new box joins the library with its line.** The tab "Boxes" of the library has the section "Add a box" with the
  line for this library – it pins the certificate of the library and asks for the bootstrap password. Without
  credentials the library hands out the installer, the slot and its signature of the newest usable version, and
  nothing else; it keeps the installer with every version from now on, and the package brings the one of its version.
- **The playbook runs the installer** for what both do: one description of how a box is set up. The roles `audio`,
  `bluetooth` and `slots` are gone, `base` keeps hostname, locale, WLANs, SSH and the packages for building. The block
  of the playbook in `config.txt` becomes the block of the installer without a reboot. Run `./update_box.sh` once.
- **Library and box start with nothing but a folder:** `tinyjukebox-library --data DIR`, `tinyjukebox --data DIR`. The
  first start takes the password of `admin` from `TINYJUKEBOX_ADMIN_PASSWORD` (or asks, or writes a generated one to
  the log), the bootstrap password from `TINYJUKEBOX_BOOTSTRAP_PASSWORD`, the library of a box from
  `TINYJUKEBOX_LIBRARY`.
- **A Docker image for the library and for a box without hardware,** `ghcr.io/tgd1975/tinyjukebox`, for amd64 and
  arm64, and `deploy/docker/compose.yaml` for both together. **The package on PyPI:** `pip install tinyjukebox`. Both
  are published by the workflow after the release, once the repository is public; the workflow tries the image on
  every push.
- A version that needs a newer setup names the installer on the pages, no longer the playbook.

## 0.37.2 – 2026-10-16

- **A trigger file on the boot partition is obeyed once again, and Spotify can be connected, on a box whose user needs
  a password for `sudo`.** The start script removes `tinyjukebox-revert` or `tinyjukebox-fallback` as the user of the
  box with `sudo`; without a rule that allows it, the file stayed and every start switched the version again. The same
  for "Connect" of Spotify, which stops `avahi-daemon` for a few minutes. The playbook adds two sudoers rules for
  exactly these commands: `/etc/sudoers.d/tinyjukebox-trigger` (role `slots`) and `tinyjukebox-avahi` (role `audio`).
  Run `./update_box.sh` once.

## 0.37.1 – 2026-10-15

- **The playbook waits 30 s for the box instead of 10.** A task failed with "Timeout waiting for privilege escalation
  prompt": the sudo prompt of the box came too late. `update_box.sh` asks for the sudo password of the box
  (`--ask-become-pass`), unless `ANSIBLE_BECOME_PASSWORD_FILE` names a file with it.

## 0.37.0 – 2026-10-15

- **The login page says where it is: "Library", "Box" or the box with its name**, e.g. Box "antonia". After the
  login the name stands next to "Tiny Jukebox" at the top, and the tab of the browser carries it.
- **Every box has colors of its own**, on the login page and on its whole page: buttons, tabs and the dot of the
  logo. They follow from the name of the box, or from its id while it has no name (a box gets a name with a
  library). A renamed box gets new colors; the library keeps the colors of the project.
- `GET /api/identity` tells it without a login: `mode`, `name`, `hue`.

## 0.36.2 – 2026-10-15

- **The volume buttons keep working after a step that failed.** If setting the volume ended with an exception, the
  state machine of the volume buttons stayed in the state of that step and took no button any more until the box
  was started again. It goes back to "idle" now; the exception is logged and reported as unexpected, as before.

## 0.36.1 – 2026-10-15

- **A new version is no longer sent back right after the box was switched on.** The start script gives a new version
  120 s to pass stage 1 of its health check, and it counted them on the clock of the system. The Pi has no real-time
  clock: it starts with the time it was switched off at, and a few seconds later the clock is set from the network
  and jumps forward by the hours the box was off. The time limit had passed at once, the box went back to the version
  before and reported the new one as defective – the box antonia did so with 0.30.2, 0.31.0 and 0.35.0. A restart of
  the service without switching the box off was not concerned. Now the limit counts the seconds since the boot.
  - The start script lies outside the slots: a box gets it with the next run of the playbook (`./update_box.sh`), not
    with the update of the box software.
  - A version that a box reported as defective for this reason is `manual` in the library. The box installs the
    next newer version by itself; only to bring that very version to a box, set it to `use` again in the tab
    "Versions".
- **"Update now" and "Back to the previous version" restart the box again on a box that asks for a password for
  `sudo`.** The slot scripts restart the service with `sudo systemctl restart tinyjukebox` as the user of the box. A
  box set up from an image whose user may not use `sudo` without a password waited for one in vain: the switch was
  made, but took effect only at the next start. The playbook allows this one command now
  (`/etc/sudoers.d/tinyjukebox-restart`); a box gets it with the next run of `./update_box.sh`.

## 0.36.0 – 2026-10-14

- **A tag can play an album, a playlist, an audiobook or a podcast of Spotify** – built, not tried on a box yet: the
  trial is in `docs/developer/roadmap.md`, section 1.
  - Tab "Spotify", built like "Radio": an entry is made from the link that the Spotify app shares; name and cover come
    from Spotify (a podcast is named by hand). Tags are linked to entries in "Tag management" and "Now"; "Tags" shows
    and filters them. "Test" plays an entry for a minute.
  - An entry plays like an album: it continues where it stopped – Spotify always starts at the first track, so the box
    skips silently to the saved one, and finds it again in a changed playlist –, the buttons and the headset work as
    with an album, and the end is "finished".
  - The phone has no influence while a tag plays: a pause, another track, shuffle, repeat or the volume from the
    Spotify app are undone. Music started elsewhere with the account of the box pauses the box until "play".
  - The box plays with Spotify Soloist, which it fetches from Spotify itself when the key is entered (cards "Account"
    and "Player" of a box). It keeps its build until 30 days before its last day; "Update now" asks at once. "Connect"
    waits five minutes to be chosen in the Spotify app. Every box has a Spotify account of its own.
  - Per box it can be switched on that the box is a Spotify speaker while no tag is on it: the Spotify app of the
    box's account plays on it, a tag wins. The volume is always the box's.
  - The playbook installs `pipewire`, `wireplumber` and `libatomic1` and masks their user services: the box starts
    its own PipeWire, whose only output is a pipe that the box plays with `aplay`.
  - The statistics count entries like albums; their skipped tracks are named by title.
- **The interface between box and library is version 2.** A box of an older version fetches the new version before
  it synchronizes again; the library offers it as usual.

## 0.35.0 – 2026-10-14

- **A backup is put back on the page**, tab "System", card "Backup", "Put a backup back" – on a box and on the
  library, by admins. Until now it was unpacked by hand.
  - "Choose backup …" sends a file; on the library, and on a box without a library, "Put back" takes a backup from
    the table of the backups. With MP3s the upload is unpacked while it arrives, not kept in memory.
  - The backup is read to its end and unpacked beside the data first. Refused, with nothing changed: a file that is
    no backup or was cut off, a backup of the other kind (of a box on the library and the other way round), one of a
    newer format, and one with MP3s for which the space is missing – the answer says how much.
  - The page asks once: "This replaces all data by those of <date>." Then the box or the library stops, swaps the
    folders and starts again; the page loads anew. No backup is made before; the question reminds to make one.
  - Not replaced: users, passwords, certificate, tokens, `settings.json` of the library, the identity of a box and
    its folder `sync`, and the schedules and folders of the backups.
  - A box without a library gets the lines of its journal back – its statistics.
  - A backup without MP3s leaves the albums folder. Every album that the data names and whose folder is missing is
    logged (`ERROR restore: album …`) and named on the card.
  - A box with a library gets the state of the library again at its next synchronization; the page says so before.
- `tinyjukebox-backup … --restore FILE` puts a backup back from the PC, the console of a box has `restore FILE`.
- A box that ends with the status 75 is started again: by systemd, as before, and now also by `start_sim.sh` on the
  PC.

## 0.34.0 – 2026-10-14

- **Two schedules for the backups: one without MP3s, one with MP3s**, set apart from each other. Each says how often
  – every so many days, weeks or months – at what time of day, how many backups of its kind are kept, and into
  which folder they go. An example: without MP3s every week and 52 kept, with MP3s every month and 2 kept.
  - "… are kept" counts only the backups of its own kind, also when both write into the same folder.
  - Months: the same day of the month as the last backup; a day that the month does not have is its last day.
  - If both are due at the same time, the one without MP3s is made first.
  - "Back up now" and "Back up now with MP3s": the backup goes into the folder of its kind and counts for its
    number. The table lists the backups of both folders with their kind.
  - The one schedule of before becomes the schedule of its kind, with its folder; the other one is off.
- **A box without a library makes backups by itself**, with the same two schedules on its page (tab "System", card
  "Backup"). Its folder is `backups-folder` of its config, else `backups` beside the albums folder. While the box
  works with a library the part is not shown and no schedule runs – the library makes the backups; the settings are
  kept for the day the box is unlinked.
- `/api/library/backups` returns and takes `{"plain": {…}, "albums": {…}}` in place of the flat fields; a box has
  the same under `/api/backups`. `tinyjukebox-backup` sets the second folder with `--library-folder-albums`, also on
  a box without a library.

## 0.33.0 – 2026-10-14

- **The backup of a box without a library holds its statistics.** Such a box counts what was played from the lines
  of its journal, the last 1000, and nobody else has them: they were lost with the box. Its backup holds them now as
  `json/journal.jsonl`, with or without MP3s.
  - Only the lines: not the id of the box and not what it exchanges with a library. A box that is set up again
    stays a new box for a library.
  - A box with a library needs nothing: the library has its journal, and the backup of the library holds it.

## 0.32.0 – 2026-10-14

The library needs this version first, then the boxes; a box installs it by itself.

- **Every backup holds the covers of the albums.** The cover of an album was the file `cover.jpg` or `cover.png` in
  its album folder, and the album folders are only part of a backup "with MP3s": a backup without them had the
  covers of the stations and none of an album. Now the cover of an album is kept in the images folder, as the cover
  of a station is, under the name `album-<8 characters>.jpg` (`.png`, `.webp`), and the album names it in
  `targets.json` (`cover`).
  - Setting a cover – on the page, with `PUT /api/albums/{id}/cover`, `set_album_cover` or
    `tinyjukebox-import-covers` – writes into the images folder. WebP is taken too, as for a station.
  - With a library the cover reaches the library and the other boxes like the cover of a station; it is no file of
    the album any more. A box has the cover of an album whose files it has not fetched.
  - A backup and putting it back need no change: the images folder is in every backup.
- **The covers that are there are moved.** At its start the library moves the stored cover (`cover.jpg`,
  `cover.jpeg`, `cover.png`) of every album folder into the images folder; a box without a library does the same
  for its albums.
  - A box with a library moves nothing: it removes its own stored cover when the library has moved it, and fetches
    the image. Every album with a stored cover has a new list of files once; no MP3 is fetched again for it.
  - Other images of an album folder (`folder.jpg`, `front.jpg`) and the images inside MP3 files are not touched. An
    album that names no cover shows them as before.
- **A box that still runs an older version** gets the catalog from the library as it knows it, without the covers
  of the albums: it shows no cover of an album until it has this version, and a change of an album made on it keeps
  the cover. A box with this version at a library that is older keeps a new cover of an album for itself.
- `GET /api/albums` and `GET /api/albums/{id}` have `cover`: the name of the cover image or `null`.

## 0.31.1 – 2026-10-14

- **The library says why a box went back from a version.** A version that fails its health check – it does not
  come up in time, it ends, or an unexpected exception happens on probation – is left by the start script of the box,
  and the reason was only on the card of the box (`state/history.log`, `tinyjukebox-slot status`). The library logged
  "version … is manual now (box …: )" with nothing after the colon. Now the box sends the reason of its last switch
  away from the version along, and the library shows it: in its log, in the tab "Versions" and in its journal.
- **A box whose list of switches began anew reports its switches again.** On a card that was set up again with the
  folder `sync` of before, box and library still counted the switches of the old card and took none of the new ones.

## 0.31.0 – 2026-10-13

- **The page of a box shows what its hardware does: the card "Hardware" in the tab "System"**, for admins, read
  twice a second while the tab is open.
  - Buttons: the level of the GPIO pin right now, the last event the driver reported (pressed, held, released) with
    its time, and how often the button was pressed since the start.
  - LED: the pattern it shows and which of its three lights are on, read from the pins.
  - RFID reader: the tag that lies on it, whether `rfid-reader` runs, and the reason if it does not.
  - A button without effect is told apart with it: if the level does not change when it is pressed, the button does
    not arrive at the pin (button, wiring); if the level changes and no event follows, the edges get lost; if the
    events are there, the cause lies behind the driver and the log says more.
  - `GET /api/box/hardware` delivers it. A box that runs without device drivers (`--no-hardware`) says so.

## 0.30.2 – 2026-10-13

- **The page of a box shows its name with "Rename" next to it** (tab "System", section "Library"), as the page of
  the library does: the dialog starts from the name the box has. The empty field "Name of this box" is gone.

## 0.30.1 – 2026-10-13

- **The playbook sets up a new box with slots.** On a box that never had the service, the run stopped at "Service
  starts with the box": the role `tinyjukebox` enabled the service before the role `slots` had written its unit.
  With slots the role `slots` alone installs and enables the unit now.

## 0.30.0 – 2026-10-13

**The project is called Tiny Jukebox, and this is its public repository.** The repository starts with this version:
it has one commit, and the releases up to 0.29.0 are not in it. What they brought is told below.

**A box and a library are set up again for this version.** Neither installs it by itself over an older one, because
everything that carries the name of the project has changed:

- The commands: `tinyjukebox`, `tinyjukebox-library`, `tinyjukebox-users`, `tinyjukebox-backup`,
  `tinyjukebox-fill-library`, `tinyjukebox-import-cards`, `tinyjukebox-import-covers`, `tinyjukebox-migrate-01`, and
  on a box `tinyjukebox-slot` and `tinyjukebox-start`. The Python package is `tinyjukebox`.
- A box: the folder `/srv/tinyjukebox`, the service `tinyjukebox`, the host name `tinyjukebox.local`, the key
  `tinyjukebox` at the top of its config, the variables of the playbook (`tinyjukebox_…`).
- The library on a NAS: the package `tinyjukebox-library` with the shared folder `tinyjukebox`. For DSM it is
  another package than the one before, not an update of it.
- The files of a release and of a backup begin with `tinyjukebox-box-` and `tinyjukebox-library-`.
- The data stays valid and is moved by hand: the albums, the JSON files, the id of a box and its link to the library
  (`identity/`, `sync/`), the data folder of the library, the users of the pages.
- On the pages everybody logs in once more and chooses the language again.

What else is new:

- **The releases need no token:** the repository is public. A library and a box without a library ask it without
  one; a token is only for a private repository.
- **The files of a release say which software of others they contain.** `README.md` lists it with its licences.
  The slot holds the licence texts of the two libraries inside `rfid-reader`, bcm2835 (GPL-3.0) and RPi-MFRC522
  (Unlicense), in `bin/rfid-reader-licenses/`. The code of the project stays under the MIT licence.
- **The paths in the config of a box may begin with `~`:** the log file, the albums folder and the JSON files. The
  box on the PC keeps its data below `~/tinyjukebox-sim` (`deploy/config/tinyjukebox-sim.yaml`, `./start_sim.sh`).
- **The slot for the boxes builds on a PC with Docker Desktop again.** Its emulator gets a system call wrong that uv
  uses since 0.13.0 to unpack archives; in the container of the build uv therefore unpacks as before
  (`UV_LEGACY_TAR_BACKEND=1`). When that can go is in
  [docs/developer/roadmap.md](docs/developer/roadmap.md), section 3.
- `./import_covers.sh FOLDER` takes the folder of the covers as its argument.
- `tinyjukebox-migrate-01` expects the albums of the old box below `/srv/tinyjukebox/albums`; for any other folder
  give `--old-albums-folder`.
- The data sheets of the components are not in the repository any more: `docs/hardware/README.md` has their key
  data and the links to them.

## 0.29.0 – 2026-10-12

The library needs this version first, then the boxes; a box installs it by itself.

- **A page on which nothing is done logs out after 30 minutes.** It says so on the login page. A mouse movement, a
  tap or a key counts as doing something, an upload that runs too – also in another tab of the same browser. The
  time is `rest-api/idle-timeout` in the config of a box or of the library, in seconds; `0` switches it off.
  Scripts that log in with a token are not concerned.
- **The tab "Versions" of the library hides old versions**: those that are not in the archive any more, that no box
  runs or has prepared and that the library does not run itself. "hide old versions that are not in the archive" is
  ticked by default; the page says how many are hidden and remembers the choice in the browser.
- **The covers of the radio stations have a folder of their own, `images`.** What was left of the card images of
  tags is gone from the synchronization: the kind `card`, `cards.json` and the folder `cards`.
  - A box moves its covers from `json/cards/` to `json/images/` at its first start. Whatever else is in
    `json/cards/` stays there, is not used any more and can be deleted.
  - The library keeps the list of the covers in `catalog/images.json`. Its config key is `images-folder` instead of
    `cards-folder`: the upgrade of the package on the NAS renames the key in the installed config and leaves the
    folder where it is. A library started by hand needs the key renamed in its config.
  - Only files named `station-…` count in that folder; other images are neither shown nor sent to the boxes.
  - `tinyjukebox-fill-library` has `--images` instead of `--cards`. A backup holds the folder as `images/`.
  - The playbook step that copied card images of the previous box (`migrate_cards_src`) is removed.

## 0.28.0 – 2026-10-10

This release contains 0.26.0 and 0.27.0 as well, which were not released on their own. In short: **a tag is what is
put on the box** (not "figure"), **the tab "Tags" is the one for everyday use** and the old tab "Tags" is "Tag
management", and **a tag has no picture of its own any more, it shows the cover of its album or station**. The
library needs this version first: it makes the covers of the albums out of the card images that tags had.

- **The tab "Albums" is a list.** Cover, title, number of tracks, playing time, tags, the day an album was added and its
  folder, one album per line; a click on a column heading sorts by it, a second click turns the order round. It is the
  technical view next to the tab "Tags".
- **The tab "Tags" has a filter and a list.** "All", "Albums" and "Radio" show all tags or only those of albums or
  of stations; "Tiles" and "List" switch between the cover tiles and one tag per line with its album or station and
  its progress. The page remembers the choice in the browser.

## 0.27.0 – 2026-10-10

- **A tag shows the cover of its album or station – there is no card image of a tag any more.** The picture of a tag
  was the card image if it had one and the cover otherwise, and most tags had both. Now there is one picture, the
  cover. The dialog "Choose card image", the column "Card" as a button, `/api/cards` and `/api/tags/{uid}/card` are
  gone; the column is "Cover" and shows the cover.
- **What was assigned becomes the cover.** At its first start the box (without a library) or the library makes the
  card image of every tag the cover of its album, if that album has no stored cover, and removes the assignment and
  the image. What cannot be adopted stays as it is: a tag without an album, an image that is neither JPEG nor PNG.
  A box with a library gets the covers from the library like every change of an album, so the library needs this
  version first. The kind `card` stays in the synchronization so that boxes and library of different versions
  understand each other; nothing creates it any more.
- `tinyjukebox-import-cards` cuts the pictures of cards out of a presentation into any folder; they become covers with
  `tinyjukebox-import-covers` ([docs/developer/operations.md](docs/developer/operations.md), section 6).

## 0.26.0 – 2026-10-10

- **One word for what is put on the box: tag.** A disc, a figure, a card – anything with an RFID chip is a tag, and
  the page, the manuals and the developer documents say so everywhere. "Figure" is gone, and so is "card" for the
  SD card: it is the SD card now.
- **The tabs are named after what they do.** The tab "Figures" is now "Tags": the tags with their images, a click
  places a tag on the box. The tab that was "Tags" is now "Tag management": name, UID, link and progress, and where
  tags are created, linked and deleted. "Tags" stands right after "Now" (in the library after "Versions").

## 0.25.0 – 2026-10-10

- **A box shows its own statistics.** The page of a box has the tab "Statistics", for every user: what was listened
  to on this box per figure, per day with the hours, per station, the skipped tracks, and the operation per version.
  The library does the counting, the box asks it when the tab is opened; if the library cannot be reached, the tab
  says so. A box without a library counts its own journal, the last 1000 entries. A box that is newer than its
  library says that the library has to be updated first – so the library needs this version too.
- **Statistics of the library with several boxes:** "All boxes" or one box is chosen at the top of the tab. Until
  now everything was summed over all boxes, also what belongs to one child: the listening time of a day and the
  hours between which something was listened to.
  - One box chosen: exactly what the page of that box shows.
  - "All boxes": figures, stations, skipped tracks and versions are summed as before; every figure names the boxes
    it played on, with the listening time (until now a tooltip). A new table "Boxes" shows listening time, how often
    a figure was placed and when last; a click on a box chooses it. It replaces the sentence "In total: …".
  - "Days" has one row per day and box, never a sum over the boxes.
  - With one box the tab looks as before.
- `GET /api/library/statistics` takes `?box=`; `days` has an entry per day and box, `boxes` of a tag is a list, and
  `boxes` names every box with `plays` and `last`. New: `GET /api/box/statistics` on a box and
  `GET /api/sync/v1/boxes/{box}/statistics` for the boxes ([docs/developer/rest-api.md](docs/developer/rest-api.md)).

## 0.24.0 – 2026-10-10

- **The library keeps five versions of the box software at most**, and the number is set on its page, tab
  "Versions". Older ones leave the archive, at once and whenever a release arrives. What runs on a box or lies in
  one of its slots always stays, and so do the version of the library itself and the newest one in the state `use`.
  A version that left the archive is fetched again from the releases when a box needs it. Until now the limit was
  `keep-versions` of the config only, and without it the archive kept everything.

## 0.23.0 – 2026-10-10

- **The library shows on which boxes a version is prepared.** The tab "Versions" has a column "Prepared on" next
  to "Runs on": it names the boxes that have fetched a version and try it the next time they are switched on. An
  admin sees there that a release has been distributed although no box runs it yet.
- The version the library runs itself is named in that table, in the column "Runs on", and no longer in the text
  above it.
- **Statistics, "Operation":** per version also the days it was installed, the hours the boxes were switched on
  with it and the hours of these in which something was listened to. A box notes every ten minutes that it is
  switched on; versions before this one did not, for them the time until the last event of a start counts.
- **Statistics, "What was listened to?":** a click on a column heading sorts the table by it, a second one turns
  the order round.
- **Statistics, "Days":** the hours are shown as spans, "7:00–9:00" instead of "7:00, 8:00".
- **Statistics:** the figures that were never placed have a block of their own at the end of the tab.

## 0.22.0 – 2026-10-10

This release contains 0.19.0, 0.20.0 and 0.21.0 as well, which were not released on their own. In short: **a box
without a library updates itself from the releases on GitHub**, a box can be unlinked from its library on its page,
and the first version of a box is its emergency version. What changed in the slot scripts reaches a box with the
next run of the playbook, not with this release.

- **A box without a library can ask before it installs a release**: with "Updates: ask" it downloads nothing by
  itself and shows admins a banner at the top of every tab, "Version … is available.", with "Install", "Skip" and
  "What is new". An admin who never opens the tab "System" learns of a release that way.
- "Install" downloads in the background; the version starts the next time the box is switched on. "Skip"
  (`tinyjukebox-slot skip VERSION`) makes the box ask again only for the next release.
- Of releases that follow each other the box asks for the newest one only; the ones in between are never
  downloaded.
- The concept for boxes without a library is implemented with this version and merged into
  [docs/developer/library.md](docs/developer/library.md), sections 4.20, 6.10 and 6.16.

## 0.21.0 – 2026-10-10

- **A box without a library updates itself from the releases on GitHub.** Once a day it asks for the newest
  release, installs it into a slot and tries it on its next start, as it does with a version from a library. A box
  that is linked to a library never asks GitHub ([docs/developer/library.md](docs/developer/library.md),
  section 6.16).
- **A release waits three days** before such a box installs it by itself (`WAIT_DAYS` of `slot.conf`). The
  section "Version" shows the release and the day; "Install now" does not wait. A release that is marked as
  pre-release or deleted on GitHub within that time never reaches a box.
- **Three update modes**, for every box: automatic, by hand only, and off – a box that does not even look. The
  section "Version" has a list for them instead of the button, and names where the versions come from.
- The repository may be public or private: a box can be given a token (`vault_releases_token`), and the library
  asks a public repository without one. It sent an empty token until now.
- The playbook: `tinyjukebox_releases`, `tinyjukebox_release_wait_days`, `tinyjukebox_releases_token`. A new box without
  a library fetches its first version itself and needs no slot archive.
- `sync/look-hours` in the config: how often a box without a library looks (default 24).
- All of this is in the slot scripts, which only the playbook brings to a box.

## 0.20.0 – 2026-10-10

- **A box can leave its library**: "Unlink from the library" in the tab "System", section "Library", for admins;
  `unlink_library YES` on the console; `DELETE /api/box/library`. The box keeps everything on its card – figures,
  albums, stations, progress – and works on its own at once. The library is not asked and need not be reachable
  ([docs/developer/library.md](docs/developer/library.md), section 4.20).
- The page asks first and says what is lost: the changes the library has not taken over, and the albums that are
  not completely on the card (`absent` of `GET /api/box`).
- A box that stopped playing because its library is too old plays again as soon as it is unlinked.
- Linking again works like the first time; the library recognizes the albums and stations it has already.
- The section "Version" names where the versions of the box come from (`library` of `GET /api/box/system`).
- Until now the address of a library could be entered on the page, not removed.

## 0.19.0 – 2026-10-10

- **The first version of a box is its emergency version.** When the playbook installs the first version on a box,
  the same version is put into the fallback slot, before it has started once; until now the slot stayed empty until
  somebody chose "Keep as emergency version". An update never touches it
  ([docs/developer/library.md](docs/developer/library.md), section 6.10).
- A box that was set up before has no emergency version. The next run of the playbook gives it the version it runs,
  if that version is confirmed. The change is in the slot scripts, which only the playbook brings to a box: a
  release alone changes nothing on a box.
- A first version that fails its health check is not left for its own copy in the fallback slot: the box stays on
  it and reports the problem, as before.
- First stage of the concept for boxes without a library, which is implemented with 0.22.0.

## 0.18.0 – 2026-10-10

This release contains 0.16.1, 0.16.2 and 0.17.0 as well, which were not released on their own. In short: **the web
page is English and German and starts in English** – the language is chosen in the list "Deutsch / English" on the
login, at the top right or in the tab "System", once per device and box.

- **What the box or the library refuses, the page says in its language**: "Den Tag … gibt es schon." instead of
  "tag '…' exists already.". The answer to a refused request keeps its English `error` and names a `code` and the
  values of its text; the page has the text ([docs/developer/rest-api.md](docs/developer/rest-api.md), section 3).
  About 60 errors have a code – those a user of the page runs into. The same for why a box works without the
  library (`alone_code` of `GET /api/box`), a box that does not fit the library, and a search for versions that
  fails.
- English on every page, as before: the log, the output of the version commands, the problems of the files of an
  album, and the reason of a failed backup or stream.
- **Quick guide and manual in English**, next to the German ones: `docs/user/en/` and `docs/user/de/`, with the
  file names `quick-guide.md` and `manual.md` in both (they were `docs/kurzanleitung.md` and
  `docs/benutzerhandbuch.md`). Both languages have the same sections with the same numbers.
- The package of the library for the NAS is English, and German when DSM is set to German: name, description, the
  dialog of the installation.
- The headset of a box without hardware is called "Virtual headset".
- The developer documents quote the English labels of the page.

## 0.17.0 – 2026-10-10

- **The web page is English and German.** It starts in English; the language is chosen in a list "Deutsch /
  English" on the login, at the top right and in the tab "System". The browser keeps the choice, for this device
  and this box, and the page loads again in the chosen language.
- Dates, times, sizes and the order of lists follow the settings of the browser.
- German quotation marks close as “ instead of a straight quotation mark.
- `scripts/pages/check.sh` chooses German for what it looks at, and goes through every tab of both pages in
  English as well.

## 0.16.2 – 2026-10-10

- The texts of the web page are in a file of their own, `web/web/texts/de.json`; `index.html` and `app.js` name
  them by keys ([docs/developer/rest-api.md](docs/developer/rest-api.md), section 5). The page shows what it
  showed before – this is the first stage of the page in several languages.
- Numbers get their singular: "1 Datei", "1 Album hinzugefügt", "1 Änderung", "1 Start" instead of "1 Dateien" and
  the like.
- `scripts/pages/check.sh FOLDER` also writes every text of each tab into the folder.

## 0.16.1 – 2026-10-10

- The page of the library, "Figur scannen": if Chrome does not let the page use NFC, the page says how it is
  allowed again in Chrome, and the text stays below the button. It tells a page that is blocked from a question
  that was only closed.

## 0.16.0 – 2026-10-10

- **Backups** ([docs/developer/operations.md](docs/developer/operations.md), section 10): the data of a box or of
  the library as one archive, without the albums (small) or with them. Only admins make one, and nothing secret is
  part of it: no certificate, no user, no password, no token.
  - On the page: tab "System", card "Backup" – "Backup herunterladen" and "Backup mit MP3s herunterladen".
  - REST API: `GET /api/backup[?albums=1]`.
  - On the PC: the new command `tinyjukebox-backup ADDRESS --user … [--albums] [FOLDER]`, which keeps no backup that
    was cut off on the way.
  - On the console of a box: `backup FOLDER [ALBUMS]`.
- **The library makes backups by itself** ([docs/developer/library.md](docs/developer/library.md), section 4.19):
  every so many days at a time of day, with the albums or without, into a folder, and it keeps a number of them.
  Schedule and folder are set on its page (tab "System", "Regelmäßig sichern"); "Jetzt sichern" makes one at
  once, and the backups of the folder can be fetched and deleted there. The folder is `backups` in the shared
  folder `tinyjukebox` until another one is set – on the page, or with `tinyjukebox-backup ADDRESS --library-folder
  FOLDER`. The requests are below `/api/library/backups`.
- A backup is put back by hand: stop, unpack into the folders, start
  ([docs/developer/operations.md](docs/developer/operations.md), section 10.3).
- The library takes the slot of its package into the archive again if the files of that version are gone, as after
  its data was put back from a backup.

## 0.15.2 – 2026-10-09

- **"Figur scannen" is in the tab "Tags"** of the library, in the card "Neuen Tag anlegen" next to the fields that
  create a tag by hand, and no longer in the tab "Figuren". The figure that was read is shown in that card, and the
  table of the tags has the new tag at once. A user sees the card with the button only.
- On a phone the button is always there. Where the browser cannot read figures, a tap says why – an iPhone, another
  browser than Chrome, a page without HTTPS, or Chrome that does not offer NFC to the page.

## 0.15.1 – 2026-10-09

- **Documentation only; the software behaves as 0.15.0.** The concept documents of the library and of the phone as
  reader are gone: what describes the system is in [docs/developer/library.md](docs/developer/library.md), and the
  comments in the code, the scripts and the playbook that named a concept refer to it. The documents for
  developers describe the box as it runs now, with slots and the library. The test bed is not kept;
  [docs/developer/library.md](docs/developer/library.md), section 9, describes it and how to build it again.

## 0.15.0 – 2026-10-09

- **The tab "Alben" sorts the albums** by title, figure (the name of the tag), playing time or the day they were
  added, in both directions. Albums without a named figure or without a known playing time come last. Sorted by
  day, the list shows the day; the dialog of an album always names it. The list starts sorted by title instead of
  by the number of the album.

## 0.14.0 – 2026-10-09

- **The token for the releases is set on the page of the library**: tab "Versionen", section "Zugang zu den
  Releases" (`PUT /api/library/releases/token`). The library asks the git server with the token before it keeps
  it, and looks for a release at once – no file on the NAS and no restart. A token file of the config still counts
  as long as no token is set on the page. Without a token the page says so instead of "no repository … or its token
  is missing".

## 0.13.1 – 2026-10-09

- The tab "Statistik" of the library names the album or station of every figure (column "Album / Sender"): what
  the figure played last. `GET /api/library/statistics` has it as `album` of a tag.

## 0.13.0 – 2026-10-09

- Fix: the tab "Alben" showed "0 Titel" for an album that got its files after it was listed for the first time –
  every album of a library that was filled while its page was open, and an album that a box fetched later. The list
  of the tracks is read again whenever the playlist changes.
- **The tab "Alben" shows the playing time of every album**, and a box with a library shows the number of tracks
  and the playing time also of the albums that it has not fetched: the library notes both in the catalog (`tracks`,
  `length` of an album) as soon as it has the files. `GET /api/albums` has `length` (seconds) for every album.
- **A radio station may have a cover image.** It is set in the tab "Radio" (click the image of the station), in the
  console (`set_radio_cover`) and with the REST API (`PUT /api/radios/{id}/cover`), and shown in the tabs "Jetzt",
  "Radio", "Tags" and "Figuren". The image is kept with the card images, so with a library it reaches the library
  and the other boxes; its name is stored with the station in `targets.json` (`cover`).
- `tinyjukebox-import-covers` also sets the covers of the radio stations: the image `radio-<id>.jpg` of the folder
  becomes the cover of the station with this id.
- The first start of the library with this version reads every track once.

## 0.12.0 – 2026-10-09

- **The library collects which mp3 files are broken.** It checks every track itself as soon as it has the files of
  an album, and again when they change. A box whose decoder finds errors in a track while playing reports the file
  with its journal and plays on, as before. The tab "Alben" of the library marks such an album with "defekt"; the
  album dialog names the files that boxes reported, and an admin drops reports there ("Meldungen verwerfen",
  `DELETE /api/library/albums/{id}/broken`). What is noted about a file ends when the file is replaced.
- The first start of the library with this version reads every track once.

## 0.11.1 – 2026-10-09

- Fix: the library ignored a change of the files of an album that a box made, if the entry of the album itself
  stayed what it was – a new cover alone, a playlist written anew. The box then took the older files of the library
  for a change made there and undid its own change. 0.11.0 fixed only a part of this.

## 0.11.0 – 2026-10-09

- **Every album has a title and may have a cover image.** Both are set when the album is created and can be
  changed later: in the album dialog of the web page (edit the title, "Titelbild ändern"), in the console
  (`set_album_title`, `set_album_cover`) and with the REST API (`PATCH /api/albums/{id}`,
  `PUT /api/albums/{id}/cover`).
- A new album needs a title: the tab "Hochladen" asks for it and takes an optional cover image, "Ordner als Album
  hinzufügen" has a field for it, `add_album` is now `add_album FOLDER TITLE …` and `POST /api/albums` wants
  `{folder, title}`. "Neue Ordner suchen" and `add_new_albums` show the title every folder gets – the album that
  its first track names, otherwise the name of the folder. `GET /api/albums/new` returns `[{folder, title}]` instead
  of folder names.
- The title is stored with the album in `targets.json`; an album that was there before keeps showing the title
  from its tracks or its folder until one is set. The cover is `cover.jpg` or `cover.png` in the album folder. With
  a library both reach the library and the other boxes.
- `tinyjukebox-import-covers ADDRESS FOLDER --user ADMIN` sets the titles and covers of many albums at once from a
  folder with the images and a `captions.json` (album id → image and title)
  ([docs/developer/operations.md](docs/developer/operations.md), section 5).
- Fix: a box with a library could lose a change of the files of an album that was made on the box (a new cover, a
  playlist written anew) if another change of the same album had been made just before. The box took the older
  list of files of the library for a change made there and deleted its own new file.

## 0.10.1 – 2026-10-09

- The dialog that chooses the card image of a tag shows below every image the album or station of the tags that use
  it – the title as in the select of the tags – and lists the images in the order of these titles. Before, an image
  without text on the card could only be told by its picture.
- The card images of our box are no longer part of the repository. `--tags migrate` of the playbook copies card
  images to a box without card images from a folder on the PC, `migrate_cards_src`, if it is given
  ([docs/developer/deployment.md](docs/developer/deployment.md), section 7).
- The tab "Statistik" of the library is open to every user of the page, no longer to admins only.
- The playbook sets a box up with slots and self-update by default (`tinyjukebox_slots: true`). A box that has no
  slot yet needs the slot archive of a release at the next run (`-e tinyjukebox_slot_archive=…`); the playbook stops
  and says so if it is missing.

## 0.10.0 – 2026-10-06

- **The phone as reader of the library** ([docs/developer/library.md](docs/developer/library.md), section 5.1): on
  the page of the library, tab "Figuren", the button "Figur scannen" reads a figure that is held to an Android phone
  (Chrome, NFC) and adds its tag to the catalog, not linked. A new figure gets into the system without a box; an
  admin names and links it right there. Every user of the page may scan. Nothing is written to a tag, and no other
  open page reacts to a scan.
- The library has the request `PUT /api/tags/{uid}` for it.
- Tried in a browser with a stand-in for the reader of a phone; with a real phone it is still to be tried
  ([docs/developer/roadmap.md](docs/developer/roadmap.md), section 1).

## 0.9.2 – 2026-10-06

- Fix: the package of the library did not start on the NAS ("System failed to start"): the start script ran the
  library in a way that ended at once without doing anything.

## 0.9.1 – 2026-10-06

- Fix: a stop signal that arrived at the very moment the start had finished was not handled; the threads of the
  service kept the process alive until systemd killed it.

## 0.9.0 – 2026-10-06

The central library, synchronization and self-update of the boxes
([docs/developer/library.md](docs/developer/library.md)).

- **Library** (`tinyjukebox-library`): a program of its own for the NAS or the PC. It holds tags, albums, stations and
  card images for all boxes, the positions and settings of every box, the journal, and the archive of box versions.
  Its web page is the page of a box with the tabs "Boxen", "Versionen" and "Statistik" in place of the player.
- **Synchronization**: a box works with a library as soon as its address was entered (tab "System", or the
  playbook). It keeps what the library delivered and its own unconfirmed changes, and plays without the library.
  Changes made on a box without a connection reach the library later; in a conflict the library wins and logs it.
  Targets created on a box have a provisional id until the library has assigned the final one.
- **Albums on demand**: a box fetches the audio files of an album when its figure is placed and plays as soon as the
  track that is due has arrived. An album uploaded to a box is passed on to the library. While the playback waits
  for a file the LED flows green, aqua, blue.
- **Space on the card**: a box that works with a library keeps `sync/keep-free` MB free (default 500) by deleting the
  audio files of the albums not played for the longest time; they are fetched again when needed.
- **Slots and self-update**: `tinyjukebox-slot`, the start script and the playbook role `slots` (off by default,
  `tinyjukebox_slots`). A box with slots fetches released versions from the library, tries a new one on its next start
  and goes back if the health check fails. The tab "System" shows the version and offers update, revert, fallback
  and the update mode.
- **Journal and statistics**: a box notes what was played; the library shows it per figure, per day and per station.
- **First filling**: `tinyjukebox-fill-library` sends tags, albums, stations and card images from the PC to the library.
- **Release pipeline**: a push to `master` that changes the software becomes a release with the signed slot for the
  boxes and the package for the NAS. `build_package.sh` builds the package with the library in it.
- The hardware libraries (`rpi-lgpio`, `pyalsaaudio`, `dbus-fast`) are the extra `box` now: a box installs
  `tinyjukebox[box]`, the library needs none of them.
- The answer of `/api/session` and `/api/login` names the kind of page (`mode`: `box` or `library`).

## 0.8.0 – 2026-10-02

- **The RFID reader is read by a program in C++**, `rfid-reader`: the read loop that the box has used since 2021,
  now without MQTT. It notices reliably that a tag is taken away. The driver in Python that did not is gone.
- `tinyjukebox-migrate-01` converts the tags and albums of a box with version 0.1.
- The Ansible playbook sets up a box on a fresh SD card.

## 0.7.0 – 2026-10-02

- **Bluetooth headset**: one headset is paired with the box by holding two buttons. Its buttons work, it has a
  volume of its own, and a headset that gets lost pauses the album.
- **Pause**, on the box, on the web page and in the console.

## 0.6.0 – 2026-10-02

- **Internet radio stations**: a tag can start a station instead of an album. The tab "Radio" adds, changes and
  tests stations; a stream that breaks off is connected again.

## 0.5.0 – 2026-10-01

- **One process instead of four.** The framework and the hardware drivers, until then the packages pelops-core
  and pelops-devices, are part of the project now (`tinyjukebox.core`, `tinyjukebox.devices`). The parts of the box talk
  to each other inside the process; no MQTT broker is needed any more.

## 0.4.0 – 2026-10-01

- **REST API and web page**, and the box runs on a PC without hardware (`--no-hardware`).
- **A current Python project**: `src` layout, `pyproject.toml`, uv, ruff, mypy in strict mode, type hints
  throughout.
- Console and REST API use the same logic. The JSON files are written in one step, and a stop signal ends the
  service cleanly, with the position saved.
- Fixed: albums and tracks with `[…]` in their names were reported missing; albums with `*.MP3` files failed the
  check of the data; the end of a track together with "next" could skip a track.

## 0.2.0 and 0.3.0 – 2026-09-27

- The player uses ffmpeg instead of pygame.
- The console links a tag that lies on the box with an album.
- Tests for the data, the state machines and the console.

## 0.1.11 – 2026-09-26

- After almost four years in which the box just played, work on it starts again. The software gets a new
  repository: the state on the PC, with the classes for the data and the player of 2020.

## 0.1.10 – 2022-12-16

- The box drives its buttons and its LED itself, and the volume control becomes a service of its own. The driver
  service of pelops is not needed any more.
- The circuit is drawn for the sixth time since 2019. A board that debounces the buttons, designed and tried this
  year, is not used.

The box played with this version until October 2026.

## 0.1.9 – 2021-11-21

- The tags are read by a program in C++, because the driver in Python did not notice reliably that a tag was taken
  away.
- The UIDs get the form they have today.

## 0.1.8 – 2020-12-08

- The classes for tags, albums and positions are written anew, with unit tests and a check of the data.
- The player gets an interface and a stand-in for the tests.

## 0.1.7 – 2020-01-02

- The JSON files are saved at every start.
- The box gets a console.

## 0.1.6 – 2019-12-30

- The end of a track is detected properly, and the gap between tracks can be set per album.
- A held volume button keeps changing the volume, and an album can be started from the beginning.
- The LED shows the state of the box.
- The JSON files are checked against a schema and written under a lock.

## 0.1.5 – 2019-12-24

- The box is ready for Christmas: a Raspberry Pi 3 with an RFID reader, four buttons, an RGB LED and a speaker, in
  a printed case. The commit of this day is called "v1.0!!!".

## 0.1.4 – 2019-12-22

- An RGB LED.
- At its start the box asks the reader at once whether a tag lies on it.

## 0.1.3 – 2019-12-21

- The box remembers for every tag where its album stopped.

## 0.1.2 – 2019-12-20

- "One album can be played."

## 0.1.1 – 2019-12-19

- The player in Python, begun as a copy of the sound service of pelops, started by a service of systemd.
- Tags and albums are kept in JSON files.

## 0.1.0 – 2019-12-15

- The project starts: the configuration of the drivers, the start scripts and the first parts for the printer.

## Before 0.1 – 2018 and 2019

The software grew out of pelops, a collection of MQTT microservices for the Raspberry Pi that was begun in February
2018: drivers for sensors, displays, inputs and outputs, a common core for configuration, logging and messages, and
services built on them – from April 2018 one that plays a sound when a message arrives. The drivers had 37 releases
until December 2019, the core 51 until April 2020, in about 400 commits. The box still uses both.
