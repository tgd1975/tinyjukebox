# Concept: an app for the library – "simple" and "expert"

Status: concept, nothing implemented. Reviewed against the code on 2026-10-12 (version 0.28.0); the plan is section 9.
Related today: the phone as reader ([library.md](../library.md), section 5.1), the interface of the boxes (section
4.13), keys of a box (section 4.14), albums on demand and space on the card (sections 4.8 and 4.16), the web page
([rest-api.md](../rest-api.md), section 5).

## 1. Why an app, when the browser is enough today

Section 5.1 of library.md left out an app: "the browser is enough". That holds for managing the library. It does not
hold for the second use: **a child puts a tag on a phone or tablet and the album plays**, like on the box. The
browser cannot do that:

| Need | Browser | App |
| --- | --- | --- |
| Read a tag | Web NFC: Chrome on Android only, HTTPS, a permission question | NFC of Android, no question, reads while the app is on screen |
| Notice that the tag is taken away | no | yes (section 2.3) |
| Play with the screen off, headset buttons, lock screen | limited and unreliable | media session of Android |
| Play without the library | no | albums are kept on the phone, like on a box |
| The self-signed certificate | a warning that is accepted by hand | the app accepts exactly the certificate of the library (section 3.2) |

If none of these matters, the page stays as it is. The app adds a way to listen; it replaces nothing.

## 2. Two modes

| | Simple | Expert |
| --- | --- | --- |
| For | the child; the phone is a box | the adult managing the library |
| What it is | a small native player | the page of the library, in the app |
| Login | none: the device is paired (section 3.2) | the login of the page (user or admin) |
| Offers | a tag on the phone → the album plays; big buttons; the tags to tap | everything the page offers |

- **The app is paired first, always** – also on the phone of an adult who wants expert mode only. Pairing gives the
  app the address and the certificate of the library; without them it shows nothing.
- **Simple mode is the mode after pairing.** The button "Expert" asks for the login of the page; the way back is
  free. A child reaches neither the page nor the settings of the app this way.
- This guards the app, not the phone: the home button leaves every app. A phone that is to stay in the app is
  pinned with the screen pinning of Android (settings of the phone); the app does nothing for it.

### 2.1 Expert: the page in the app

Expert mode is a `WebView` that shows the page of the library. Nothing of the page is built a second time: a new
tab, a new text or a new right on the page is in the app with the next load, and the roles work as they do.

What the app adds around the page, because a `WebView` does not bring it:

| Part | Why |
| --- | --- |
| Certificate | the `WebView` asks the app about the self-signed certificate; the app accepts the one of the library and no other |
| "Scan tag" (tab "Tag management") | the app offers the page an object `TinyJukeboxApp` with the NFC of the phone. `app.js` uses it where it is there and `NDEFReader` otherwise; `scanned()` gets the same UID. The page in a browser is unchanged |
| Choosing files | uploads of albums and covers open the file chooser of Android. Choosing a whole folder (`webkitdirectory`) does not exist in a `WebView`: the button "Choose folder" is hidden in the app, files are chosen |
| Downloads | the backups are fetched by the app itself (its own request with the session and the certificate) and saved to "Downloads" |
| Questions and the back button | `confirm()` dialogs; "back" closes a dialog of the page or leaves expert mode |

The session of the page lives in the cookie of the `WebView` and ends as on every device (`session-timeout`, logout).

### 2.2 Simple: the phone as a box

The screen shows what a box shows: the cover of the playing album, its title, track and position, play/pause, next,
previous. Below, the **tags as tiles** with the cover of their album or station, like the tab "Tags" of the page, to
tap – for a device without NFC or a child who prefers to tap. A band of colour follows the LED of the box, so the
child sees what the tag did.

