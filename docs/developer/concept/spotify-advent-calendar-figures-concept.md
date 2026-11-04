# Concept: Spotify advent calendar figures

Status: concept, not implemented. Written on 2026-10-17. Related: Spotify entries and tags
([library.md](../library.md), sections 4.8 and 4.13; [architecture.md](../architecture.md), section 4.1), and the
Spotify player ([architecture.md](../architecture.md), section 4.1).

## 1. Idea

A physical figure from a Spotify advent calendar selects its calendar's audio when it is placed on the box. For
example, a family could use figures for a growing Spotify advent-calendar playlist with audio stories such as
"Die drei Rufzeichen". The figure is passive: its RFID tag identifies it; Tiny Jukebox does not write to the figure.

the newly available chapter. It must not treat a persistent `finished` flag as proof that the playlist can never
grow again, and it must not restart the old chapters just to reach the new one.
The concept supports two kinds of calendar content: a fixed special album/collection with 24 titles, and a Spotify
calendar source that grows over time. A playlist exhausted yesterday is not necessarily finished today: Spotify may
have appended the next chapter. In that case, the box continues from the saved end into the new chapter rather than
replaying the old ones.

## 2. Calendar and figures

A calendar has a name, season/year, timezone and a content mode. Its physical figures are entries into that calendar,
not copies of each track. A figure entry may carry:

- a day number (1–24), release date and optional character/display name;
- the UID of its physical figure;
- the calendar/source it selects;
- for fixed content, the assigned chapter in the 24-title collection.

| Mode | Source | Figure behavior |
| --- | --- | --- |
| Fixed 24-title collection | A local album, Spotify album/playlist, or 24 stable track/episode URIs | Each figure/day selects its assigned title. On first play it starts at that title; later it resumes its own saved position. |
| Growing Spotify calendar | One Spotify playlist/source URI that gains chapters over time | Figures for the same calendar select the shared source and share its saved progress. A newly appended chapter continues after the last consumed chapter. |

For fixed content, do not rely on a Spotify playlist index as a durable identity. Prefer the track/episode URI, or a
stable local track ID for a local album. If the source only exposes an ordered playlist, the setup must validate the
24-title mapping and the behavior after reordering.

For the growing-playlist mode, the 24 figure entries may be alternative keys for one calendar source; they do not
imply 24 separate playlist copies or 24 fixed track positions. The playlist URI stays the source identity while its
chapter list changes.

An admin creates a calendar in the library, scans each figure as a tag, assigns its day/source, reviews the
assignments and publishes the calendar to selected boxes. Reuse normal tag identity and Spotify support where
practical; do not require a new RFID format or a Spotify account per figure. The current Spotify support accepts
playlist, track and episode URIs.

## 3. Progress and completion

Progress depends on the content mode:

- **Fixed 24-title collection:** progress is per figure/day. Each figure starts at its assigned chapter the first
  time and then resumes its own saved position. Figures for different days do not overwrite each other's positions.
- **Growing Spotify calendar:** progress belongs to the calendar source, not to a physical figure. Placing a
  different figure for the same calendar continues at the same saved chapter/position. Different calendars have
  independent progress.

The current activity stores a track URI, position and a boolean `finished` per tag UID. The fixed mode can use that
per-figure position. The growing mode needs shared, content-aware completion instead:

- Save the last consumed track URI and position, plus enough information to identify the content state at which the
  playlist was considered finished (for example, the last track URI or a provider revision when available).
- When playback is requested again, refresh/read the current playlist state. If it now contains an unplayed chapter
  after the saved position, clear completion and continue at that chapter.
- Do not infer that new content exists merely because the calendar date changed. The Spotify source must actually
  expose a new playable item.
- Do not use a playlist index as the durable checkpoint. Playlist ordering can change; use track URIs and reconcile
  the saved URI against the current playlist/queue.
- If Spotify cannot provide enough state to determine whether the playlist grew or where the saved track moved,
  report that limitation rather than silently restarting the old playlist or claiming that new content was played.

This behavior must be verified against the Spotify player interface actually used by the box; a playlist URI alone
does not guarantee that the player API exposes a complete, current track listing.

## 4. Date and availability

- Day `n` becomes available on its configured date, normally December `n` of the calendar year. The timezone comes
  from the calendar settings in the library.
