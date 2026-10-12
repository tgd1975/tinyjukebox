# Concept: Spotify – albums, playlists, audiobooks and podcasts on a tag

Status: concept, nothing implemented and nothing tried on a box yet; written against version 0.28.0, the players
researched on 2026-10-12 (sources: section 11). What must be tried before anything is built is step 0 of section 9.
Related today: radio stations ([architecture.md](../architecture.md), sections 2.3 and 4;
[rest-api.md](../rest-api.md)), the catalog and its synchronization ([library.md](../library.md), section 4).

## 1. What it is

A tag can be linked to something on Spotify, the way it can be linked to a radio station today: the entry is
created on the page from a link, gets a name and a cover, and is linked to a tag in "Tag management". The tag on the
box plays it; the tag taken off ends it.

Like a station it comes from the internet and is not stored on the box. Unlike a station it has tracks and a
position, so on the box it behaves like an album:

| | Album | Radio station | Spotify entry |
| --- | --- | --- | --- |
| Where the sound comes from | files on the box | the internet | the internet |
| Plays without WLAN | yes | no | no |
| Needs | nothing | nothing | a Spotify account, connected to the box (section 5) |
| Created from | an upload | name and address of the stream | a link of Spotify |
| Cover | set by hand, or from the files | set by hand | fetched from Spotify, can be replaced by hand |
| Position | saved, continues | none (live) | saved, continues |
| "Next", "back", "back" held | yes | no effect | yes |
| Pause | keeps the position | ends the stream | keeps the position |
| Test without a tag | – | yes, one minute | yes, one minute |
| Travels to the other boxes | data and files | data and cover | data and cover |

## 2. What plays it

Two programs can play Spotify on a Raspberry Pi and be told by the box what to play. Both play playlists.

| | Spotify Soloist | go-librespot |
| --- | --- | --- |
| From | Spotify itself, published 2026-08-13 | volunteers; speaks the protocol of the Spotify apps without being one |
| Tested by its makers on | a Raspberry Pi 3 with the Debian trixie of Raspberry Pi OS, 64 bit – the system of the box | Raspberry Pi in general |
| Which accounts play | every account | **only older accounts.** Since November 2025 Spotify refuses the old way to the music for more and more accounts; accounts created in 2026, also new members of a family subscription, play nothing. The authors call it a dead end: the new way is protected and may not be rebuilt |
| Albums, playlists, podcasts | yes | yes |
| Audiobooks | yes ("the whole catalog") | not named |
| Start an album or playlist from its address | yes | yes |
| Start in the middle, at a saved track | no – it starts at the first track and is skipped forward (section 3.1) | yes, directly |
| Sound | only into PipeWire or PulseAudio, which the box does not have | into a pipe or ALSA, which fits the box as it is |
| Login | the box is chosen once as a speaker in the Spotify app of a phone in the same WLAN | a code entered at spotify.com/pair |
| Needed besides | a key from Spotify's developer page, made once by the owner with a Premium account | nothing |
| Getting it | only from Spotify's own address; must not be passed on; every build stops working 90 days after it was built | free program (GPL-3.0), can be part of our release |
| When Spotify changes something | Spotify's own product | can stop for everybody, as for the new accounts now |

**The box uses Spotify Soloist** (decided, section 10). It is the way Spotify provides, it plays for every account
– also for an account made for the box tomorrow – and it plays audiobooks. Its price is paid once, in building: the
sound has to be brought from PipeWire or PulseAudio into the output of the box, a saved position is reached by
skipping, and the box has to fetch and renew the program itself.

go-librespot is no way back: there is no account old enough for it.

Looked at and not taken:

| Way | Why not |
| --- | --- |
| Spotify's Web API | it only steers a player that already runs; and since February 2026 an app registered for it is limited to five users and needs a Premium account of its owner |
| librespot, raspotify, spotifyd | speakers only – something else has to tell them what to play; and the account problem of go-librespot |
| Mopidy with its Spotify extension | a music server of its own, beside ours, on librespot |
| Saving the music on the box | Spotify does not allow it, and no part of this concept does it |

What follows, and cannot be changed by us:

- **It needs the internet**, every time. Nothing of Spotify plays without WLAN.
- **Spotify Premium is needed** for the key. Spotify writes that free accounts can then listen too; this concept does
  not count on it.
- **One account plays in one place at a time** (section 5).

## 3. What can be linked