| Event | A box | The app |
| --- | --- | --- |
| A tag is put on | plays its album from the saved position, or its station | the same |
| Another tag | ends the first, plays the other | the same |
| The tag is taken away | ends the playback and saves the position | the same, as far as the phone sees it (section 2.3) |
| The same tag again | continues at the saved position | the same |
| A tag that is not linked | LED yellow | band yellow, a sentence on screen, no sound |
| An unknown tag | asks the library, then creates it, not linked | the same: the tag appears on the page of the library |
| An album whose files are not there | fetches it, LED flows green, aqua, blue | the same (section 4.2) |
| The next track has not arrived | waits at the end of the track | the same |
| No library reachable | plays what is on the card; changes wait | plays what is in the cache; changes wait |
| A tile tapped on screen | – (the virtual reader of the page) | as if it were put on; a second tap, or a tap on another one, takes it away |
| Buttons | play/pause, next, previous, previous held = from the start | the same, on screen and on a headset |

- **Tags are read only:** the app never writes to a tag, only the UID counts.
- **Volume:** the keys of the phone set the volume, as always. The app plays with at most `max-volume` of the
  settings of this device (speaker or headphones, [architecture.md](../architecture.md), section 3) of what the
  phone is set to – so the limit holds whatever the keys say. `default-volume` has no meaning on a phone. The
  limits are set on the page of the library, at the box of this phone.
- **Stations** come from the internet, not from the library: a station plays wherever the phone has network.
- **Spotify entries**, once they exist ([spotify.md](spotify.md), section 10): played if the Spotify app is
  installed on the phone; without it the tile is shown and says that this tag plays on a box.
- No part of the app reacts to a change somebody else made, except the tag that is on the phone: if it is linked
  on the page meanwhile, it plays (`recheck_tag()` of a box).

### 2.3 Presence, and what the phone cannot see

The browser reports a touch only. The app can tell whether the tag is still there: Android keeps the connection
to a tag in range and reports when it is gone (`NfcAdapter.enableReaderMode`, then `NfcAdapter.ignore()` with a
listener for the removal; if a phone does not report it reliably, the app asks the tag every 250 ms and takes a
failed answer for "gone"). That is `TAG_ON` / `TAG_OFF` of `rfid-reader`.

**Android reads tags only while the screen is on, the phone is unlocked and the app is visible.** A screen that
switches off would look like a tag that was taken away. Therefore:

- **Only a removal the app sees counts.** If the screen goes off or the app leaves the screen, the playback goes on,
  as with every player on a phone. When the app is visible again it looks for the tag for 2 seconds: there –
  nothing changes; not there – the playback ends, as if the tag had been taken away just then.
- **While simple mode is visible it keeps the screen on,** dark and dimmed after 30 seconds without a touch, so that
  a tag can be put on and taken away at any time without waking the phone. The power button still switches the
  screen off; the rule above applies then.
- A tag that is lifted for less than 1 second (the phone was moved) is not a removal.
- One tag at a time. The reader of the phone stays on while a tag lies there: some battery.

How well this works in the hand of a child is not known and decides the handling. It is measured before anything
else is built (section 9, step 0): how the phone and the tag lie best, how reliably a tag is found and kept,
how late a removal is reported, what the dimmed screen costs.

**If presence proves unreliable** on the phones at hand, simple mode works by touch: a tag touched plays, the
same tag touched again pauses or continues, nothing ends by taking it away. The rest of this concept is the same
for both; only this section and the journal line "end" differ.

## 3. The app is a box of its own kind

The simple mode needs exactly what a box needs: catalog, albums, positions, a journal. The interface exists
(library.md, section 4.13), is authenticated by a key of one device (section 4.14) and gives a device its own
positions. So the app **registers as a box** and uses `/api/sync/…`; the library needs no second interface for it.

