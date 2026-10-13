# REST API and web page

The HTTPS server of Tiny Jukebox (`src/tinyjukebox/web/`): authentication, endpoints, events and the web page built on
them. Every endpoint is a thin wrapper around `BoxControl` ([architecture.md](architecture.md), section 8); the
console offers the same operations. Config: [configuration.md](configuration.md), section 5.

## 1. Server

`web/server.py`: `http.server` with TLS, one thread per connection (`ThreadingMixIn`).

- At most `MAX_CONNECTIONS` (32) at the same time; further connections are closed at once.
- Routes are patterns with placeholders (`/api/tags/{uid}`) and a minimum role. Static files come from a fixed list.
- Every response carries security headers (CSP, HSTS, `X-Frame-Options`, `nosniff`, `no-referrer`).
- Nagle is switched off; otherwise responses would wait for the delayed ACK (~40 ms).
- HTTPS only (TLS ≥ 1.2). If certificate or key is missing, a self-signed certificate is created with `openssl`
  (`web/certificates.py`): EC key (prime256v1), the key file is created beforehand with mode 600. The fingerprint is
  logged on start (`rest api listening on … fingerprint`).

## 2. Authentication and roles

| Role | May |
| --- | --- |
| `user` | read status, tags, albums, stations and progress; operate the box: player, volume, buttons, virtual reader; in the library add a tag by scanning it with a phone; change the own password |
| `admin` | additionally change data: tags, albums, stations, uploads, users, volume limits, pairing, log |

- **Login** returns a session token. Scripts send it as `Authorization: Bearer <token>`. The web page gets it as a
  cookie (`HttpOnly; Secure; SameSite=Strict`); changing requests with a cookie additionally need the header
  `X-TinyJukebox-Request` (CSRF).
- **Sessions:** 256-bit random token; only its SHA-256 is stored. Expiry after `session-timeout` without use.
- **Logout of a page that is left alone:** a session also ends `idle-timeout` seconds after the user did something
  last (default 30 minutes, `0`: never). An open page asks by itself all the time, so a request alone proves nothing:
  the page sends the header `X-TinyJukebox-Idle` with every request, the seconds since the last pointer movement, tap,
  key or wheel in any tab of the browser (0 while it uploads files). A request with the cookie and without that
  header – an image the browser loads, the event stream – says nothing about the user. A request with the token in
  the header comes from a program and counts as an action itself. The page knows the limit from `idle_timeout` of
  `GET /api/session` and `POST /api/login`; when it is reached it logs out by itself and says so on the login page.
- **Passwords:** scrypt (n = 2¹⁴, r = 8, p = 1, 16 byte salt), constant-time comparison. For an unknown user a dummy
  hash is checked (no user enumeration through the response time). Verifications run one at a time (each needs
  16 MB). Minimum length: 8 characters.
- **Users file:** mode 600, written atomically, reloaded when it changes. Role changes and removals take effect at
  once, also on existing sessions.
  - `/api/users` (admin): a new password ends the sessions of that user, except the one that set it. Nobody can
    remove their own user – so an admin always remains. The role of a user is changed only with `tinyjukebox-users`.
  - `/api/session/password` (every user): needs the current password – an open session alone is not enough. The
    other sessions of the user end.
- **Login throttle:** after 5 failed attempts per address, every further failed attempt blocks the address for 30 s;
  reset after 15 min or a successful login. A wrong current password when changing the own password counts as a
  failed attempt. With more than 10 000 addresses, entries that are not blocked are forgotten first – flooding with
  many addresses lifts no block.

## 3. Endpoints

**Errors.** A refused request is answered with its status and `{"error": "<English text>"}`. An error that a user
of the web page runs into also has a `code`, and the values its text needs:

```json
{ "error": "tag '04:AA:BB' exists already.", "code": "tag.exists", "uid": "04:AA:BB" }
```

The page shows its own text for a code, in its language (`error.<code>` in `web/web/texts`), and `error` for an
answer without one. Codes are given where the error is raised (`code=…` of an `ApiError`, `errors.py`); a test
fails if a code has no text or a text asks for a value the server does not send. Without a code, and English on
every page: requests the page never sends, the interface of the boxes, the log, the output of the version commands,
the problems of the files of an album, and the reasons of a failed backup or stream.

