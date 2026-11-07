# The app

The app for Android phones and tablets: a phone as a box, and the page of the library in the app. It works with the
library as a box does ([library.md](library.md)); numbers in parentheses in the tables are sections of library.md.

The browser is enough to manage the library ([library.md](library.md), section 5.1). It is not enough for the second
use: **a child puts a tag on a phone or tablet and the album plays**, like on a box. The app for Android does that. It
adds a way to listen; it replaces nothing. The code is in `app/` (section 9).

| Need | Browser | App |
| --- | --- | --- |
| Read a tag | Web NFC: Chrome on Android only, HTTPS, a permission question | NFC of Android, no question, reads while the app is on screen |
| Notice that the tag is taken off | no | yes (section 4) |
| Play with the screen off, headset buttons, lock screen | limited | the media session of Android |
| Play without the library | no | albums are kept on the phone, as on a box |
| The self-signed certificate | a warning accepted by hand | the app accepts exactly the certificate of the library |

Not measured yet: how a tag behaves on a real phone (section 10).

## 1. Two modes

| | Simple | Expert |
| --- | --- | --- |
| For | the child; the phone is a box | the adult managing the library |
| What it is | a small native player | the page of the library, in the app |
| Login | none: the device is paired (section 5) | the login of the page (user or admin) |
| Offers | a tag on the phone plays its album; big buttons; the tags as tiles to tap | everything the page offers; the settings of the app |

- **The app is paired first, always** – also on the phone of an adult who wants expert mode only. Pairing gives the
  app the address and the certificate of the library; without them it shows nothing but the way to pair.
- **Simple mode is the mode after pairing.** The button "Expert" asks for the login of the page; the way back is
  free. A child reaches neither the page nor the settings of the app this way.
- This guards the app, not the phone: the home button leaves every app. A phone that is to stay in the app is pinned
  with the screen pinning of Android; the app does nothing for it.

## 2. Expert mode

A `WebView` shows the page of the library (`ExpertActivity`). Nothing of the page is built a second time: a new tab,
text or right is in the app with the next load. What the app adds:

| Part | How |
| --- | --- |
| Certificate | the `WebView` asks the app about the self-signed certificate; the app accepts the one of the pairing and no other |
| "Scan tag" (tab "Tag management") | the object `TinyJukeboxApp`, a web message listener for the origin of the library only: the page sends `scan-start`/`scan-stop`, the app answers with the UID. `app.js` uses it where it is there and `NDEFReader` otherwise |
| Choosing files | uploads and a backup to put back open the file chooser of Android. "Choose folder" (`webkitdirectory`) does not exist in a `WebView` and is hidden in the app |
| Downloads | a backup is fetched by the app itself – with the cookie of the page and the pinned certificate – and saved to "Downloads" |
| Questions, back button | `alert()`, `confirm()`, `prompt()` as dialogs of Android; "back" closes an open dialog of the page or leaves expert mode |
| Links | a link to another address (Spotify, GitHub) opens in the browser of the phone |
| Login and logout | the page tells the app `session:<role>` and `logout`. Logged in, the button "App settings" works; a logout – by hand or after 30 minutes without a touch (`idle-timeout`) – leads back to simple mode. Leaving expert mode logs out and forgets the cookie, so "Expert" always asks for the login |

The settings of the app (`SettingsActivity`), reached only while the page is logged in: the name of the device as the
library has it, the connection, the versions of app and library and the app the library hands out, the cache with its
limit and content, the licences of the libraries in the APK, and "Unpair". Expert mode and the settings say when app
and library do not fit together (section 5); the banner of expert mode also has the link to `/app.apk`.

## 3. Simple mode

The screen shows what a box shows: the cover, title, track and position, play/pause, next, previous (held: from the
start). Below, **the tags as tiles** with the cover of their album or station, for a phone without NFC or a child
who prefers to tap. A band of colour follows the LED of the box. `Jukebox` is one small state machine with the states
of `STMOperation` that apply:

| State | Band | |
| --- | --- | --- |
| `WAIT_FOR_TAG` | green | |
| `CHECK_TAG` | aqua | an unknown tag: one pass asks the library (3 s), then the tag is created not linked and reported |
| `UNMAPPED_TAG` | yellow | a sentence on screen, no sound |
| `UNKNOWN_ALBUM` | yellow and red | linked to something the catalog does not have |
| `FETCH_ALBUM` | green, aqua, blue flowing | the album is loaded into the cache, beginning with the track that is due |
| `PLAY_ALBUM` | blue; paused white and blue; waiting for a track flowing; at the end blinking blue; aqua while the Spotify app is not connected yet or not yet at the saved place | an album, a station or an entry of Spotify |
| `BROKEN_ALBUM` | red and black | cannot be played; without the library: the album is not on the phone |
| `ELSEWHERE` | yellow | an entry of Spotify without the Spotify app or the registration: "This tag plays on a box" |