What comes with it: positions per device ("Anna's phone" continues where Anna's phone stopped), the journal and so a
row of its own in the statistics, a name and the volume limits on the page of the library, removing the device makes
its key invalid, and a phone that is set up again can take over the positions of the old one (tab "Boxes").

### 3.1 What the app does of a box

| Part of a box | App |
| --- | --- |
| Handshake, key, pinned certificate (4.14) | yes, with a one-time password (section 3.2) |
| "Which interface versions do you serve" (4.10, 4.15) | yes (section 3.4) |
| Copy of the catalog: `tags.json`, `targets.json`, `cards.json`, whole files compared by checksum (4.3) | yes |
| Positions and settings of this device: `box.json` (4.2) | yes |
| Outbox with running numbers and revisions (4.4) | yes, for three changes: a position, an unknown tag (created "by the software", not linked), nothing else |
| Answers `applied`, `conflict`, `rejected` (4.5) | yes; a conflict of a position means the library changed it on purpose and its value stays |
| The library lost positions, e.g. restored from a backup (4.12) | yes: sent again, as a box does |
| Provisional ids (4.6), uploading albums and images (4.8) | no: the app creates no album and no station |
| Albums on demand with manifest and checksums (4.8) | yes: the cache (section 4.2) |
| The images of the images folder, all of them (4.8) | yes: the covers of the stations, and those of the albums once they have moved there (section 9) |
| Journal with running numbers (7) | yes (section 3.5) |
| Status report | yes: version, interface, name of the device; no slots |
| Events of the library (4.9) | only while the app is visible; otherwise it runs a pass on start, when a tag is put on, after a change, and every minute while it plays |
| Slots, health check, self-update, defective versions (6) | no |
| Broken files reported from the decoder (4.18) | no: another decoder than the one of the boxes |

### 3.2 Pairing

A box proves the bootstrap password, which Ansible wrote to it. A phone gets a password for one use instead:

1. An admin opens the page of the library, tab "Boxes", button "Pair a phone". The page shows a **QR code** for
   10 minutes: the address of the library and a one-time password (a long random string, as the bootstrap password
   is).
2. The app scans it and runs the handshake of section 4.14 with the one-time password in place of the bootstrap
   password. The handshake proves the certificate of the library to the app, as it does to a box; the app stores it
   and accepts only this one from then on, in both modes.
3. The app has a key of its own now. The one-time password is used up; the bootstrap password is never on a phone.

- **The QR code is the only way.** A short number to type would not be safe: the proof of the handshake is computed
  from the password, and a password that can be guessed is guessed from an overheard proof. The address alone can be
  typed nowhere either – pairing gives both.
- The page draws the QR code on the library (an SVG); nothing of it is kept, and a new one makes the old one
  invalid.
- **The library decides what was paired, not the device:** a handshake with the bootstrap password registers a box,
  one with a one-time password an app (`kind`: `box` or `app`).
- The app generates its id on its first start, as a box does. Deleting the data of the app or installing it anew
  makes a new device; an admin lets it take over the old one on the page.
- "Unpair" in the settings of the app (behind the login of expert mode) deletes key, certificate, copy, positions
  and cache. The app is empty then and asks for a QR code; the library keeps the device until it is removed there.

### 3.3 What a paired phone may do

The key of a box may change the catalog and send albums and images. A phone of a child needs none of that, and
it is lost more easily than a box. For a device of the kind `app` the library therefore

- applies only changes of a position, and a tag that the software created as not linked; every other change is
  `rejected`,
- refuses files of albums and images (`PUT …/albums/…`, `PUT …/cards/…`),
- offers no versions of the box software.

Reading stays as for a box: the catalog, the albums, the images, its own positions, settings and statistics.

### 3.4 Versions, and app and library that do not fit together

- The app is released with the project and has its version (section 7). Towards the library it names itself
  `app-<version>` – in `X-Box-Version`, in every change and in every journal line – so that the statistics "per
  version" and the defective versions of the boxes are never mixed with it.
- The app speaks one version of the interface (`INTERFACE`), as a box does. A box whose library serves a newer one
  updates itself; the app cannot:

| Situation | The app |
| --- | --- |
| the library serves the version of the app | works |
| the library serves only newer versions | plays what is in the cache, changes wait; simple mode says nothing, expert mode and the settings say "This app is too old for the library – install the new one", with the link of section 7 |
| the library serves only older versions | the same, with "The library is older than this app" |

What never changes in the interface (section 4.10) is what lets the app find this out. Expert mode is not affected
by the interface version: page and server always come from the same version.

### 3.5 The journal of the app

The same lines as a box writes, so `library/statistics.py` counts them unchanged:

| Line | When | Fields beyond those of every line |
| --- | --- | --- |
| `start` | the app process starts | – |
| `tag` | a tag is put on or tapped | `uid`, `kind`, `target`, `output` (`speaker` or `headphones`) |
| `track` | a track ends or is left | `album`, `track`, `seconds`, `to_end` |
| `end` | the playback ends | `uid`, `reason`: `tag removed`, `tag replaced`, `album finished` |
| `alive` | every ten minutes while something plays | – |

Every line has the running number, the number of the start, the seconds since that start, the time of day with
`synchronized: true` (a phone knows the time) and the version. `alive` is written only while something plays: an
app that is merely installed does not count as "switched on".

## 4. The app

### 4.1 Parts

- **Kotlin, Android 8 (API 26) or newer**, one Gradle project in `app/` of this repository with two modules:
  - `sync`: plain Kotlin without Android – handshake, client with the pinned certificate, copy, outbox, journal,
    manifests and the cache. It is the counterpart of `tinyjukebox.sync` and tested on the PC.
  - `app`: Android – NFC, the player (Media3/ExoPlayer in a foreground service with a media session), the two
    screens, pairing with the camera, the `WebView`.
- Libraries are fetched by Gradle and named in the build file; nothing foreign is copied into the repository.
- Texts German and English from the language of the phone (`strings.xml`); the page inside keeps its own choice.
- The state of simple mode is one small state machine with the states of `STMOperation` that apply
  (`WAIT_FOR_TAG`, `CHECK_TAG`, `UNMAPPED_TAG`, `FETCH_ALBUM`, `PLAY_ALBUM`, `BROKEN_ALBUM`) and the same colours.

### 4.2 The cache of albums

The app holds no copy of the library. Albums are **loaded when they are played and kept as a cache**. This is what a
box does already – albums on demand (library.md, section 4.8) and making room (section 4.16) – with one difference:
a box keeps an amount of space free, the app keeps the cache below a size.

- An album is loaded when its tag is put on or tapped for the first time: the manifest, then the files that are
  not played (playlist, cover), then the track that is due, then on in the order of the playlist. "Next" on a track
  that is not there pulls it forward.
- A file gets its name only after its checksum was verified; a transfer that broke off is continued. A file with a
  wrong checksum is requested again, three times.
- What is loaded stays: the second time the album plays at once and without the library.
- **Limit:** set in the settings of the app ("at most … GB", 4 GB at first). Before a file is loaded that does not
  fit, the album that was played longest ago leaves – never the one that plays. An album bigger than the limit is
  played and leaves with the next one.
- An album whose files changed in the library is taken over when it is put on the next time, not before and not
  while it plays: the files that differ are loaded again.
- An album that was removed in the library leaves the cache with the next pass.
- **Positions are no part of the cache.** They are kept and sent whether the album is in the cache or not; an album
  that left continues where it stopped when it comes back.
- The library is reached in the home network only (section 4.14). Away from home the cache is all there is; the
  settings show what is in it.

### 4.3 What the app stores

In the private folder of the app, in the same form as a box (library.md, section 4.2), so that both sides of the
interface are tested with the same files:

```text
identity/   box-id, library (address), key, library.crt
sync/       copy/tags.json, targets.json, cards.json, box.json; outbox; journal; numbers.json; manifests/; played.json
albums/     the cache
cards/      the images folder: covers
```

Nothing of it is in a backup of the phone (`allowBackup` off): the key belongs to this device.

## 5. Changes to the library and the page

| Where | Change |
| --- | --- |
| `library/service.py`, handshake | accepts an open one-time password beside the bootstrap password; the one that fits decides the `kind` |
| `library/store.py` | a box has `kind`; boxes that exist are `box`. Section 3.3 for `app` |
| `POST /api/library/pairings` (admin) | creates a one-time password, valid 10 minutes; answers the content of the QR code and the QR code as SVG |
| `GET /api/library/overview` | `kind` with every box |
| Page, tab "Boxes" | button "Pair a phone" with the QR code; a phone is shown as a phone, without slots and update mode; the link to the app (section 7) |
| Page, tab "Versions" | the column of the boxes names boxes only |
| `app.js`, "Scan tag" | uses `TinyJukeboxApp` where it is there |
| Statistics | nothing: an app is a box with a name; its versions are rows `app-…` |
| A box | nothing |

The interface version stays 1: everything is added, nothing a box of today cannot cope with.

## 6. Left out

| Left out | Reason |
| --- | --- |
| iPhone | a build needs a paid developer account and a second code base |
| Controlling a box from the phone | the app is a box of its own (section 10) |
| Creating, naming, linking in simple mode | that is expert mode |
| A second implementation of the page | expert mode is the page |
| The pages of the boxes in the app | the app connects to the library; a box has its own certificate and login |
| Expert mode without pairing | pairing is what gives address and certificate; one way in is enough |
| A library reached from outside the home network | not planned for the boxes either (section 4.14) |
| Uploading an album from the phone into the cache | albums come from the library; uploads go through expert mode |
| Reporting broken files | another decoder (section 3.1) |

## 7. Delivery

No app store. The app is built by the pipeline of the project and is a file of the release, next to the package for
the NAS and the slot for the boxes:

- **One version for everything.** The app has the version of `pyproject.toml` and its changes are in `CHANGELOG.md`.
  `app/` counts as software (`scripts/ci/release_needed.sh`): a change of the app needs a bump and becomes a release
  with a push to `master`, as every change does.
- The boxes fetch such a release too, although nothing changed for them. A box installs it on its next start; that
  costs a download and nothing else. While the app is being developed its pushes go to a branch: the build of every
  push has the APK as an artifact, and a phone on the PC gets it with `adb install`.
- **Signed with one key,** a secret of the repository like the key of the releases; Android refuses an update that
  is signed with another one.
- **The library fetches the APK with the slot** and offers it on its page ("Boxes", next to "Pair a phone") – a
  phone in the home network needs no access to GitHub. Installing and updating is opening that link on the phone and
  confirming, by hand (decided, "probably": section 10).
- The app shows its version and the one the library offers in its settings; it does not fetch anything by itself.

## 8. Tests

| What | How |
| --- | --- |
| Handshake and proofs | the same test vectors for Python and Kotlin, in a file both read |
| `sync` module: copy, outbox, journal, manifests, cache and its limit | unit tests on the JVM, with the files of section 4.3 |
| App against a real library | the JVM tests start `tinyjukebox-library` on the PC and run pairing, a pass, an album on demand, a position that comes back |
| Library: one-time password, `kind`, section 3.3, the overview | `tests/sync/`, with a stand-in that speaks like the app |
| Page: "Pair a phone", a phone in the table | `scripts/pages/check.sh` |
| NFC, presence, the screen | the probe (step 0) and by hand on phones with real tags; the emulator has no NFC |
| Simple mode without NFC | in the emulator, with the tiles to tap |
| Expert mode | in the emulator: login, a tab of each kind, an upload, a backup |

## 9. Implementation plan

Each step ends with something that can be tried, and with a commit. A step that changes what a user sees also
brings: version bump, `CHANGELOG.md`, library.md, rest-api.md, the user manual (de, en), page texts.

### Step 0 – The probe: how does a tag behave on a phone

A throw-away app (`app/probe/`, not released, removed after step 3) with one screen and a log. No library, no
sound. It answers what section 2.3 leaves open:

| Question | How it is measured |
| --- | --- |
| How do phone and tag lie best? | the tag on the back of the phone, the phone on the tag, for the kinds of tags there are (a disc, a card, a little figure), with and without a case; the spot of the reader |
| How reliably is a tag found? | time from putting on to the UID, failed reads, for tags of both makers (`04…`, `1D…`) |
| Is the UID the one of the box? | compared with the UIDs of the catalog |
| Is the tag kept? | minutes without a false "gone", with the phone lying still and being moved |
| How late is a removal reported? | `NfcAdapter.ignore()` against asking every 250 ms, both timed |
| What happens when the screen goes off and on? | logged: gone, found again, how fast |
| What does the dimmed, lit screen cost? | battery per hour with a tag on the phone |

The log can be shared from the phone as a file. **The result decides** between presence and touch (section 2.3)
and sets the times of that section; it is written into this concept before step 3.

### Step 1 – Library: pairing and the kind `app`

- `kind` in the store, the one-time password in the handshake, section 3.3, `POST /api/library/pairings`, the
  overview.
- Page: "Pair a phone" with the QR code, phones in the table of the boxes.
- Tests in `tests/sync/` with a stand-in for the app; the page check.
- Done when: a script on the PC pairs with a one-time password, is listed as a phone and is refused what section 3.3
  refuses; a box is paired as before.

### Step 2 – The `sync` module

- Gradle project, module `sync`: handshake, client, copy, outbox, journal, manifests, cache with its limit.
- The shared test vectors; JVM tests against a library on the PC.
- The pipeline builds and tests it on every push.
- Done when: a command-line program of the module pairs with a QR content, fetches the catalog, loads an album
  into a folder, moves a position and delivers a journal line – all seen on the page of the library.

### Step 3 – Simple mode plays

- Module `app`: pairing with the camera, the screen of simple mode, the state machine, the player service with the
  media session, the tiles to tap, the band of colour.
- NFC with the rules the probe gave (section 2.3).
- Journal and positions; the volume limit; the settings (name of the device as the library has it, cache with its
  limit and content, version, "Unpair" – behind a login).
- Done when: on a phone a tag plays its album, continues after being taken away and put on, an album that is
  not in the cache is loaded, the library shows position and statistics, and all of it works again with the
  library switched off. The acceptance list for it goes into [roadmap.md](../roadmap.md).

### Step 4 – Expert mode

- The `WebView` with certificate, file chooser, downloads, dialogs, back button; the login as the door out of
  simple mode.
- `TinyJukeboxApp` and the change in `app.js`.
- Done when: every tab of the page works in the app as an admin and as a user, an album is uploaded, a backup is
  saved, a tag is scanned.

### Step 5 – Delivery

- The signing key; the APK in the build of every push and in the release; `app/` in `release_needed.sh`.
- The library fetches the APK with the slot and offers it; the settings of the app show both versions.
- App and library that do not fit (section 3.4).
- Done when: a release holds the APK, the page of the library on the NAS offers it, and a phone installs it over
  the older one.

### Step 6 – The documents

- This concept goes into library.md (a section "The app" beside "The phone as reader"), rest-api.md and
  development.md (building the app), and is deleted here.
- User manual (de, en): pairing a phone, simple mode, expert mode; the quick guide gets the tag on the phone.
- roadmap.md: what was not seen on a real phone.

Steps 1 and 2 do not depend on the probe and can run beside it. The covers of the albums need no step: they come
with the files of the album today and with the images of the images folder once they have moved there
([backup-restore.md](backup-restore.md), section 1); the app shows whichever the catalog names.

## 10. Decided

2026-10-11:

1. Android only.
2. The app is no remote control: it is a stand-alone variant of the box and plays itself.
3. Pairing by QR code from the page of the library.
4. Updates of the app by hand – probably; looked at again once the app exists.
5. Presence of a tag is polled, where the phone can.
6. The albums are loaded as a cache.

2026-10-12:

7. One version for boxes, library and app (section 7).
8. Simple mode keeps the screen on while it is visible, dimmed and dark (section 2.3).