| Method and path | Role | Effect (console command) |
| --- | --- | --- |
| `POST /api/login` `{username, password}`, `POST /api/logout` | – | session |
| `GET /api/session` | user | logged-in user, role, `mode` (`box` or `library` – the page of the library is the same page) and `idle_timeout`, the seconds without an action of the user after which the session ends (0: never) |
| `PUT /api/session/password` `{current_password, password}` | user | change the own password (wrong current password: 403) |
| `GET /api/users`, `POST /api/users` `{username, password, role?}`, `PUT /api/users/{name}/password` `{password}`, `DELETE /api/users/{name}` | admin | users of the web page: list, add (role `user` or `admin`), set password, remove (not the own one) |
| `GET /api/status` | user | state, tag, album with tracks and position (`playback.state`: e.g. `play`, `paused`) or station (`radio`: name, URL, `cover`, `state`, `error`), volume (as `GET /api/volume`), headset (`bluetooth`, as `GET /api/bluetooth`), LED (`system_state`, `album_info`) |
| `GET /api/events` | user | server-sent events, section 4 |
| `GET /api/tags[?unmapped=1]`, `GET /api/tags/{uid}` | user | `list_tags`, `unmapped_tags` |
| `POST /api/tags` `{uid?, name?, album_id? \| radio_id?}` | admin | create a tag without placing it (without UID: random) |
| `PATCH /api/tags/{uid}` `{name}` | admin | `set_tag_name` |
| `PUT /api/tags/{uid\|current}/album` `{album_id}` | admin | `link_tag_album`, `link_current_tag` |
| `PUT /api/tags/{uid\|current}/radio` `{radio_id}` | admin | `link_tag_radio`, `link_current_tag_radio` |
| `DELETE /api/tags/{uid}/album`, `DELETE /api/tags/{uid}/radio` | admin | `unlink_tag` (album or station) |
| `DELETE /api/tags/{uid}` | admin | `remove_tag` |
| `POST /api/rfid` `{uid}`, `DELETE /api/rfid` | user | virtual RFID reader: place / remove a tag |
| `GET /api/albums[?unmapped=1]`, `GET /api/albums/{id}`, `GET /api/albums/{id}/cover` | user | albums (each with `track_count` and `length`, the playing time in seconds or `null`; with a library also of an album that is not on the box), details with ID3 data and problems, cover. A library adds `broken`: in the list the number of broken files of the album, in the details `[{file, library, boxes: [{box, name, when, error}]}]` – `library` is what the library found itself (`null` if nothing), `boxes` the boxes whose decoder found errors in the file ([library.md](library.md), section 4.18) |
| `POST /api/albums` `{folder, title}` | admin | `add_album`; the title is required |
| `PATCH /api/albums/{id}` `{title}` | admin | `set_album_title` |
| `PUT /api/albums/{id}/cover` (body: JPEG or PNG, at most 10 MB) | admin | `set_album_cover`: the image becomes `cover.jpg` or `cover.png` in the album folder and replaces a cover stored there |
| `GET /api/albums/new`, `POST /api/albums/new` `{titles?}` | admin | `add_new_albums`: the folders that are no album yet, each with a proposed title (`[{folder, title}]`) / add them all; `titles` maps folders to titles, a folder that is not named gets the proposed title |
| `POST /api/albums/{id}/playlist` | admin | regenerate the playlist from the MP3 files (old one kept as `.bak`) |
| `DELETE /api/albums/{id}[?delete_files=1]` | admin | remove an album (only without linked tags and while it is not playing) |
| `GET /api/radios`, `GET /api/radios/{id}`, `GET /api/radios/{id}/cover` | user | `list_radios` (with linked tags and `cover`, the name of the cover image or `null`), cover |
| `PUT /api/radios/{id}/cover` (body: JPEG, PNG or WebP, at most 10 MB) | admin | `set_radio_cover`: the image is stored in the images folder and replaces the cover the station had |
| `POST /api/radios` `{name, url}`, `PATCH /api/radios/{id}` `{name?, url?}`, `DELETE /api/radios/{id}` | admin | `add_radio`, `set_radio_name`/`set_radio_url`, `remove_radio` (only without linked tags and while it is not playing) |
| `POST /api/radio-test` `{url, name?}`, `DELETE /api/radio-test` | admin | `test_radio`, `stop_radio_test` (state in `GET /api/status`: `state: test_radio`, `radio.test: true`) |
| `GET /api/activities` | user | `list_activities` |
| `POST /api/activities/rebuild` | admin | `rebuild_activity_json` |
| `POST /api/sanity-check` | admin | `sanity_checks` |
| `GET /api/backup[?albums=1]` | admin | `backup`: the data as one archive, with `albums=1` also the albums ([operations.md](operations.md), section 10). The answer is written while it is sent and has no length; an archive without `complete.json` at its end was cut off. Also on the library |
| `POST /api/player/next\|prev\|restart` | user | `track_next`, `track_prev`, `restart_album` |
| `POST /api/player/pause`, `POST /api/player/play` | user | `track_pause`, `track_play` |
| `GET /api/volume`, `PUT /api/volume` `{volume}` | user | `get_volume`, `set_volume`: active volume (`output`: `speaker` or `headphones`) and both profiles (`profiles`) with `default_volume` and `max_volume` |
| `PUT /api/volume/limits/{speaker\|headphones}` `{default_volume, max_volume}` | admin | `set_volume_limits` |
| `GET /api/bluetooth` | user | `bluetooth_info`: `available`, `virtual`, `device` (`address`, `name`), `connected`, `battery`, `volume`, `pairing`, `pairing_result` (`paired`, `not_found`, `failed`), `default_volume`, `max_volume` |
| `POST /api/bluetooth/pair`, `DELETE /api/bluetooth/device` | admin | `bluetooth_pair`, `bluetooth_unpair` |
| `POST /api/bluetooth/virtual` `{connected?, button?}` | user | virtual headset (only without hardware): switch on/off, press a button |
| `POST /api/buttons/{volume-up\|volume-down\|next\|prev\|play\|pause}` `{action: press\|hold\|release}` | user | the same message as the button: `press` → `BUTTON_PRESSED`, `hold` → `BUTTON_HELD`, `release` → `BUTTON_RELEASED` |
| `POST /api/uploads` `{name}` (the title of the album; the folder gets the same name without the characters a folder must not have), `PUT /api/uploads/{id}/files/{file}` (body: MP3), `POST /api/uploads/{id}/finish` `{order?, tag_uid?}` | admin | upload an album |
| `GET /api/uploads[/{id}]`, `DELETE /api/uploads/{id}[/files/{file}]` | admin | open uploads (expire after 1 h) |
| `GET /api/logs?after=&level=&text=&limit=` | admin | latest log entries |