An entry is created from the link that the Spotify app gives ("Share", "Copy link"), e.g.
`https://open.spotify.com/playlist/…`, or from the form `spotify:playlist:…`. The kind is read from the link.

| Kind | Plays | Position | When the end is reached |
| --- | --- | --- | --- |
| Album – also most radio plays for children, which Spotify lists as albums | its tracks in order | track and second | finished, like an album: the next time it starts from the beginning |
| Playlist (section 3.1) | its tracks in the order it has at that moment | track and second; if the track has left the playlist meanwhile: from the beginning | finished |
| Audiobook | its chapters in order | chapter and second | finished |
| Podcast | its episodes in the order Spotify lists them | episode and second | finished |
| A single episode or track | this one | second | finished |
| Artist | what Spotify puts together for the artist – it changes | track and second; from the beginning if the track is gone | finished |

- Nothing is shuffled and nothing is repeated, whatever the account is set to elsewhere. What Spotify adds by itself
  after the last track ("autoplay") is not played: the entry is finished there.
- The documents of Soloist name track, album, playlist and episode as what can be started. Podcast, audiobook and
  artist are tried in step 0; a kind that does not start is refused by the page with a sentence that says so.
- Something that Spotify does not offer in the country of the account, or has withdrawn, does not play: the tag ends
  like an album that cannot be played, and the tab "Now" says why.

### 3.1 Playlists

Playlists are a must. What is known, and how:

| Point | State | From |
| --- | --- | --- |
| Start a playlist from its link | yes. The example in the documents of Soloist for starting something is a playlist | documents of Soloist (`play` with `uri`) |
| Name and cover when the entry is created | yes. Both were returned for a playlist of Spotify and for a playlist of a user | tried on 2026-10-12 (Spotify's oEmbed address, no account needed) |
| Order | the order of the playlist; shuffle is switched off by the box before every start | documents |
| Pause, next, back, position within the track | yes | documents |
| **Continue at the track where it stopped** | **possible, but not at once.** Soloist always starts a playlist at its first track; the request to name a starting track is open at Spotify without an answer. The box skips forward to the saved track, silently. Somebody measured 0.15 s per track on a smaller Raspberry Pi: track 22 after 3.3 s, track 80 after 12 s. The LED is aqua meanwhile | open request no. 9 at Soloist; to be measured on the box in step 0 |
| The playlist was changed in Spotify meanwhile | it plays as it is now. The box finds the saved track by its address in the list of what comes next, wherever it has moved; if it is gone, the playlist starts at the beginning | documents (`get_queue` lists everything that comes) |
| Long playlists | no limit is named. The cost is the skipping above | – |
| Whose playlists | everything the connected account can open: public ones, its own, private ones it was invited to. "Discover Weekly" and the like are those of the connected account | – |
| A private playlist | plays on a box whose account may open it. Name and cover probably cannot be fetched – set by hand | not tried |

Not tried by anybody for this concept: playing itself. That needs the box and an account and is step 0.

For albums the skipping is the same; a radio play with 30 tracks continued at track 20 starts about 3 seconds
later than at track 1.

## 4. On the box

| Event | What happens |
| --- | --- |
| The tag is placed | LED aqua while it connects and goes to the saved position, then blue: it plays |
| The tag is taken off | the playback ends, the position is saved |
| The same tag again | continues at the saved position |
| Play/pause | pauses (LED white/blue) and continues, at the same position |
| "Next", "back" | as with an album: "back" within the first seconds goes to the track before, otherwise to the start of the track |
| "Back" held | from the beginning |
| The last track has ended | finished (LED blue, blinking), as with an album |
| No WLAN when the tag is placed, or the WLAN is lost while it plays | LED aqua, the box tries again and again, as with a radio station; it continues at the position it had |
| The box is switched off while it plays | the position of the last save counts (saved every few seconds, as with an album) |
| The headset connects or is lost | as with an album: plays on the new output; pauses when the headset is lost |
| The account plays somewhere else meanwhile (section 5) | the box shows a pause; "play" on the box takes the playback back |
| No account is connected, or the key is missing | the tag ends like an album that cannot be played; the tab "Now" says what is missing |
| Volume | the buttons and limits of the box, as always. The volume slider of the Spotify app does nothing to the box |

The tracks are levelled to the same loudness by Soloist, so one album is not louder than the next.

## 5. The account

Two things are done once per box, on its page, tab "Spotify", card "Account":

1. **The key.** The owner makes a "Soloist API key" on Spotify's developer page (developer.spotify.com, logged in
   with a Premium account) and pastes it into the card. With the key the box fetches the player from Spotify.
   Spotify gives the key to one person; it is not to be passed on. Whether one key serves several boxes of the same
   person at the same time is tried in step 0.