- Once a day opens, it remains available for replay; a figure does not relock the next morning. This keeps the
  calendar usable after a missed day and after December.
- A future figure does not start playback. The box gives the normal not-available feedback and, when connected to a
  page, may show the opening date. A child's surface should not reveal the future chapter title unless the family
  chooses that behavior.
- The latest unlocked day is persisted locally. Temporary network loss or a later clock correction must not relock
  an already-open day.
- The box has no reliable real-time clock while offline. If it has no trusted date for an unopened day, it must not
  unlock future content by guessing. Already-open days continue to work; the library can correct the date when the
  box reconnects.

An admin may configure a calendar without date gating for evergreen use, but the normal advent-calendar setup gates
items by date.

## 5. Setup and everyday use

**Setup:** create the calendar; choose the mode, season/year and timezone; provide the fixed 24-title collection or
the growing Spotify source; scan each figure; assign its day and chapter/source; review the assignments; then publish
the calendar to selected boxes. The setup page must show missing days, duplicate figure UIDs, invalid chapter
references and Spotify sources that cannot be resolved before publication.

**At home:** place a figure on the box. If its day is open, fixed mode plays that figure's assigned chapter from its
own saved position. Growing mode plays the shared calendar source from its saved position; a new chapter appended to
the source is played next without replaying already-consumed chapters. Removing the figure stops playback. In growing
mode, another figure for the same calendar resumes the shared position. A locked or unassigned figure never falls
through to an unrelated album or Spotify item.

**On the page:** show the calendar name, day, assigned figure/tag and source to an admin. Children see only the
feedback needed for the interaction; account credentials and raw Spotify tokens are never part of calendar data.

The source still requires the existing Spotify prerequisites of the box: a supported player, a connected account and
network access. A calendar assignment does not make Spotify content available offline and does not bypass Spotify's
availability or subscription rules.

## 6. Data and synchronization

The library remains the source of truth for the calendar and figure assignments. Boxes receive a published calendar
through the existing versioned sync interface and cache the complete mapping with their other catalog data. The
physical figure remains a normal tag; its calendar assignment and release date are additional metadata associated
with its UID.

Calendar publishing must be atomic from a box's point of view: it must not receive half of a figure-to-source mapping
and play the wrong item. A box that is offline keeps its last complete calendar. It reports that a new calendar
revision is pending until the complete revision is available.

This feature must obey the rules for damaged and unknown data
([library.md, section 4.21](../library.md#421-damaged-and-unknown-data)): an older or partially updated box must not
become unavailable because it sees calendar fields it cannot use. Raise the API interface
version if an older counterpart cannot safely tolerate the published representation. This does not promise that old
firmware can play every new calendar feature.

## 7. Open decisions

1. Is date gating mandatory for both modes, or may the fixed 24-title collection be evergreen by default?
2. Is the calendar fixed to December 1–24, or may an admin choose another 24-day schedule?
3. Does a locked figure show only its day number, or may it show the character name?
4. What should happen if Spotify removes a chapter or moves the saved track out of the growing playlist?
5. Should the page show the complete 24-day calendar, or only the figures known to a given box?
6. Is a bulk-import format needed for the calendar source, 24 chapter mappings and scanned figure UIDs?

## 8. Acceptance tests

1. An admin can create either a fixed 24-title collection or a growing Spotify calendar, assign figures and publish
  the complete mapping to a box.
2. In fixed mode, every figure starts at its assigned title and keeps an independent saved position.
3. In growing mode, figures for the same calendar share progress; independent calendars do not share it.
4. When a growing Spotify playlist is exhausted, completion is associated with the current content state. After a
  new chapter is appended, the next play continues into it without replaying old chapters.
5. A playlist reorder or rename does not make the saved position jump to an unrelated chapter; reconciliation uses
  stable URIs, not only numeric indexes.
6. A future-day figure does not play early; an already-open day remains available offline and after a clock
  correction.
7. A partial sync, duplicate UID, missing mapping or unavailable Spotify source never maps a figure to unrelated
  audio.
8. Tests verify both modes and the growing-playlist completion transition with the real Spotify-player protocol or
  a faithful fake; they do not treat a playlist's `finished` state as permanent across playlist updates.