Notes:

- **Buttons** behave as on the box, including the hold gestures: `prev` held restarts the album; `next` and `prev`
  held pair a headset; `volume-up`/`volume-down` held make one small step per `hold`. The route needs only the role
  `user`: whoever can pair at the box may do so here as well.
- **Volume limits** need `admin`, so that nobody raises the hearing protection in passing.
- **Uploads:** names are sanitized (no path separators, no `#`); only valid MP3 (mutagen); size per file limited
  (`max-upload-size`); at most `MAX_FILES` (500) files; 200 MB must stay free after the upload. Files first land in
  a staging folder and are moved only on "finish".
- **Paths:** station covers and upload files are addressed only through their list, never through names from the
  request. `add_album` and `delete_album` resolve symlinks and accept only folders below `albums-folder`.
- **Request bodies:** JSON at most 64 KB, images at most 10 MB.

Only a box has the routes of player, volume, headset, buttons, virtual reader and radio test, and these:

| Method and path | Role | Effect |
| --- | --- | --- |
| `GET /api/box` | user | synchronization with the library: `box`, `name`, `library`, `attached`, `connected`, `alone` (why not, and `alone_code` if the page has a text for it), `fit`, `blocked`, `outbox`, `absent` (albums that are not completely on the card), `unsettled`, `fetching` |
| `PUT /api/box/library` `{url, bootstrap?}` | admin | address of the library and, for the first contact, the bootstrap password; a box that had no library works with it from its next start |
| `DELETE /api/box/library` | admin | unlinks the box from its library: it keeps what is on its card and works for itself ([library.md](library.md), section 4.20). The library is not asked. 409 `box.not_linked` without a library |
| `PUT /api/box/name` `{name}` | admin | name of the box for people |
| `GET /api/box/statistics` | user | play statistics of this box, as `GET /api/library/statistics?box=` of the library answers them, with `origin`: `library` (the library counted) or `box` (a box without a library counts its own journal, the last `limit` lines). `missing` instead of the numbers if the library does not answer: `unreachable`, or `library_too_old` |
| `GET /api/box/system` | admin | `available` (the box has slots), `version`, `library` (its address), and what `tinyjukebox-slot status --json` says: `active`, `slots`, `mode` (`automatic`, `manual`, `off`), `versions`, `problem`, `history`; `origin` (where versions come from: `library`, `releases`, `none`), `releases` and `wait` (their address and waiting time in days), `script` (version of the slot script), `offer` (a release a box without a library has not installed: `version`, `published`, `size`, `notes`, `due`; else null) |
| `GET /api/box/hardware` | admin | what the device drivers say right now, for the card "Hardware" of the tab "System": `available` (the box runs with drivers; `false` with `--no-hardware`), `time` (the clock of the box in seconds since 1970, to which the times below refer) and one entry per driver in `buttons`, `leds` and `readers`. Every entry has `id`, `name`, `type` and `running`. A button adds `button`, `pin`, `pressed` (the level of the pin right now; `null`: it cannot be read), `hold_timeout`, `hold_repeat`, `last_event` (`pressed`, `held`, `released` or `null`), `last_event_at` and `presses` (since the start). An LED adds `pins` (`red`, `green`, `blue`), `colors` and `delays` (the pattern) and `lit` (which lights are on, read from the pins; `null`: they cannot be read). A reader adds `pins`, `reader` (`rfid-reader` runs), `uid`, `error` and `changed_at` |
| `POST /api/box/system/{command}` `{arguments: []}` | admin | `check`, `update`, `revert`, `use`, `fallback`, `promote`, `mode` or `skip` of `tinyjukebox-slot`; answers `{status, output}` |

