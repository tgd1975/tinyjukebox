# Concept: Spotify advent calendar figures

Status: concept, ready to be implemented, nothing built. Written on 2026-10-17, brought up to date against 0.48.2
on 2026-11-08; decisions D1 to D4 taken by the owner on 2026-11-08, D5 to D13 follow from the code (section 2). The
plan is section 8.
Related today: entries of Spotify and tags ([library.md](../library.md), sections 4.2, 4.8 and 4.10), `PlaySpotify`
and Soloist ([architecture.md](../architecture.md), sections 2.5 and 4.1), the app ([app.md](../app.md)).

## 1. Idea

A family has a Spotify advent calendar – for example 24 chapters of an audio story – and 24 figures, one per day,
each with a tag. A figure placed on the box plays its calendar. The figure is a normal tag: only its UID counts,
nothing is written to it.

There are two kinds of calendar on Spotify, and both are supported:

- **Chapters:** one album (or playlist) that holds all 24 chapters from the start. Figure n plays chapter n.
- **Growing:** one playlist to which Spotify adds the next chapter every day. Every figure continues where the last
  one stopped; a chapter added since then plays next, without the old ones again.

The figure for day n plays only from December n on, unless the calendar is set otherwise.

## 2. Decisions

Taken on 2026-11-08:

| | Question | Decided |
| --- | --- | --- |
| D1 | Date lock | a switch per calendar, "only from its day", on by default; off, every figure plays at once (a calendar of last year) |
| D2 | Which days | 1–24 December of the calendar's year, fixed |
| D3 | Growing calendar, everything heard, no new chapter yet | the newest chapter plays again from its beginning |
| D4 | The phone as reader | the app plays calendar figures with the same rules |
| D5 | What a calendar is | a new kind of entry, `calendar`, beside album, station and entry of Spotify; a figure is a tag linked to the calendar with its day – not 24 entries |
| D6 | Chapters: what a figure plays | the n-th track of the source, and only that one: at its end the figure is finished (it never runs into the next, maybe locked, day). Its position is its own, per tag as today; finished, it starts its chapter from the beginning |
| D7 | Growing: whose position | one position per calendar on each box and each phone, shared by all its figures; it plays on through all chapters there are and is finished after the last |
| D8 | Growing: how a new chapter is found | only from what Spotify plays, never from the date: the player goes to the saved last chapter and looks at what follows it. A track of the playlist itself is the new chapter; a track Spotify adds (`source: autoplay`) or none means there is none (D3). No Web API of Spotify is needed |
| D9 | A locked figure on the box | nothing plays, LED yellow blinking (new state `LOCKED_DAY`); taken off, the box waits as usual. The page shows the tag, its day and "opens on 17 December" – never the title of the chapter |
| D10 | Which boxes get a calendar | every box, like every entry: tags are global. No publishing to chosen boxes, no import format: the page links the figures one by one, as tags are linked today |
| D11 | The saved chapter is gone from the growing playlist | as for every entry of Spotify: looked for in the queue, and if it is not there, from the beginning |
| D12 | Which links a calendar takes | album and playlist of Spotify. A podcast is not offered: Spotify lists its episodes newest first |
| D13 | Interface | version 3: a box of version 2 would refuse a `targets.json` with a `calendar` (as version 1 refused the entries of Spotify, [library.md](../library.md), section 4.10) |

## 3. The calendar and its figures

**The entry** in `targets.json`:

```json
{"target-id": "31", "target-type": "calendar", "name": "Die drei ??? Adventskalender 2026",
 "uri": "spotify:album:…", "mode": "chapters", "year": 2026, "locked": true, "created": "…", "cover": "spotify-…"}
```

`mode` is `chapters` or `growing`, `uri` an album or a playlist (D12), `locked` the switch of D1. The cover is
fetched as for an entry of Spotify.

**A figure** in `tags.json` links to the calendar with its day: `"target": {"type": "calendar", "id": "31",
"day": 17}`, `day` 1–24. A tag links to one target only, so a figure cannot be in two calendars or on two days. Two
figures on the same day are allowed (two children, one calendar); the page notes it.

**Positions** (`activity.json`, per box):

- Chapters: per tag UID as today, with `target-type: calendar` and `track-uri`.
- Growing: one activity of the calendar under the key `calendar:<target-id>` – it cannot clash with a UID. Every
  figure of the calendar reads and writes it.

**The open days** are kept on each box and phone in a small file of its own (`calendar-days.json`: target ID →
highest day opened), not synchronized: it only makes sure an open day stays open (section 5).

## 4. Playing on the box

`CHECK_TAG` finds the calendar of the tag. If the day is locked → `LOCKED_DAY` (D9). Otherwise `PLAY_ALBUM` starts
`PlaySpotify` with the source of the calendar and a start rule; buttons, headset, pause, "the phone has no influence",
Soloist restarts and the journal stay as for an entry of Spotify ([architecture.md](../architecture.md), sections 2.5
and 4.1).

**Chapters (D6).** The first time, the player starts at track n (skipping n − 1 tracks silently, as for a saved
position), later at the saved track and second of the tag. When the track after it begins, the player pauses and
reports `FINISHED` (LED blue, blinking) – as at the end of an entry. "Next" and "back" stay within the chapter:
"next" ends it (finished), "back" goes to its beginning. Finished, the next placing starts the chapter from its
beginning; "back" held does so at once.

**Growing (D7, D8, D3).** The player starts at the saved track and second of the calendar and plays on through the
playlist; after the last chapter it is finished, as today. Placed while finished:

1. The player goes to the saved last chapter, silently (as for a saved position, including the search in the queue
   if the playlist was changed, D11).
2. It looks at the queue (`queue --json`): if the next entry is a track of the playlist (not `source: autoplay`),
   that is the new chapter – it starts there, from its beginning.
3. If none follows, it plays the saved chapter again from its beginning (D3), and is finished at its end.

"Back" held plays the whole playlist from the beginning; the shared position moves with it.

**`LOCKED_DAY`.** LED yellow, blinking. Nothing plays, Soloist is not started. The state is left like `UNMAPPED_TAG`
(`NO_TAG`, `NEW_TAG`); while in it the box looks at the date once a minute, so a figure left on the box past
midnight starts by itself when its day opens.

What still holds: Spotify needs the key, the player, the login and the network of the box; a calendar does not make
anything available offline.

## 5. The date

- Day n opens at 00:00 on n December of the calendar's year, in the time zone of the box (set by the playbook,
  [box.md](../box.md)) or of the phone. Once open, a day stays open – also after December and in later years.
- `locked: false`: every day is open.
- The box has no real-time clock. Without network it starts with an older time (the one it last knew), so it may
  open a day late, never early. A day it opened once stays open through `calendar-days.json`, even if its clock is
  behind after a power cut.
- The date alone decides whether a figure may play; what Spotify has decides what it plays (D8). A day is never
  opened because Spotify added a chapter, and no chapter is assumed because the date changed.

## 6. The page

- **Tab "Spotify", section "Advent calendars"** (`admin`): "New calendar" with name, link (album or playlist),
  kind ("Chapters: all 24 are there" or "Growing: one more every day"), year (this year by default), "Only from its
  day" (on). Below it each calendar with its cover and a table of the 24 days: day, figure (name and UID of the tag),
  "open" or "opens on …". Days without a figure and days with two are marked; nothing blocks the calendar from
  playing.
- **Tab "Tag management":** a calendar is offered as a target like an album; chosen, a field "Day" (1–24) appears.
  Scanning with a phone ([user manual](../../user/en/manual.md), section 13.5) works for figures as for every tag.
- **What is playing:** the name of the calendar, "Day 17" and the title of the chapter, as for an entry of Spotify;
  for a locked figure the tag, its day and "opens on 17 December" – no title. Account data of Spotify is never part of
  a calendar.
- Texts only in `web/web/texts/de.json` and `en.json`; light and dark theme checked.

## 7. The phone

The app follows the same rules (D4), with the Spotify app and Spotify's App Remote SDK as it plays entries of Spotify
today ([app.md](../app.md)):

- Lock by the date of the phone; locked: the band of colour as `LOCKED_DAY`, the text "opens on 17 December".
- Chapters: the n-th track of the source (`skipToIndex`), finished when the next track begins.
- Growing: the shared position of the calendar on this phone. Placed while finished: to the saved chapter, one
  `skipNext`; if Spotify plays what it added itself (autoplay, which the app recognises already), back to the saved
  chapter from its beginning (D3), otherwise the new chapter.
- Without the Spotify app or its registration the figure says, as every entry of Spotify: "This tag plays on a box".

## 8. Plan

| Step | What | Done when |
| --- | --- | --- |
| A-1 (data and page) | entry `calendar`, link with `day`, schemas and sanity check, interface version 3 in library, box and app; tab "Spotify" and "Tag management" (section 6); until A-2 a box shows a calendar figure as not playable (yellow) | a calendar created on the page of the library is on the box, its figures linked; tests of the formats, of the sync and of a box of version 2 against a library of version 3 |
| A-2 (chapters and date on the box) | `LOCKED_DAY`, `calendar-days.json`, the start rule and the end of a chapter (sections 4 and 5, D6) | tests with the stand-in of Soloist and a `ManualClock`: day n plays track n and ends there; a locked day does not play and opens at midnight while the figure lies on the box; an open day stays open with the clock set back |
| A-3 (growing on the box) | the shared position, the new chapter from the queue, D3 | tests with the stand-in: a finished playlist gets a track appended → the next placing plays it; nothing appended → the last chapter again; an autoplay track is never taken for a chapter; two figures share the position, two calendars do not |
| A-4 (the app) | section 7 | tests of the app with a stand-in of App Remote, as for entries of Spotify |
| A-5 (documents) | architecture.md, library.md, rest-api.md, app.md, the manuals (de, en); the trial (section 9) into roadmap.md; this concept is deleted | – |

Each of A-1 to A-4 is a version of its own, its documents with it; A-5 completes them with the last one.

## 9. To be tried

On the box, with the account of the box and a phone (after A-2 and A-3):

| Item | Expected | If not |
| --- | --- | --- |
| An album of 24 chapters, the figure of day 24 | plays chapter 24; the time until the sound is noted (23 tracks skipped) | above 15 s: Soloist through its WebSocket instead of single `soloist ctl` calls ([roadmap.md](../roadmap.md), section 1) |
| A playlist of your own with three tracks played to its end, then a fourth added in the Spotify app, the figure placed again | the fourth plays, not the first three | Soloist does not show the added track: step 2 of section 4 is decided anew |
| The same without a fourth track | the third plays again from its beginning | – |
| The figure of a later day | LED yellow, blinking; nothing plays | – |
| The box without WLAN after a power cut, the figure of yesterday | plays once the WLAN is back (Spotify needs it); its day stays open | – |
| The same figures on the phone with the Spotify app (after A-4) | as on the box | – |