2. **Connect.** After "Connect" the box appears for a few minutes as a speaker in the Spotify app of every device
   in the same WLAN – the app on a phone, a tablet or a PC. Choosing it there once connects the box to the account
   of that app. "Disconnect" removes the account from the box. Soloist does not tell which account it is; the card
   shows "connected" and since when.

   This is the only login Soloist has: it takes no user name and password, shows no code and opens no web page, so
   neither our page nor a page of Spotify can do it. The app is needed this one time per box.

- **Key and login stay on that box.** They are not sent to the library, are not in a backup and do not reach
  another box. The entries themselves – what a tag plays – are in the library and on all boxes, as stations are.
- **One account plays in one place at a time.** That is a rule of Spotify. If the box is connected to the account
  that is also used on a phone: a tag on the box stops the music on the phone, and music started on the phone stops
  the box. Two boxes with the same account stop each other the same way. The way out is an account of its own per
  box – a family subscription has six, and with Soloist a new account plays.
- **The box in the Spotify app.** While a Spotify entry plays, the box is one of the devices the app of this account
  offers. What the phone may do with it is the first open item of section 10.

## 6. The page

| Where | What is new |
| --- | --- |
| Tab "Spotify" (new, beside "Radio") | all entries with cover, name, kind and linked tags. "Create a new entry": paste the link – name and cover are fetched from Spotify, the name can be changed before saving. Rename, choose another cover, replace the link, test, delete (only without linked tags and while it is not playing). Changes: admin |
| Tab "Spotify" on a box, card "Account" | section 5; and which build of the player the box has and until when it works. Admin |
| Tab "Tag management" | a tag can be linked to a Spotify entry, as to an album or a station |
| Tab "Tags" | the tiles show the cover of the entry; the choice "All / Albums / Radio" gets "Spotify" |
| Tab "Now" | cover, name, the track that plays with its position, the state (connecting, playing, paused, trying again) and the reason if it does not play |
| Tab "Statistics" | entries count like albums: per tag, per day, the tracks that were skipped |
| Page of the library | the tab "Spotify" without "Account" and without "Test": the library does not play |

- If name and cover cannot be fetched (no internet, a private playlist), the entry is created with the link as its
  name and without a cover; both are set by hand.
- "Test" plays the entry for one minute on the box while no tag is on it; a tag that is placed ends the test. The
  same rule as the test of a station.
- The page does not change because the account was connected or an entry was created somewhere else; it shows it
  with the next load, as everywhere.

## 7. How it is built

### 7.1 Data

| File | Change |
| --- | --- |
| `targets.json` | a third kind: `{"target-id", "target-type": "spotify", "name", "uri", "created", "cover"}`. `uri` is the form `spotify:<kind>:<id>`, whatever was pasted; `cover` names an image `spotify-<random>.<type>` in the images folder |
| `tags.json` | `target.type` gets `"spotify"` |
| `activity.json` | `target-type` gets `"spotify"`; such an entry has `track-uri` (the track, chapter or episode) in the place of `track-id`, and `track-time` and `album-finished` as an album has |

- The entries, their covers and the positions are synchronized like stations, their covers and the positions of
  albums (library.md, sections 4.1 and 4.8). Positions stay per box.
- Name and cover come from Spotify's oEmbed address (`https://open.spotify.com/oembed?url=…`), which needs no
  account: it is asked once, by the box or the library on whose page the entry is created. The image (300 pixels)
  is copied into the images folder; nothing is fetched from Spotify when the page is shown.
- To check when implementing: a box with an older version does not know the kind `spotify` and its schema refuses a
  catalog that has one. The library gets the version first; whether the entries are held back for a box that has
  not updated yet or the interface number rises (library.md, section 4.10) is settled then.

### 7.2 Parts