| Event | The app, as a box |
| --- | --- |
| A tag is placed, or a tile tapped | plays its album from the saved position, or its station |
| Another tag or tile | ends the first (`end`, "tag replaced"), plays the other |
| The tag is taken off, the tile tapped again | ends the playback and saves the position |
| The album has played to its end | the band blinks blue; the tag placed again starts from the beginning |
| The next track has not arrived | waits at the end of the track; "next" pulls it forward |
| The tag on the phone is linked on the page meanwhile | it plays (`recheck_tag()` of a box); nothing else reacts to a change of others |
| No library | plays what is in the cache; changes wait |

- The player is Media3/ExoPlayer on the thread of the jukebox, in a foreground service with a media session
  (`PlaybackService`): screen off, lock screen, headset buttons. Its buttons go to the jukebox like the buttons of a
  box. Headphones taken off or another app taking the sound pause it, as the button does.
- **What other apps may do:** every app may connect to the media session – the lock screen, a headset, a watch, a car.
  It gets play/pause, next, previous and stop – stop is a pause – and may read what plays; it can neither set the
  volume nor another track or a place in it, so the volume limit holds. Swiped away, the app keeps its session while
  something plays, is loaded or waits for its next track.
- The network is never asked on the thread of the jukebox: the playlist of a station and an album that changed in the
  library are fetched on threads of their own.
- **Volume:** the keys of the phone set the volume. The app plays with at most `max-volume` of the settings of this
  device for speaker or headphones – whatever is connected – of what the phone is set to. The limits are set on the
  page of the library, at the phone in the tab "Boxes".
- **Stations** come from the internet, not from the library: an m3u or pls playlist is resolved to its stream – at
  most 64 kB of it in at most 10 seconds, as on a box.
- **Entries of Spotify** play in the Spotify app of the phone, steered with Spotify's App Remote SDK
  (`SpotifyPlayer`), as a box plays them with Soloist: shuffle and repeat off, the entry at the saved place of its
  tracks and the saved second – from the beginning if the track there is another one –, the buttons, the position
  with `track-uri`, `track` lines with `uri` and `title`, and the end when Spotify plays something else by itself
  (autoplay). The sound is the Spotify app's: the volume limit is held by setting the volume of the phone back while
  an entry plays. It needs the registration of the app at Spotify by the owner, once: on developer.spotify.com an app
  with the API "Android", the redirect URI `tinyjukebox://spotify`, the package name and the SHA-1 of the signing
  certificate (the settings of the app show both), and the Spotify accounts of the phones under "User Management" –
  five at most, and Spotify asks Premium of the owner. Its client ID is entered in the tab "Spotify" of the library,
  which gives it to every phone as the setting `spotify-client`. Without the Spotify app or the client ID, or if
  Spotify refuses the registration, the tag says that it plays on a box.
- **Tags are read only:** the app never writes to a tag, only the UID counts, in the form of `rfid-reader`.
- The position is saved every 5 seconds and reaches the library with a change of track, a pause or the end.

## 4. Presence of a tag

Android keeps the connection to a tag in range and reports when it is gone (`TagReader`): reader mode, then
`NfcAdapter.ignore()` with a debounce of 0.5 seconds; where that fails, the tag is asked every 250 ms (READ of page 0,
or connecting again) and is lost when it did not answer for 0.5 seconds. A lost tag counts as taken away only when it
is not found again within a second; found again, nothing changes. Only the watch of the tag found last counts. That
is `TAG_ON` / `TAG_OFF` of `rfid-reader`; `adb logcat -s TagReader` shows each tag found and removed. A Pixel 9a
found a tag of the box ten times a second and reported it lost in between – without that second, the album started
again each time.

**Android reads tags only while the screen is on, the phone unlocked and the app visible.** Therefore:

- **Only a tag taken off while the app sees it counts.** If the screen goes off or the app leaves the screen, the
  playback goes on. When the app is visible again it looks for the tag for 2 seconds: there – nothing changes; no tag
  at all – the playback ends, as if the tag had been taken off just then; another tag in its place – it has replaced
  the first one and plays.
- **While simple mode is visible it keeps the screen on,** dark and dimmed after 30 seconds without a touch; the first
  touch only wakes it. The power button still switches the screen off.
- A tag that is taken off and placed again within 1 second counts as never taken off. One tag at a time; a tile
  stays until it is tapped.