`status` of a box that works with a library also has `sync`: `connected`, `blocked`, `fetching`.

The library ([library.md](library.md)) has the routes of tags, albums, stations, uploads, users and log, and:

| Method and path | Role | Effect |
| --- | --- | --- |
| `GET /api/library/overview` | user | boxes, versions, albums on their way, state of the query for releases |
| `PUT /api/tags/{uid}` | user | the phone as reader ([library.md](library.md), section 5.1): makes sure that the library knows the tag. An unknown one is added, not linked (`201`), a known one stays as it is (`200`); the answer is the tag. `uid`: 4, 7 or 10 bytes as `rfid-reader` reports them (`04:1A:2B:3C:4D:5E:6F`), anything else is `400`. Nothing is kept about the scan |
| `PUT /api/library/versions/{version}` `{state, why?}` | admin | `use`, `manual` or `dontuse` |
| `PATCH /api/library/boxes/{box}` `{name?, takeover?}` | admin | rename a box; take over positions and settings of another box |
| `DELETE /api/library/boxes/{box}` | admin | the key of the box is invalid from then on |
| `GET /api/library/boxes/{box}`, `PUT /api/library/boxes/{box}/positions/{uid}` `{value}` | admin | positions and settings of a box; set or reset a position |
| `POST /api/library/releases/check` | admin, or the token of a script | look for a release now |
| `PUT /api/library/releases/token` `{token}` | admin | the token with which the library reads the releases. The library asks the git server with it first: a token that the server does not know or that may not read the releases is `400` and not kept. One that fits counts at once and is kept in `settings.json`; the answer is the one of a check. The overview tells only where the token comes from (`releases.token`: `page`, `file`, `missing`, `none`) |
| `PUT /api/library/releases/keep` `{versions}` | admin | how many versions of the box software the archive keeps at most (1 or more); what is too much leaves the archive at once ([library.md](library.md), section 6.13). The overview names the number as `releases.keep` |
| `PUT /api/library/bootstrap` `{password}`, `POST /api/library/tokens` | admin | bootstrap password; a new token for scripts (shown once) |
| `POST /api/library/verify` | admin | compare albums and station covers with the folders |
| `DELETE /api/library/albums/{id}/broken` | admin | drop what boxes reported about the files of the album; the answer is the album |
| `GET /api/library/journal[?kind=&box=]` | admin | journal |
| `GET /api/library/statistics[?box=]` | user | play statistics of all boxes, or of one box ([library.md](library.md), section 7): `tags` (each with `boxes`: listening time per box), `never`, `days` (one entry per day and box, with `box` and `name`), `unknown_time`, `stations`, `skipped`, `versions`, `boxes` (always every box: `id`, `name`, `seconds`, `plays`, `last`) and `box` (the one asked for, or null). 404 for a box the library does not know |
| `GET /api/library/backups` | admin | the backups the library makes by itself ([library.md](library.md), section 4.19): `enabled`, `time` (`HH:MM`), `every` (days), `albums`, `keep`, `folder`, `default_folder`, `running`, `last` (`when`, `ok`, `message`), `next`, and `backups` (`name`, `when`, `size`, `albums`), newest first |
| `PUT /api/library/backups` `{enabled?, time?, every?, albums?, keep?, folder?}` | admin | set schedule and folder; what is left out stays, an empty `folder` is the one of the config. The answer is as for `GET`. `400` for a folder the library cannot write to, `409` for another folder while a backup is being made |
| `POST /api/library/backups` `{albums?}` | admin | make a backup in the folder now, in the background (`202`); `409` while one is being made |
| `GET /api/library/backups/{name}`, `DELETE /api/library/backups/{name}` | admin | fetch a backup of the folder (the rest of it with `Range: bytes=N-`) / remove it |
| `POST /api/library/fill`, `POST /api/library/fill/albums/{id}`, `PUT /api/library/fill/albums/{id}/files/{file}`, `PUT /api/library/fill/images/{name}` | admin | first filling (`tinyjukebox-fill-library`) |