| Part | Module | What it does |
| --- | --- | --- |
| `TargetSpotify` | `data/target.py` | the entry; `check_spotify_link` turns a link into `uri` and kind and refuses what is not one |
| `PlaySpotify` | `stm/playspotify.py` | the state machine of an entry that plays: `PLAY`, `PAUSED`, `RESUME`, `FINISHED`, `END` – `PlayRadio` with the position, the buttons and the end of `STMPlayAlbum` |
| `SpotifyPlayer` | `albumplayer/spotifyplayer.py` | owns Soloist and the output, in a thread of its own like `RadioPlayer`; states `connecting`, `playing`, `retrying`, `stopped` and `error` |
| `SpotifyAccount` | `spotify.py` | the key, fetching and renewing the player, connect, disconnect |
| `STMOperation` | `stm/stmoperation.py` | `PLAY_ALBUM` creates `PlaySpotify` (`spotify_factory`) for such a target; `TEST_RADIO` becomes the test of a station or an entry |

### 7.3 Soloist

- **A process of the box, not a service.** `SpotifyPlayer` starts it when an entry is to play (tag, test) and ends
  it afterwards, as it does with `ffmpeg`. A box without entries runs nothing, and the box is a device of the
  account only while it plays Spotify. If step 0 shows that the start takes too long, the process stays running
  after the first use and the box ends whatever it did not start itself.
- **Its files** are in a folder `spotify` of the data of the box: the key, the program, its data directory with the
  login (`--data-dir`) and its cache (`--cache-dir`, `--cache-size 100`, the smallest value). The folder is not
  synchronized and not part of a backup.
- **Started as** `soloist --device-name <name of the box> --api-key … --initial-volume 100 --ws 127.0.0.1:0`
  with the two folders and the output below. The key is visible in the list of processes of the box (open report
  no. 12); on a box only its own users see that.
- **Sound:** Soloist plays into PipeWire, or into PulseAudio where there is no PipeWire. The box has neither
  today: it plays with `aplay` straight to ALSA, and to the headset through BlueALSA. One of the two is installed
  and gets a single output that writes 16 bit stereo, 44.1 kHz, into a named pipe; `aplay` reads it, on the device
  `AudioOutput` names. So Soloist takes the place of `ffmpeg`, and everything the box does with the output stays as
  it is: speaker or headset, the volume the headset is opened with, the loss of the headset that pauses,
  `--no-audio`. The sound server is started with Soloist and ended with it; it is given no sound card and no
  Bluetooth, because left to itself it would take both and get in the way of BlueALSA and of the volume of the
  box. Both have such an output (`module-pipe-sink` of PulseAudio, the pipe tunnel of PipeWire). PipeWire is tried
  first, because one user reports for PulseAudio that every track that follows another stays silent (open report
  no. 15, one setup, not confirmed by others); if PulseAudio turns out simpler and plays, it is taken.
- **Volume:** Soloist stays at 100; if the Spotify app changes it, the box sets it back. The `VolumeManager` and its
  limits apply as for every other sound.
- **Steering:** with the program's own command, as the box reads `rfid-reader`: one `soloist ctl trace` runs beside
  the player and reports every event as a line; commands are single calls (`soloist ctl play <uri>`, `pause`,
  `next`, `prev`, `seek`, `shuffle off`, `repeat off`, `queue --json`). No WebSocket client is added. If the calls
  are too slow for the skipping, a client that speaks to the WebSocket directly replaces them.
- **Start of an entry:** shuffle and repeat off, `play` with the `uri`. With a saved position: no sound yet, the
  list of what comes is read, the saved track is looked up by its address, that many times `next`, `seek`, sound.
- **Position:** from the events (`track_changed`, `position_sync`); saved every `update-schedule` seconds and when
  `PLAY` is left, as `STMPlayAlbum` does.
- **End:** nothing plays any more, or the track that starts was added by Spotify (`source: autoplay` in the list) –
  the box pauses there and the entry is finished.
- **Failures:** no connection, or a connection that is lost – `retrying` with the delays of `RadioPlayer`, LED aqua.
  Soloist reports no errors to its interface and can stand still while it says "playing" (open report no. 5): a
  playback whose position has not moved for 15 seconds is started again at the saved position. No key, not
  connected, a build that has stopped working and cannot be renewed – `BROKEN_ALBUM`, with the reason in `error`.

### 7.4 REST, commands, configuration

