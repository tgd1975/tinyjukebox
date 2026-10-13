# Tiny Jukebox – user manual

Tiny Jukebox is a music box for children. Put a tag on the box, and the album of the tag continues where it
stopped last time. The box is operated with four buttons; an LED shows its state. Albums, tags and radio stations
are managed on a web page. If there are several boxes or a library in the home network, the boxes share albums,
tags and stations (section 11).

The essentials on one card: [quick guide](quick-guide.md). Technology, setup and troubleshooting for
administrators: [`docs/developer/`](../../developer/operations.md).

Contents:

1. [Terms](#1-terms)
2. [Operating the box](#2-operating-the-box)
3. [LED](#3-led)
4. [Pause](#4-pause)
5. [Bluetooth headset](#5-bluetooth-headset)
6. [Web page](#6-web-page)
7. [Managing albums, tags and radio stations](#7-managing-albums-tags-and-radio-stations)
8. [Limiting the volume](#8-limiting-the-volume)
9. [Users and passwords](#9-users-and-passwords)
10. [If something does not work](#10-if-something-does-not-work)
11. [Library](#11-library)

---

## 1. Terms

| Term | Meaning |
| --- | --- |
| Tag | what is put on the box: a disc, a figure, a card – anything with an RFID chip. The box recognizes the chip by its unique number (UID), e.g. `04:1A:2B:3C:4D:5E:6F`, not the object. |
| Album | a folder with MP3 files and a list of its tracks (playlist) |
| Radio station | an internet radio stream; needs WLAN |
| Linking | assigning an album or a radio station to a tag |
| Cover | the picture of an album or a radio station. A tag shows the cover of its album or station. |
| Progress | the saved position of an album, per tag |
| Library | a program in the home network (e.g. on the NAS) that holds albums, tags and stations for all boxes |

---

## 2. Operating the box

### 2.1 Switching on and off

- **Switching on:** connect the power. During the start the LED is red, then magenta. When it is green, the box is
  ready.
- **Switching off:** disconnect the power. This is allowed at any time, also while an album plays. The box saves the
  position every 5 seconds; next time it continues a few seconds earlier at most.
- The box also plays without WLAN. Only the web page and radio stations need it.

### 2.2 Tags

| Action | Effect |
| --- | --- |
| Put a tag on the box | The album continues at the saved position. A radio station starts to play. |
| Take the tag off | Playback stops, the position is saved. |
| Put another tag on | Its album plays. |
| Put a new, unknown tag on | The LED is yellow. The box remembers the tag; it is linked on the web page (section 7.2). |

Every tag has its own progress. An album that was listened to the end starts from the beginning the next time
its tag is put on.

### 2.3 Buttons

| Button | Press briefly | Hold |
| --- | --- | --- |
| louder, quieter | one step (5 %) | on in small steps (1 % every 0.2 s) |
| next | next track | – |
| back | to the start of the track; in the first 10 seconds of a track: to the previous track | 1 s: the album starts from the beginning |
| next + back together | – | 5 s: pair a headset (section 5.1) |

- The volume is limited (section 8).
- When the album has ended, "next" has no effect. "back" plays the last track, holding "back" for 1 s the whole
  album from the beginning.
- With a radio station "next" and "back" have no effect. The station is changed with the tag.
- Every press of a button acts at once. While a headset is paired, an album that plays therefore jumps by one track
  at most. To avoid that, take the tag off first.

---

## 3. LED

| LED | Meaning |
| --- | --- |
| red | The box is starting or is stopped. |
| magenta | start or end |
| green | ready, no tag on the box |
| green, blinking | no tag for 10 minutes – the box is still switched on |
| aqua (turquoise) | The album is being loaded, or the radio station connects (also after the stream broke off). |
| green → aqua → blue, back and forth | The box fetches the album or the next track from the library and plays as soon as it is there (section 11). |
| blue | playing |
| blue, blinking | The album has ended. |
| white/blue in turn | pause (section 4) |
| yellow | The tag is not linked to an album or a station. |
| yellow/red in turn | The linked album or the linked station does not exist any more. |
| red, blinking | The album cannot be played (folder, playlist or MP3 files are missing or broken). |
| blue, blinking quickly | The box searches for a headset (60 s at most). |
| white for 2 s | headset paired – then the colour from before again |
| red, blinking quickly for 3 s | no headset found or pairing failed – then the colour from before again |
| red, white, magenta, yellow in turn | The box is out of service because the library is too old for it. Only an administrator can fix that (section 11.4). |

The web page shows the same LED next to the logo.

---

## 4. Pause

During a pause playback halts, the position is saved, and the tag stays on the box. The LED changes between white
and blue.

Pausing and resuming:

- play/pause on the headset
- ⏸ / ▶ on the web page (tab "Now")
- The box itself has no pause button. Taking the tag off and putting it on again later has the same effect.

| During the pause | Effect |
| --- | --- |
| Play | continues at the same position |
| "next" | 1st press: continues at the same position. Only the 2nd press jumps to the next track. |
| "back" | jumps at once: to the start of the track, in the first 10 seconds to the previous track |
| hold "back" for 1 s | The album starts from the beginning. |
| take the tag off and put it on again | continues at the saved position |
| louder, quieter | changes the volume only |

- With an album that has ended, play/pause changes nothing.
- With the radio, the pause ends the stream. Play, "next" or "back" connect again; the radio goes on live, there is
  no position.
- The box pauses by itself when the connection to the headset breaks (section 5.3).

---

## 5. Bluetooth headset

The box plays through a Bluetooth headset as soon as the paired headset is connected. The speaker is silent then.
Principles:

- **Automatic:** If the headset is connected, it plays. Otherwise the speaker plays. There is no menu.
- **Never surprisingly loud:** If the connection breaks, the box pauses. It never changes to the speaker by itself.
- **Hearing protection:** The headset has a start volume and a maximum volume of its own.
- **Exactly one headset:** At most one headset is paired. A new one replaces the old one.

### 5.1 Pairing

1. Put the headset into pairing mode. How that is done is in its manual (Sony WH-CH530: hold the middle button for
   about 5 s until it says "Pairing").
2. Hold "next" and "back" on the box together for 5 s. After that the buttons can be released.
3. The LED blinks blue quickly while the box searches (60 s at most).
4. Result:
   - white for 2 s: the headset is paired and connected.
   - red, blinking quickly for 3 s: nothing found, or pairing failed. The previous headset stays paired.

Further rules:

- The box searches only for audio devices (headsets, speakers) in pairing mode. If it finds several, it takes the
  one with the strongest signal – as a rule the one right next to the box.
- The box itself is never visible. A foreign phone cannot pair with it.
- The same headset can be paired again, e.g. after a factory reset of the headset.
- The search cannot be cancelled; it ends by itself after 60 s. Playback and buttons go on as usual meanwhile.
- Pairing and unpairing is also possible on the web page: tab "Bluetooth", role `admin`.

### 5.2 Every day

- **Switching the headset on is enough.** The box tries to reach it every 15 s.
- If it connects while an album plays, the album goes on in the headset (about 1 s back), and the speaker goes
  silent.
- The web page shows a headset symbol next to the LED as long as it is connected.
- If a second headset is to be used, it has to be paired anew. The first one does not connect any more after that.

### 5.3 Connection lost

Battery empty, out of range, switched off or unpaired:

| Situation | Behaviour |
| --- | --- |
| The headset is gone while an album plays | Pause, the position is saved. The speaker stays silent. |
| … the headset comes back within 10 minutes | It goes on in the headset. |
| … the headset comes back later | The pause stays. Play on the headset resumes. |
| … "next" or "back" on the box | It goes on through the speaker. The buttons act as during a pause (section 4). |
| … take the tag off and put it on again | It goes on through the speaker. |
| The headset is gone, nothing plays | The speaker is active again. |

- If it was paused before the connection broke, it stays paused after reconnecting as well.
- "louder" and "quieter" on the box change only the volume of the speaker during this pause. They do not resume.

### 5.4 Buttons on the headset

| Button on the headset | Effect |
| --- | --- |
| Next, back | like the buttons of the box |
| Play/pause | pause and resume (section 4) |
| Louder, quieter | volume of the headset, limited to its maximum |

### 5.5 Volume of the headset

| | Speaker | Headset |
| --- | --- | --- |
| Start volume | 50 %, when the box is switched on | 40 %, every time it connects |
| Maximum volume | 82 % | 75 % |
| The buttons of the box change | the speaker | the headset, as long as it is connected |

- The headset starts with its start volume every time it connects, never with the volume of last time.
- The values can be changed (section 8).
- The percentages refer to the headset: how loud 75 % is depends on the model. Set the maximum once with the
  headset on the ear.

### 5.6 Notes on headsets

- **A phone nearby:** Many headsets hold two connections at once (multipoint) and play whatever sent sound last. If
  the headset is also connected to a phone, its sounds can drown out the box. Then disconnect the headset on the
  phone.
- **Limit in the app of the headset:** Some headsets limit the volume themselves (Sony: "Max Volume Control" in the
  app "Sound Connect"). That acts in addition to the limit of the box. A factory reset of the headset resets it.
- **LE Audio:** The box uses classic Bluetooth. A setting like "LE Audio Priority" in the app of the headset must be
  switched off.
- **Battery level:** The web page shows it in the tab "Bluetooth" if the headset reports it.

---

## 6. Web page

### 6.1 Opening and logging in

1. Phone, tablet or PC must be in the same WLAN as the box.
2. Open `https://tinyjukebox.local` in the browser.
3. The first time, the browser warns about the certificate: the box issues it for itself. Confirm the warning
   ("Advanced" → "Proceed to tinyjukebox.local" or similar).
4. Choose the language: the page is English the first time. The choice "Deutsch / English" is below the login,
   after the login at the top right, and in the tab "System".
5. Log in with user name and password.

- The browser remembers the language, for this device and this box. On another device and on the page of another
  box or of the library it is chosen once again.
- The login stays; after 7 days without use the page logs out.
- After 5 wrong passwords every further failed attempt blocks the login from this device for 30 seconds.

### 6.2 Roles

| Role | may |
| --- | --- |
| `user` | look at everything and operate the box: pause, track, volume, place tags; scan new tags with the phone in the library; change the own password |
| `admin` | in addition change everything: albums, tags, radio stations, covers, pair a headset, volume limits, users |

The tabs "Upload" and "Log" and all buttons that change something are seen by the role `admin` only.

### 6.3 Tabs

| Tab | Content |
| --- | --- |
| Now | what is playing: cover, album, track, position, list of tracks. Buttons back, pause/play, next, "Album from the start"; volume with slider and buttons. If an unknown tag is on the box: choose an album and "Link & play". |
| Tags | the tab for everyday use: all tags with the cover of their album or station, as tiles or as a list ("Tiles" / "List"), all of them or only those of albums or of radio stations ("All" / "Albums" / "Radio"). The page remembers the choice. A click places the tag on the box as if it really stood there; a second click or "Take off tag" takes it off again. |
| Tag management | all tags with cover, name, UID, album or station and progress. Here tags are created, named, linked, unlinked and deleted. |
| Albums | a list of all albums for managing them: cover, title, number of tracks, playing time, tags, the day they were added and the folder. A click on a column heading sorts by it, a second click turns the order round. A click on the title opens the album: title and cover (both can be changed), list of tracks, linked tags, problems. "Look for new folders" takes in albums that were copied to the box directly. |
| Radio | all radio stations: create, rename, change the address, choose a cover, test, delete |
| Bluetooth | the paired headset: name, connected, battery, volume, start value and maximum; "Pair" and "Unpair" |
| Statistics | what was listened to on this box: per tag (a click on a column heading sorts the table by it, a second one turns the order round), per calendar day with the hours between which something was listened to, on speaker or headphones, per radio station; skipped tracks; per version of the box software starts, errors and fallbacks, for how many days it was installed and for how many hours the box was switched on with it (total) and played something (active); at the very end the tags that were never placed. The library does the counting: if it cannot be reached, the tab says so. A box without a library counts by itself and reaches as far back as the last 1000 entries of its journal. |
| Upload | create a new album from MP3 files |
| System | state of the box, language of the page, "Check consistency", "Reset progress", backup (section 9.1), library (section 11.2), version (section 11.4), change own password, users, progress per tag |
| Log | the messages of the box, live, with a filter |

The buttons in the tab "Now" act like the buttons of the box.

---

## 7. Managing albums, tags and radio stations

Everything in this section needs the role `admin`.

### 7.1 Bringing a new album onto the box

1. Tab "Upload".
2. Enter the title of the album. The folder on the box gets the same name.
3. If you like, choose a cover (JPEG or PNG). It can also be set later.
4. Drag MP3 files into the field, or "Choose files" or "Choose folder".
5. Check the order. It follows the track number in the files, otherwise the file name (2 before 10), and can be
   changed before the album is created.
6. Optionally choose a tag under "Link to tag".
7. "Create album".

Limits: MP3 files only, at most 500 MB per file and 500 files per album; 200 MB must stay free on the box
afterwards. An upload that was broken off is listed under "Unfinished uploads" and expires after one hour.

Changing title and cover: tab "Albums", open the album. Overwrite the title at the top – it is saved as soon as
the field is left. "Change cover" takes a new image (JPEG or PNG) and replaces the previous one. An album without a
cover shows the image from its MP3 files, if they contain one.

Deleting an album: tab "Albums", open the album, "Remove album" – with "Delete the files as well" the MP3 files
disappear too. This is possible only if no tag is linked to the album and it is not playing.

If an album shows problems (the LED blinks red): open the album. The problems are listed there, and "Rebuild
playlist from the MP3 files" writes the list of tracks anew.

### 7.2 Linking a tag to an album

New tag:

1. Put the tag on the box. The LED is yellow.
2. Tab "Now": under "Unknown tag placed" choose the album or the station.
3. "Link & play". The album starts at once.

Known tag:

- Tab "Tag management", column "Album / station": choose another album, or "— not linked —".
- Unlinking is also possible in the tab "Now" ("Unlink" for the tag on the box) and in the album dialog (✕ at
  the tag). The progress is kept.
- Give a tag a name in the tab "Tag management", e.g. "red cat" – that makes it easier to tell the tags apart.
- Deleting a tag: tab "Tag management". Its progress is lost with it.

Several tags can have the same album; each has its own progress.

If there is a library, a new tag can also be created without a box: with the phone (section 11.5).

### 7.3 Radio stations

A tag can play an internet radio station instead of an album. The station plays as long as the tag is on the
box; there is no progress.

1. Tab "Radio", "Create a new station": enter name and address.
2. "Test" plays the address for one minute, also before it is saved. This is possible only while no tag is on
   the box; if one is put on, the test ends.
3. "Create".
4. Tab "Tag management": assign the station to the tag.

- **Address:** the stream itself (e.g. `https://…/stream.mp3`) or a playlist that names it (`.m3u`, `.pls`,
  `.m3u8`). It begins with `http://` or `https://`.
- **Interruption:** If the stream cannot be reached or breaks off, the box connects again after 1, 2, 5, 10 and
  then every 30 seconds. The LED is aqua for that time; the reason is shown in the tab "Now".
- If the address of a station that is playing is changed, it starts with the new address.
- **Cover:** In the tab "Radio" click the image in front of the station and choose an image (JPEG, PNG or WebP). It
  replaces the previous one and also appears in the tab "Now" and with the tags of the station.
- A station that is linked to tags or is playing cannot be deleted. Unlink first.

Examples:

| Station | Address |
| --- | --- |
| 88.6 | `http://radio886.at/streams/radio_88.6/mp3` |
| Ö1 | `https://orf-live.ors-shoutcast.at/oe1-q2a` |
| Ö3 | `https://orf-live.ors-shoutcast.at/oe3-q2a` |
| FM4 | `https://orf-live.ors-shoutcast.at/fm4-q2a` |

### 7.4 Covers

A tag shows the cover of its album or station, in the tabs "Tags", "Tag management" and "Now". A tag has no picture of
its own: two tags with the same album show the same cover, and a tag that is not linked shows none.

- Cover of an album: tab "Albums", open the album, "Change cover" (section 7.1).
- Cover of a station: tab "Radio", click the image in front of the station (section 7.3).

### 7.5 Progress

- Tab "System", "Progress per tag": the saved position of every tag.
- "Reset progress" sets all albums to their beginning.
- A single album starts from the beginning by holding "back" for 1 s on the box or with "Album from the start" in
  the tab "Now".

---

## 8. Limiting the volume

Speaker and headset each have a start value and a maximum:

| | Start value | Maximum |
| --- | --- | --- |
| Speaker | applies when the box is switched on | applies always |
| Headset | applies every time it connects | applies always, also for the buttons on the headset |

Changing them (role `admin`): tab "Now", "change" below the volume slider – or tab "Bluetooth", "Change start value
and maximum".

- The rule is: 0 ≤ start value ≤ maximum ≤ 100.
- A lowered maximum lowers a higher current volume at once.
- A new start value takes effect the next time the box is switched on or the headset connects.
- The values are kept, also after switching off.
- The role `user` sees the values but cannot change them. So nobody changes the hearing protection in passing.
- The box can limit the buttons on the headset only for headsets that align their volume with the box ("Absolute
  Volume"). The volume that the box itself sets is always limited.

---

## 9. Users and passwords

- **Change own password** (every user): tab "System", "Change own password" – with the current password. A new
  login is needed on all other devices afterwards.
- **Managing users** (role `admin`): tab "System", "Users" – create a user (role `user` or `admin`), set a
  password, delete.
  - A new password logs the user out everywhere.
  - The own user cannot be deleted. So there is always an admin.
  - The role of an existing user cannot be changed on the web page: delete the user and create it again.
- Passwords have at least 8 characters.
- **Logged out by itself:** a page on which nothing was done for 30 minutes logs out and says so on the login
  page. Moving the mouse, a tap or a key counts as doing something; an upload that runs counts too. The time can be
  changed or switched off in the config of the box or of the library
  ([configuration.md](../../developer/configuration.md), section 5).
- Password forgotten: an admin sets a new one. If no admin knows the password any more, only access to the box
  itself helps ([operations.md](../../developer/operations.md), section 4).

### 9.1 Backup

A backup is a file with the data of the box: tags, links, progress, volumes and the covers of the stations. Only the role
`admin` can make one.

- Tab "System", "Backup": **"Download backup"** saves the file on the device you are sitting at. It is small.
- **"Download backup with MP3s"** takes the albums along as well. The file is then as large as all albums
  together, and the download takes that long.
- Users, passwords and keys are never in a backup.
- A box that works with a library needs no backup of its own: everything is in the library, and the library backs
  itself up (section 11.6).
- A backup is not put back through the web page, but by hand
  ([operations.md](../../developer/operations.md), section 10.3).

---

## 10. If something does not work

| Observation | Cause and remedy |
| --- | --- |
| LED off | no power: check power bank and cable |
| LED stays red | The box does not start. Disconnect the power and connect it again. If it stays that way: administrator. |
| LED green, the tag is not recognized | Put the tag in the middle. Try another tag. If the box recognizes no tag any more: disconnect the power and connect it again. |
| LED yellow | The tag is not linked: section 7.2 |
| LED yellow/red in turn | The album of the tag was deleted: link it again (section 7.2) |
| LED red, blinking | The album is broken: tab "Albums", open the album (section 7.1) |
| LED aqua, no sound (radio) | The station cannot be reached: check the WLAN; the reason is shown in the tab "Now" |
| LED green → aqua → blue, it takes long | The box fetches the album from the library. The first track takes some seconds, depending on the WLAN. If the LED starts blinking red: the library cannot be reached, and the album has never been on this box. |
| LED red, white, magenta, yellow in turn | section 11.4 |
| LED white/blue, no sound | Pause. Play, "next", or take the tag off and put it on again (section 4) |
| LED blue, no sound | Is a headset connected? Then only it plays. Check the volume. |
| The headset does not connect | Switch the headset off and on, wait 15 s. Connected to a phone? Disconnect it there. Otherwise pair again (section 5.1). |
| Pairing: the LED blinks red | The headset was not in pairing mode or too far away: repeat. |
| The sound in the headset is overlaid by other sounds | The headset is also connected to a phone (section 5.6). |
| The web page does not open | Device in the same WLAN? Box switched on (LED)? `https://` in front? |
| Login refused | Check the password. After several failed attempts wait 30 seconds. |
| The album starts from the beginning instead of the old position | The album had been listened to the end, or the tag was linked to another album, or the playlist was shortened. |

Troubleshooting for administrators: [operations.md](../../developer/operations.md), section 9.

---

## 11. Library

The library is a program in the home network, for example on the NAS. It holds all albums, tags, radio stations
and covers. Every box fetches from there what it lacks – and without the library it plays what it has already.
A box without a library works as before.

### 11.1 What changes for the box

- **All boxes know the same tags.** A tag that was linked on a box or in the library plays on every box.
- **Albums come when they are needed.** If a tag is put on a box that does not have the album yet, the box
  fetches it. The LED changes green → aqua → blue meanwhile, and the box plays as soon as the first track is there.
- **The progress belongs to the box.** The same tag continues at another position in the nursery than in the
  kitchen.
- **Nothing is lost without WLAN.** What is changed on a box applies there at once and reaches the library as soon
  as the box reaches it again. If both change the same tag, the state of the library applies.
- **The space on the SD card takes care of itself.** If it gets short, the box deletes the files of the album that
  was not listened to for the longest time. The library still has it, and the box fetches it again when the tag
  is put on.

### 11.2 Connecting a box to the library

Role `admin`, tab "System", section "Library":

1. Enter the address of the library, e.g. `https://nas:8866`.
2. The first time, enter the bootstrap password. It was set when the library was installed.
3. "Save", then switch the box off and on.

After that the section shows "connected", and the box appears on the page of the library. What the box had before
moves into the library; albums that exist there already are recognized and not created twice.
"Not confirmed yet" counts the changes that the library has not taken over yet. "This box" shows the name
of the box; "Rename" next to it gives it another one, e.g. "Nursery". The library shows it under this name.

**Unlinking:** "Unlink from the library" in the same section. The box keeps everything on its SD card – tags,
albums, stations, progress – and works on its own afterwards. The library is not asked; it may be switched off or
not exist any more. Before that the page says what is lost: changes that the library has not taken over yet never
reach it, and albums that are not completely on the SD card cannot be fetched any more. With "Cancel" you wait until
the box has delivered and fetched. The library keeps showing the box until it is removed there (tab "Boxes").
Linking again works like the first time, with the address and the bootstrap password.

### 11.3 The page of the library

The page of the library looks like the one of a box, without "Now" and "Bluetooth". It has users of its own: who
can log in to a box cannot do so there automatically; an admin of the library creates the users in its tab
"System". Albums, tags, stations and covers are managed there as in section 7.

In the tab "Albums" an album is marked "broken" if one of its MP3 files is damaged: because the library cannot read
the file or because a box found errors in it while playing. The album dialog names the files. If the file is
replaced, the mark disappears; a report of a box that says nothing about the file is removed there by an
administrator with "Discard reports".

Only the library has these tabs; its tab "Statistics" covers all boxes:

| Tab | Content |
| --- | --- |
| Boxes | all boxes with version and last contact. Rename, remove (e.g. after a lost SD card), take over the progress of an old box to a newly set up one. Albums that are being uploaded from a box. For administrators: change the bootstrap password, create a token for scripts, check the files of albums and cards. |
| Versions | the versions of the box software. Old versions that are not in the archive any more and that no box runs are left out, as long as "hide old versions that are not in the archive" is ticked; the page says how many. `use`: boxes install them by themselves. `manual`: by hand only. `dontuse`: every box leaves it at once. "Runs on" names the boxes a version runs on and the library itself, "Prepared on" the boxes that have fetched it already and try it the next time they are switched on. Below the table it is set how many versions the library keeps at most (5 at first); what a box still needs always stays. "Access to the releases" says where new versions come from; the token with which the library may fetch them is saved there. On GitHub a fine-grained token for this one repository with the right "Contents: Read-only" is enough. |
| Statistics | the same as the tab "Statistics" of a box (section 6.3). With several boxes "All boxes" or one of them is chosen at the top. "All boxes" sums per tag, radio station, skipped track and version and names for every tag on which box it played for how long; a table of the boxes shows listening time and when a tag was placed last, a click on a box chooses it. The days are never summed: every box has its own row with its own hours. |

### 11.4 Version of the box

A box that is set up for it fetches new versions from the library by itself and tries them the next time it is
switched on. If the new version does not work, the previous one runs again after a few seconds. Tab "System",
section "Version" (role `admin`):

| Button | Effect |
| --- | --- |
| Look for a new version | shows what is installed and what the library offers |
| Update now | fetches the newest version and restarts the box |
| Updates: off, by hand only, automatic | whether the box installs new versions by itself. "Off": it does not even look. On a box without a library "by hand only" is called "ask". |
| Back to the previous version | at once, even if something is playing. No box installs the version that was left by itself any more. |
| Start the emergency version | the version the box was set up with, or the one that was kept last |
| Keep as emergency version | the running, proven version becomes the emergency version |

If the LED changes between red, white, magenta and yellow, the library is too old for the version of the box. The
box plays nothing then. Remedy: update the library, "Back to the previous version" on the box, or unlink the box
from the library (section 11.2).

**A box without a library** fetches new versions itself: once a day it looks at GitHub whether there is a new
release. "Source" in the section "Version" says where the versions come from. As long as a box is linked to a
library, nothing comes from GitHub.

- The box does not install a new release at once, but after a waiting time of three days: if a correction follows
  in that time, it downloads only that one. Under "New" the section shows the version and the day on which it is
  installed by itself. "Install now" does not wait.
- The installation runs in the background; the new version starts the next time the box is switched on.
- With "Updates: ask" the box downloads nothing by itself. It shows administrators "Version … is available" at the
  top of every page until somebody answers: "Install", or "Skip" – then it asks again only for the next release.
  "What is new" shows what has changed. With "off" the box does not ask GitHub at all.
- With its request GitHub learns the address of the household and nothing else: no name, no version, nothing of
  what was listened to.

### 11.5 Scanning new tags with the phone

An Android phone with NFC can read a tag. This is how a new tag gets into the library without being put on a
box.

1. On the phone, open the page of the library in **Chrome** and log in.
2. Tab "Tag management", at the bottom at "Create a new tag": "Scan tag". The first time Chrome asks whether the page may
   use NFC.
3. Hold the tag to the back of the phone. The page shows the tag with its UID; a new one is marked "new" and
   is added to the library already.
4. As `admin`, enter the name right there, choose the album or the station. This is also
   possible later in the table above, on any device.
5. Simply hold further tags to the phone one after the other. "Stop scanning" ends it.

If the phone or its browser cannot read tags, the page says why when "Scan tag" is tapped. It does not work
on an iPhone.

If the page reports that Chrome does not allow it to use NFC, the question of Chrome was answered with "Block", or
NFC is switched off for websites in Chrome. To allow it: tap the symbol to the left of the address, then
"Permissions" and "NFC devices". If the entry is missing there: Chrome menu, "Settings", "Site settings",
"NFC devices" – the switch must be on, and the page of the library must not be listed under "Blocked".

- The boxes know the new tag shortly afterwards.
- Nothing changes by itself on other open pages; the new tag is in the tab "Tag management" as soon as it is opened.
- Every logged-in user may scan, only the role `admin` may link.
- The tag is only read. Nothing is ever written to a tag.
- Only Chrome on an Android phone can read. The button is missing on the PC. The phone must be unlocked and the
  page in the foreground.

### 11.6 Backups of the library

The library has the same card "Backup" in the tab "System" as a box (section 9.1). Its backup contains the
catalog, the progress and settings of all boxes, the statistics and the covers of the stations, and the albums if wanted.

In addition the library backs itself up, under "Back up regularly":

| Setting | Meaning |
| --- | --- |
| regularly | The library makes backups by itself. |
| every … days at … | how often and at what time of day |
| with MP3s | The albums are in the backup. Every backup then takes as much space again as all albums. |
| … backups are kept | Older backups are deleted. |
| Folder | where the backups are on the NAS, e.g. `/volume1/tinyjukebox/backups`. Empty: the given folder `backups` in the shared folder `tinyjukebox`. The library must be allowed to write to the folder. |

- "Save" takes over the settings. "Back up now" makes a backup in this folder at once, with MP3s if the box is
  ticked.
- The table below shows the backups in the folder. Each can be downloaded and deleted.
- Below the settings it says when the last backup was made and when the next one is due – or why the last one
  failed.
- If the folder is changed, the previous backups stay in the old folder.