`/api/sync/…` is the interface for the boxes ([library.md](library.md), section 4.13). It does not
answer a login of the page: a box sends `Authorization: Box <id>:<key>`, and a request without a valid key gets 401
whatever its path.

Example with curl:

```bash
TOKEN=$(curl -sk https://tinyjukebox.local/api/login -d '{"username":"NAME","password":"…"}' | jq -r .token)
curl -sk -H "Authorization: Bearer $TOKEN" https://tinyjukebox.local/api/status
curl -skN -H "Authorization: Bearer $TOKEN" https://tinyjukebox.local/api/events     # follow changes (Ctrl-C)
ID=$(curl -sk -H "Authorization: Bearer $TOKEN" https://tinyjukebox.local/api/uploads -d '{"name":"New album"}' | jq -r .id)
for f in *.mp3; do curl -sk -H "Authorization: Bearer $TOKEN" -X PUT --data-binary "@$f" \
  "https://tinyjukebox.local/api/uploads/$ID/files/$(jq -rn --arg f "$f" '$f|@uri')"; done
curl -sk -H "Authorization: Bearer $TOKEN" https://tinyjukebox.local/api/uploads/$ID/finish -d '{"tag_uid":"current"}'
```

## 4. Server-sent events

`GET /api/events`: the page opens an `EventSource`; the session cookie authenticates the stream. The server answers
with `text/event-stream` without length and closes the connection at the end (`Response.stream`). Every stream runs
in the thread of its connection (`MAX_CONNECTIONS` therefore also applies to streams) and checks every
`EVENT_INTERVAL` (0.2 s):

| Event | Content | When |
| --- | --- | --- |
| `status` | as `GET /api/status` | as soon as the status changes, at least every `STATUS_REFRESH` (5 s) |
| `log` | `{"last_id": …}` | admins only, as soon as there are new log entries; the page fetches them with `/api/logs?after=…` |

- The playback position (`album/playback/position`, `tag/activity/track_time`) does not count as a change
  (`_without_position`); the page interpolates it. The 5 s refresh re-synchronizes it and keeps the connection open.
- The stream ends when the session expires, the user logs out or is removed, and when the API stops (`_stopping`).
- If the stream breaks, the page polls `/api/status` every second until the `EventSource` is connected again; on 401
  it shows the login.
- The server polls `BoxControl.status()` every 0.2 s instead of hooking into every state change (state machines,
  volume, LED, tags). That keeps the server decoupled from the domain objects, costs one status build per tick and
  open stream, and sends only changes over the network.

**`LogBuffer`** (`web/logbuffer.py`) is attached to the logger of the service and keeps the last 5000 entries with a
running id for `/api/logs?after=…`.

## 5. Web page

`web/web/`: one page without a build step (`index.html`, `app.js`, `style.css`, no framework), shipped in the wheel.
What the user sees is described in the [user manual](../user/en/manual.md), section 6.

**Texts.** The page has no text of its own. Every text is in `web/web/texts/<language>.json`, one flat JSON object
per language with English keys (`tabs.now`, `tags.remove.confirm`): `de.json` and `en.json`. The server delivers
every file of that folder without a login (`/texts/de.json`), and the page loads the one of its language before it
shows anything.

**Language.** The page is English until a language is chosen on the device. The choice is a list with the names
of the languages in three places: on the login, in the top bar and in the tab "System". The browser keeps it
(`localStorage`, key `tinyjukebox-language`), so it holds for this device and this address, and the page loads again
in the chosen language; the tab stays, it is in the address. Dates, times, numbers and the order of lists follow
the settings of the browser, not the language of the page. A further language is a text file and an entry in
`LANGUAGES` of `app.js`.