| | New |
| --- | --- |
| REST, all | `GET /api/spotify`, `GET /api/spotify/{id}`, `GET /api/spotify/{id}/cover` |
| REST, admin | `POST /api/spotify` `{link, name?}`, `PATCH /api/spotify/{id}` `{name?, link?}`, `PUT /api/spotify/{id}/cover`, `DELETE /api/spotify/{id}`; `PUT /api/tags/{uid\|current}/spotify` `{spotify_id}`, `DELETE /api/tags/{uid}/spotify` |
| REST, a box only, admin | `POST /api/spotify-test` `{id \| link}`, `DELETE /api/spotify-test`; `GET /api/spotify-account` (key there or not, connected or not, build and its last day), `PUT /api/spotify-account/key`, `POST /api/spotify-account` (starts connecting), `DELETE /api/spotify-account` |
| `GET /api/status` | `spotify`: name, `uri`, kind, `cover`, `state`, `error`, the track with title, position and length, `test` |
| Commands | `list_spotify`, `add_spotify LINK [NAME …]`, `set_spotify_name`, `set_spotify_link`, `set_spotify_cover`, `remove_spotify`, `test_spotify`, `stop_spotify_test`, `link_tag_spotify`, `link_current_tag_spotify`, `spotify_account`, `set_spotify_key`, `connect_spotify`, `disconnect_spotify` |
| Config | nothing new |

### 7.5 Journal, health check, backups

- **Journal:** "tag placed" names the entry with the kind `spotify`; "track played" carries the `uri` and the title
  of the track in the place of the track number. The statistics need no other change.
- **Health check of a new version** (library.md, section 6.4): a Spotify entry that plays never confirms a version
  and never condemns one, like a station – whether it plays depends on the internet and on Spotify.
- **Backups:** the entries and their covers are in every backup, as the stations are. Key and login are not.

### 7.6 Where Soloist comes from

- **The box fetches it itself, from Spotify.** Spotify does not allow passing the program on: it is not in the
  repository, not in a slot and not in a release. The box loads the build for its processor from Spotify's address
  when the key is entered, into the folder `spotify`.
- **Every build stops working 90 days after it was built.** The box fetches the current one when its own is older
  than 30 days – looked at once a day and at the start, when there is network – and when Soloist ends saying that
  it has expired. The build before is kept and used again if the new one does not start. A box that was switched
  off for months fetches a new build before the first Spotify tag plays; that needs the internet, as playing does.
  Spotify offers only "the current build", without numbers and checksums (open report no. 12); the address is
  HTTPS.
- **From the system** Soloist needs the sound server (`pipewire` and `wireplumber`, or `pulseaudio`) and
  `libatomic1`; the playbook installs them. A slot does not demand them: on a box without them the albums and stations play as before, a Spotify tag
  ends as "cannot be played" and the page names what is missing.
- The simulator on the PC fetches the build for the PC the same way if a key is entered, or has none: then the
  entries can be managed but not played.

## 8. Left out

| Left out | Reason |
| --- | --- |
| Playing without WLAN | Spotify does not allow keeping the music |
| Key and login in the library, one login for all boxes | nothing secret is synchronized; and one account plays on one box at a time anyway |
| Searching Spotify on the page | needs the Web API and its registration; the link from the Spotify app is enough |
| Shuffle, repeat | a tag plays the same thing in the same order; can be added per entry later (section 10) |
| The box as a speaker for whatever the phone chooses | the tag decides what plays (section 10) |
| The albums of Spotify in the tab "Albums" | they are not albums of the library: no files, no upload, no tracks to edit |
| Other services | every one has its own program; the kind `spotify` is not made general in advance |

## 9. Implementation plan

### Step 0 – The probe on the box

By hand, with Soloist beside the running software, before anything is built. It needs the key and an account.

| Question | Decides |
| --- | --- |
| Does a playlist play: started from its address, in order, to its end; a long one; a private one; one of Spotify | the must of section 3.1 |
| Sound: PipeWire, then PulseAudio, with an output into a pipe and `aplay` behind it – on the speaker and on the headset; several tracks in a row; pause, another `aplay`, continue; what happens when nobody reads the pipe | the sound path and which of the two (section 7.3) |
| Connecting: does the box appear in the Spotify app while `avahi-daemon` runs? Soloist is reported to announce nothing when the port for it is taken, without saying so (open report no. 1), and the box uses that port for its name `tinyjukebox.local` | how "Connect" works (section 5) |
| Does the login survive a restart and a power cut? Does one key run two boxes at once? | section 5 |
| Seconds from the start of the process to the first sound; and per skipped track on the way to a saved position, with `soloist ctl` and directly | started per tag or kept running; `soloist ctl` or a WebSocket client (section 7.3) |
| Which kinds start: podcast, audiobook, artist, a single track | section 3 |
| The end of an album and of a playlist: what the events show, and whether Spotify's additions can be recognized | "finished" (section 7.3) |
| What the events show without WLAN, when the WLAN is lost, when the phone takes the playback over, when the phone starts something else on the box | the failures and the first open item of section 10 |
| Memory and processor of the Pi 3 while it plays, also with the headset; the size of the cache on the card | whether anything has to be limited |
| Renewing: the build of today replaced by the next one while the login stays | section 7.6 |