How well this works in the hand of a child is not measured yet (section 10). If presence proves unreliable,
simple mode works by touch – the same question as D1 of the desktop box.

## 5. The app is a box of its own kind

The app needs what a box needs – catalog, albums, positions, a journal – so it **registers as a box** and uses
`/api/sync/…`. It gets positions of its own ("Anna's phone" continues where Anna's phone stopped), a row in the
statistics, a name and volume limits on the page, and it is removed and taken over like a box.

| Part of a box | App |
| --- | --- |
| Handshake, key, pinned certificate (4.14) | yes, with a one-time password; the name it sends is the model of the phone, renamed on the page |
| Interface versions (4.10, 4.15) | yes, below |
| Copy of the catalog, compared by checksum (4.3) | yes |
| Positions and settings of this device (4.2) | yes |
| Outbox (4.4) | two kinds of change: a position, and an unknown tag created by the software, not linked |
| Answers (4.5) | `applied`, `conflict` (a position the library changed on purpose: its value stays), `rejected` |
| Positions the library lost (4.12) | sent again |
| Provisional ids, uploading albums and covers (4.6, 4.8) | no |
| Albums on demand with manifest and checksums (4.8) | yes: the cache (section 6) |
| The covers of the images folder (4.8) | yes, all of them |
| Journal (7) | yes, below |
| Status report | the version, `host` and `fit`; no slots, no update mode |
| Passes of the synchronization (4.17) | on start, when a tag is placed, after a change, every minute while something plays, otherwise every 30 minutes; while the app is visible also 30 seconds after the last pass while the library cannot be reached, and every 10 minutes while app and library do not fit together |
| Events (4.9) | only while the app is visible, from the first pass that reaches the library, and not while app and library do not fit together; when the connection breaks, a pass finds out whether the library is gone |
| Slots, health check, self-update, defective versions, broken files (6, 4.18) | no |

**Pairing.** An admin opens the tab "Boxes", section "Add a phone", button "Pair". The page shows a QR code for 10
minutes: `tinyjukebox:pair?library=<address>&password=<one-time password>`, the address the browser uses. The app scans
it (ZXing, without the services of Google) and runs the handshake of [library.md](library.md), section 4.14 with the
one-time password in place of the bootstrap password; the handshake proves the certificate, the app keeps it and accepts
no other. The password is used up; a new one makes the old one invalid, and so does a restart of the library, which
keeps it in memory only. The QR code is the only way: a short number to type could be guessed from an overheard proof.

- **A pairing is complete or absent:** address, key and certificate are written in one step first
  (`identity/pairing.json`), then the three files from it; an app ended in between completes them on its next start –
  the password is used up by then. A certificate that cannot be read means "not paired".
- **The library decides what was paired:** the bootstrap password registers a `box`, the one-time password an `app`
  (`kind` of a box in `boxes/<id>.json`; boxes from before are `box`).
- The app generates its id on its first start. Deleting the data of the app makes a new device; an admin lets it
  take over the old one on the page.
- "Unpair" in the settings deletes key, certificate, copy, positions, the journal lines not delivered and the cache;
  the numbers stay. The library keeps the device until it is removed there.

**What a phone may do.** A phone of a child is lost more easily than a box, and it needs nothing else: the library
applies only positions and a tag that the software created as not linked (`Store._app_may()`), everything else is
`rejected`; it refuses files of albums and covers and the versions of the box software (`403`), and `GET
/api/sync/versions` lists none. Reading is as for a box.

**Versions.** The app has the version of the project and names itself `app-<version>` in `X-Box-Version`, every
change and every journal line, so that the statistics per version and the defective versions of the boxes are never
mixed with it; a version that is no number counts as new for `knows_album_covers()`. It speaks one interface version.
If the library serves only newer ones, the app plays what is in its cache, its changes wait, and expert mode and the
settings say "This app is too old for the library – install the new one"; if it serves only older ones, "The library
is older than this app". Simple mode says nothing.

**The journal** has the lines of a box, so `statistics.py` counts them unchanged: `start` (the process starts, if
paired), `tag` (`uid`, `kind`, `target`, `output`), `track` (`album`, `track`, `seconds`, `to_end`), `control`
(`album`, `action`, `by`: `button`), `end` (`uid`, `reason`: `tag removed`, `tag replaced`; `album finished`) and
`alive` every ten minutes while something plays, also an entry of Spotify – an app that is merely installed does not
count as switched on. The clock is `synchronized`: a phone knows the time.

- **A lost line becomes `recovery_gap`,** as on a box ([library.md](library.md), section 7): the library takes the
  lines only without a gap. On start, a line that cannot be read is left out and every number without its line – a
  damaged line, a number saved whose line never reached the storage – becomes a mark. So do the lines the library
  waits for and the phone no longer has: after "Unpair" and pairing again, its undelivered lines are gone and its
  numbers go on, and the library keeps the number it had reached for the device.

## 6. The cache, and what the app stores

The app holds no copy of the albums. They are **loaded when they are played and kept as a cache** (`Cache`), with the
mechanics of a box ([library.md](library.md), section 4.8): the manifest, the files that are not played, the track that
is due, then on in the order of the playlist; a file gets its name only after its checksum was verified, a transfer that
broke off is continued, a wrong checksum is asked for three times.

- **A transfer that broke off is continued:** the app asks for the rest of the file (`Range: bytes=<offset>-`) and
  appends the answer if it is a `206`, or a `200` whose `Content-Range` begins at that offset – so an older library
  answers, and every library a range at the end of a complete file, with nothing in the body. Any other answer is the
  whole file and replaces the part. A part that is complete – the app was ended between its check and its new name –
  is checked and named, also without the library.
- **Limit:** "at most … GB" in the settings, 4 GB at first, and 200 MB stay free on the phone. Before a file is loaded
  that does not fit, folders that no album of the catalog names leave first – a folder renamed in the library, an
  album removed while it played –, then the album played longest ago – never the one that plays or is being loaded. An
  album bigger than the limit plays and leaves with the next one.
- An album whose files changed in the library is taken over when its tag is placed, not before and not while it
  plays, on the thread that loads it. An album removed in the library leaves the cache with the next pass, one that
  plays when it plays no more.
- **Positions are no part of the cache:** an album that left continues where it stopped when it comes back.
- Away from home the cache is all there is; the settings show what is in it.
- **A damaged file never stops the app,** as on a box ([library.md](library.md), section 4.21): a line of the outbox
  or the journal that cannot be read is left out, a file of the copy or a manifest that cannot be read is fetched
  again. A storage that is full ends no playback: what cannot be written is logged and written with the next change.

In the private folder of the app, in the same form as a box ([library.md](library.md), section 4.2), not in a backup of
the phone (`allowBackup` off – the key belongs to this device):

```text
identity/   box-id, library (address), key, library.crt; pairing.json while a pairing is written
sync/       copy/tags.json, targets.json, images.json, box.json; outbox; journal; numbers.json; manifests/;
            played.json; fit
albums/     the cache
images/     the covers of albums and stations
```

## 7. What the library does for it

| Where | |
| --- | --- |
| `POST /api/library/pairings` (admin) | a one-time password, valid 10 minutes; the answer is the content of the QR code and the QR code as SVG (`segno`) |
| The handshake (`Library._handshake`) | the bootstrap password registers a box, the one-time password an app |
| `PATCH /api/library/boxes/{box}` (admin) | also `volume`: `max-volume` of speaker and headphones of a phone, settings `volume.speaker`, `volume.headphones` |
| `GET /api/library/qr?text=` (admin) | a QR code of an address of the library: the link to the app |
| `GET /app.apk` (no login) | the app of the version that "Add a box" installs; `404` without one |
| `GET /api/library/overview` | `kind` of every box; `app`: the version `/app.apk` hands out |
| `GET /api/sync/interface` | also `app`, for the settings of the app |
| Page, tab "Boxes" | "Add a phone": the link to the app and its QR code, "Pair" with the QR code of the pairing; a phone in the table as a phone, with its volume limits and the dialog that sets them |
| `PUT /api/library/spotify-client` (admin) | the client ID of the app at Spotify; every phone gets it as the setting `spotify-client`, a phone paired later with its pairing; empty: none |
| Page, tab "Spotify" | "The app on phones": what to register at Spotify, and the client ID |
| Tab "Versions" | names boxes only |

## 8. Delivery

No app store. The app is a file of the release, `tinyjukebox-app-<version>.apk`, next to the slot, the package and
the installer; it is in neither the Docker image nor PyPI.

- **One version for everything:** the version of `pyproject.toml`; `versionCode` follows from it (0.40.1 → 40001), so
  that Android takes every release as an update. `app/` counts as software (`release_needed.sh`).
- **Signed with one key,** the secret `APP_SIGNING_KEY` of the environment `release` (the keystore in Base64) with
  `APP_SIGNING_PASSWORD` ([library.md](library.md), section 8.2). Android refuses an update that is signed with another
  one.
- **The library fetches the APK with the slot and the installer** of a release, the package brings the one of its
  version, and `/app.apk` hands it out without a login, like the installer. The tab "Boxes" shows the link, also as a
  QR code: the camera of the phone opens it, the browser warns once about the certificate, Android asks once whether
  the browser may install apps. A phone in the home network needs no access to GitHub.
- Installing and updating is by hand. The settings show the version of the app and the one the library hands out.

## 9. The code

One Gradle project in `app/` (Kotlin, Android 8 / API 26 or newer; development.md, section "The app"):

| Module | |
| --- | --- |
| `sync` | plain Kotlin without Android, the counterpart of `tinyjukebox.sync`: `Identity` (pairing, the key), `Client` (exactly one certificate), `AppData` (copy, outbox, journal), `Sync` (one pass, the events), `Cache` and `Images`; a command-line program. Tested on the PC |
| `app` | Android: `MainActivity` (pairing and simple mode), `Jukebox`, `TagReader`, `PlaybackService`, `ExpertActivity`, `SettingsActivity` |
| `probe` | the probe of section 10, not released |

Libraries are fetched by Gradle and named in `app/gradle/libs.versions.toml`: Media3, ZXing, AndroidX, kotlinx
serialization, Gson – all Apache-2.0. Spotify's App Remote SDK (Apache-2.0) is no package of Maven Central: the build
file fetches it from the release of Spotify's repository on GitHub and checks its SHA-256. `README.md` lists them, the
settings of the app show the licence text. Texts in German and English from the language of the phone; the page inside
keeps its own choice. The icon is the lid of the logo.

## 10. Open

What is still to do:

- **Measure with the probe** (`app/probe/`, installed with
  `adb install app/probe/build/outputs/apk/debug/probe-debug.apk`; the log is shared as text with "Share log", the
  mode button switches between `ignore()` and asking every 250 ms):

  | Question | How it is measured |
  | --- | --- |
  | How do phone and tag lie best? | the tag on the back of the phone, the phone on the tag, for the kinds of tags there are (a disc, a card, a little figure), with and without a case; the spot of the reader |
  | How reliably is a tag found? | time from placing to the UID, failed reads, for tags of both makers (`04…`, `1D…`) |
  | Is the UID the one of the box? | compared with the UIDs of the catalog |
  | Is the tag kept? | minutes without a false "gone", with the phone lying still and being moved |
  | How late is a tag that is taken off reported? | `NfcAdapter.ignore()` against asking every 250 ms, both timed |
  | What happens when the screen goes off and on? | logged: gone, found again, how fast |
  | What does the dimmed, lit screen cost? | battery per hour with a tag on the phone |

- **Decide presence or touch** with the result, and write the times into section 4. If presence proves unreliable,
  simple mode works by touch – a tag touched plays, touched again pauses or continues, nothing ends by taking it off –
  and the desktop box gets the same answer for its D1 ([desktop-box-concept.md](concept/desktop-box-concept.md), section
  3). Afterwards the probe is removed.
- **Try Spotify on the phone:** the registration on Spotify's developer page with an account of the family
  subscription (whether a member of a family subscription counts as Premium is written nowhere), the client ID in
  the tab "Spotify", then a tag of Spotify on a phone with the Spotify app.
- **The key of the app:** the secrets `APP_SIGNING_KEY` and `APP_SIGNING_PASSWORD` ([library.md](library.md),
  section 8.2). 0.42.0 was released without them: its APK is signed with the debug key of the run, and a phone with it
  takes no signed release as an update – it is uninstalled and paired again once.
- **Try everything else on a real phone:** the list in [roadmap.md](roadmap.md), section 1.
- **Updates by hand** (section 8) were decided "probably"; to be looked at again once the app is in use.

Tests: `tests/sync/test_app.py` (pairing, the kind, what a phone may do, the volume limits, `/app.apk`),
`tests/sync/test_install.py` (the app of a release and of the package), `tests/unit/test_handshake.py` and
`HandshakeTest` (the same test vectors, `app/sync/src/test/resources/handshake.json`), the JVM tests of `sync`
(`LibraryTest` starts `tinyjukebox-library --data DIR`: pairing, a pass, an album on demand, a transfer continued
and a complete part, positions, the journal, an unknown tag, the volume limits, a removed phone, paired again with
the lost lines as `recovery_gap`; `AppDataTest` damaged lines and lost numbers, `CacheTest` the limit, the free space,
strays and a takeover, `ClientTest` both answers to a range, `SyncTest` a pass that fails, `IdentityTest` a pairing
ended half-way), `scripts/pages/check.sh` ("Add a phone", a phone in the table and its limits). Tried in the
emulator: pairing, a tile that loads and plays an album, positions and journal in the library, expert login,
settings, logout.