| Where | How a text gets there |
| --- | --- |
| an element of `index.html` | `data-t="key"`: the text is all the element contains. Text next to a field has a `<span>` of its own |
| a tooltip, label, placeholder, question | `data-t-title`, `data-t-label` (`aria-label`), `data-t-placeholder`, `data-t-confirm` |
| a sentence around an element that the page fills | the element stays in `index.html` with `data-part="uid"`, the text has `{uid}` at its place |
| a text that `app.js` builds | `t("key", { name })`; `{name}` in the text is the value |
| a state or another name from the server | `tOf("state", name)`: the text of `state.<name>`, or the name itself if there is none |

- A text that depends on a number has one entry per form (`{"one": "{n} Datei", "other": "{n} Dateien"}`); `t()`
  takes the form of the value `n` with `Intl.PluralRules`.
- A fixed text may contain `<b>`, `<code>` and `<strong>`; the page builds these elements. It never puts HTML into
  the page (`innerHTML` is not used).
- A key is written out where it is used and never put together, a text is a whole sentence, and a text is never a
  value the code works with. `tests/unit/test_texts.py` holds this: every key that is asked for exists, no text is
  left over, `index.html` contains nothing but the name of the project, and every language has the same keys with
  the same values.

Why a file per language and nothing more: the page has no build step and no framework, and two languages and one
page need neither gettext nor a translation platform. German is one text file like every other, not a text in the
page that is replaced – it would exist twice and drift apart. What keeps the languages together are the tests.

Known limit: where the page tells how to allow NFC in Chrome, it quotes the labels of Chrome in the language of the
page; Chrome uses the language of the phone, which can be the other one.

| Tab | Content | Visible to |
| --- | --- | --- |
| Now | cover, title, position, player buttons, volume, link an unknown tag | all (changes: admin) |
| Tags | tags with the cover of their album or station as tiles or a list, filtered by albums or stations (kept in the browser); a click places the tag on the virtual reader | all |
| Tag management | tags with cover, name, UID, target, progress; create a tag | all (changes: admin) |
| Albums | list of the albums, sortable by every column; album dialog with tracks and problems, scan for new folders | all (changes: admin) |
| Radio | stations, test a stream | all (changes: admin) |
| Bluetooth | paired headset, pair/unpair, limits; virtual headset without hardware | all (changes: admin) |
| Statistics | what was played on this box: per tag, per day, stations, skipped tracks, operation per version | all |
| Upload | upload an album | admin |
| System | state, sanity check, library, version of the box software, own password, users, progress per tag | all (users, checks, version: admin) |
| Log | live log with level and text filter | admin |

The page of the library (`mode` `library`) has no "Now" and "Bluetooth", "Statistics" next to its boxes, and these
tabs in addition:

| Tab | Content | Visible to |
| --- | --- | --- |
| Boxes | boxes with version, last contact and state; albums on their way; bootstrap password, tokens | all (changes: admin) |
| Versions | versions of the box software with their states; look for a release; token for the releases | all (changes: admin) |
| Statistics | the same as on a box, for all boxes or for the box that is chosen; with all boxes a table of the boxes, and one row per day and box | all |

- The tab "System" has the card "Backup" for admins, on a box and on the library: two links that fetch
  `GET /api/backup`, without and with the albums. On the library the card also sets the schedule and the folder of
  the backups it makes by itself, makes one at once and lists the backups of the folder. After "Back up now" the
  page asks every two seconds until that backup is made; otherwise it shows what was there when the tab was opened.

- The tab "Tag management" of the library has the button "Scan tag", in the card "Create a new tag", where the browser
  can read tags (`NDEFReader`, Chrome on Android), and on every phone – there a tap says why it cannot scan. The
  page reads the UID of a tag held to the phone, sends `PUT /api/tags/{uid}` and shows the
  tag; an admin names and links it there. No other page reacts to a scan, and nothing is ever written to a tag
  ([library.md](library.md), section 5.1).
- Elements with the class `admin-only` are hidden for the role `user`; the server enforces the roles independently.
- Elements with the class `box-only` or `library-only` are hidden on the other kind of page (`is-library` on `body`).
- The tab "System" of a box shows the library (address, connection, changes not confirmed yet) and, for admins, the
  version with the commands of `tinyjukebox-slot`.
- The tab "Bluetooth" and the headset icon next to the LED are drawn from `status.bluetooth`, i.e. they arrive with
  the status events.
- The LED next to the logo mirrors the LED of the box (`RGBLed.get_state()`).
- Without hardware the tab "Bluetooth" additionally shows the virtual headset (admins only; the route allows `user`).