### Step 1 – Entries

The kind `spotify` in `targets.json`, `tags.json` and their schemas; `check_spotify_link`; name and cover from
Spotify; the tab "Spotify" with create, rename, cover, link, delete; linking in "Tag management"; the tiles and the
choice in "Tags"; the commands; the synchronization with the library, covers included. A tag linked to an entry
ends as "cannot be played" until step 3.

### Step 2 – Account

The packages in the playbook; the folder `spotify`; `SpotifyAccount`: the key, fetching and renewing the build,
connect and disconnect, with the card "Account", its routes and commands.

### Step 3 – Playing

`SpotifyPlayer` with the sound server and the pipe, `PlaySpotify`, `spotify_factory`, the position in `activity.json` and
its synchronization, the LED, the headset, the tab "Now", the test. Tests as for `RadioPlayer` and `PlayRadio`: a
program in the place of `soloist` that answers its commands and writes its events, and the `ManualClock`.

### Step 4 – Journal and statistics

The journal lines, the statistics, the health check.

### Step 5 – The documents

architecture.md (sections 2, 4, 6), rest-api.md, operations.md (commands, the images folder), configuration.md,
box.md (packages, audio), deployment.md (acceptance checklist), library.md (sections 4.1, 4.8, 7), the manuals and
quick guides (de, en), the page texts, CHANGELOG. This file is deleted.

Each step: version bump, CHANGELOG.

## 10. Decided and to decide

Decided on 2026-10-12:

1. The box plays Spotify with Spotify Soloist. There is no old account, so go-librespot is not an option.
2. Each box is connected on its own page: the key is pasted, then the box is chosen once as a speaker in the Spotify
   app. Soloist has no other login. Key and login stay on that box.
3. A playlist tag that is placed again continues where it stopped, like an album, with the wait of section 3.1.
4. The box fetches the player from Spotify and renews it by itself.

To decide:

1. **What the Spotify app on a phone may do with the box.** Proposal: the box plays Spotify only for a tag. While
   the tag plays, the phone of the same account can pause, continue and skip within what the tag plays, like the
   buttons. If the phone starts something else on the box, the box goes back to what the tag plays; without a tag
   the box is not offered to the phone at all.
2. **A podcast continues where it stopped, like an album and a playlist.** The other way would be: a podcast tag
   always starts the newest episode unless one was interrupted. Proposal: like an album.
3. **A tab "Spotify" of its own, beside "Radio".** The other way would be one tab for both. Proposal: its own tab –
   the account and the tracks have no counterpart in "Radio".
4. **The app ([app.md](app.md)):** a phone in simple mode shows the tiles of Spotify tags but does not play them;
   it says that this tag plays on a box. Playing them on the phone would need the Spotify app there and is a
   concept of its own. Proposal: as described.

## 11. Sources

Read on 2026-10-12.

- Spotify Soloist: [announcement](https://developer.spotify.com/blog/2026-08-13-introducing-spotify-soloist),
  [documents](https://developer.spotify.com/documentation/soloist) (overview, getting started, authentication,
  command line, `soloist ctl`, WebSocket API, downloads and updates),
  [open reports](https://github.com/spotify/soloist/issues) (no. 1 announcing, 5 standing still, 9 starting track,
  12 key and checksums, 15 PulseAudio, 16 `libatomic`)
- go-librespot: [repository](https://github.com/devgianlu/go-librespot), version 0.10.3 of 2026-10-03; accounts
  that do not play: [go-librespot no. 279](https://github.com/devgianlu/go-librespot/issues/279),
  [librespot no. 1649](https://github.com/librespot-org/librespot/issues/1649)
- Web API: [Spotify, 2026-02-06](https://developer.spotify.com/blog/2026-02-06-update-on-developer-access-and-platform-security)
- Name and cover: [oEmbed](https://developer.spotify.com/documentation/embeds/reference/oembed)
