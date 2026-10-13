# Concept: backups – covers, statistics, restore, two schedules

Status: sections 1 (0.32.0) and 2 (0.33.0) are implemented, sections 3 and 4 are concept. Today's state:
[operations.md](../operations.md), sections 6 and 10 (`tinyjukebox.backup`, `tinyjukebox.library.backups`,
`BoxControl.backup`, `LibraryControl.backup`).

## 1. The covers of the albums move into the images folder

State: 0.28.0. A tag has no picture of its own any more, it shows the cover of its album or station (0.27.0); the
card images that tags had became covers of their albums. The images folder (`cards`, the name is older) holds the
covers of the stations and nothing else that is used ([operations.md](../operations.md), section 6).

**Cause of the missing covers, checked on the backup of the library of 2026-10-10 (without MP3s):** the cover of an
album is the file `cover.jpg` / `cover.png` in its album folder (`metadata.store_cover`) – set on the page, with
`tinyjukebox-import-covers` or adopted from a card image by 0.27.0. The album folders are only part of a backup "with
MP3s". The images folder, which every backup takes completely, holds only the covers of the stations.

**Change:** the cover of an album is kept in the images folder, the same way as the cover of a station.

- The image is named `album-<random>.<type>`; the album names it in its field `cover`, as a station does.
- Setting a cover (page, `PUT /api/albums/<id>/cover`, `set_album_cover`, `tinyjukebox-import-covers`) writes into the
  images folder.
- The images reach every box the way the covers of the stations do; the cover no longer travels as a file of the
  album.
- A backup needs no change: the images folder is in it, with or without MP3s. A restore needs none either.
- Moving what is there: at its first start with this version the library moves the stored cover (`cover.jpg` /
  `cover.png`, `metadata.has_stored_cover`) of every album folder into the images folder and sets `cover`. A box does
  the same for its albums if it works without a library; with a library it gets the images from it and removes its
  own stored covers. The library needs the version first, as with 0.27.0.
- Only the stored covers are moved. Other images in an album folder and images inside MP3s are not touched and not
  part of this; an album without `cover` shows what it shows today.

Implemented with 0.32.0 ([operations.md](../operations.md), section 6; [library.md](../library.md), section 4.8).
What turned out while implementing:

- The cover file counted as a file of the album (manifest, checksum). The library takes the file out of the manifest
  in the step that moves it, without computing a checksum of a track: every album with a stored cover has a new
  checksum once, and a box takes that over by removing its own stored cover – no MP3 is fetched again
  (`tests/sync/test_covers.py`).
- The name is `album-` and eight characters `0`–`9`, `a`–`f`. Not every `album-…` counts: the images folder of a
  library may still hold card images from before 0.27, and the images for `tinyjukebox-import-covers` are named
  `album-<id>.jpg`.
- The move runs at every start, not only at the first one: an album folder that is added later with a `cover.jpg`
  in it is moved at the next start.
- A box before 0.32 gets the catalog without the covers of the albums; otherwise it would fetch every cover of an
  album with every pass and drop the cover with every change of an album.
- WebP is taken for the cover of an album too, as for a station.

## 2. Statistics

The statistics are counted from the journal (`library.statistics`).

| | Today | Change |
| --- | --- | --- |
| Library | the journal is in the backup (`data/`, the copy made by SQLite) – the statistics of all boxes are saved | none |
| Box with a library | the library has the journal; the box asks it | none |
| Box without a library | the journal lines lie in the `sync` folder, which is never part of a backup – the statistics are lost | the journal lines go into the backup as `json/journal.jsonl` |

Only the journal lines are taken from `sync`, not the identity and not the outbox: a box set up again stays a new
box for the library. On restore the lines are only put back on a box that works without a library.

Implemented with 0.33.0 (`BoxControl.backup`, `tinyjukebox.backup.JOURNAL`); putting the lines back is part of
section 3.

## 3. Restore

Putting a backup back replaces the current data by the data of the archive. Only admins.

### 3.1 Where the backup comes from

| | Box | Library |
| --- | --- | --- |
| Upload on the page, tab "System", card "Backup" | yes | yes |
| A backup from the list of the backups folder, button "Restore" | – | yes |
| Command | `restore FILE` in the CLI | `tinyjukebox-backup … --restore FILE` |

A backup with MP3s is as large as all albums: the upload is written to disk as it arrives, not kept in memory.

### 3.2 Steps

1. Read the archive to its end (`verify`). Refused: cut off, the wrong kind (a box backup on the library and the
   other way round), a `format` newer than this program knows.
2. The page asks once: "This replaces all data by those of <date>. With MP3s: all albums are deleted and replaced."
3. Playback stops; the box takes no tag and the library no request of a box while it runs.
4. Unpack into new folders beside the current ones (`json.new`, `cards.new`, `albums.new`, `data.new`).
5. Swap: current folder → `.before`, new folder → current. Only now is anything of the current data touched; a
   failure before this step leaves everything as it was.
6. Load the data anew (the program restarts itself, like after an update).
7. Remove the `.before` folders once the program runs again with the restored data.

With MP3s the albums folder needs the space of all albums twice for a moment (step 4). If the space is not there,
the restore is refused before anything is changed and says how much is missing.

### 3.3 What is not replaced

- Nothing secret is in a backup, so nothing secret is replaced: certificate, users, passwords, tokens and in the
  library `settings.json` stay as they are.
- The identity of a box and what it exchanges with the library (`identity`, `sync`) stay.
- The schedules and the folders of the backups stay (they are in `data/` of a library backup, but a restore must not
  change where backups go).

### 3.4 A backup without MP3s

Only the data is replaced, the albums folder stays. Afterwards the data can name an album whose folder is not
there. Accepted. For every such album one line is written to the log:

`ERROR restore: album '<title>' (<id>) is in the data, but its folder '<album-folder>' is missing.`

The page says after the restore how many albums are missing and names them. Album folders that are there but not in
the data stay untouched.

### 3.5 Box and library after a restore

- **Library:** the boxes hold a newer state than the restored one. The library is the record: the boxes take over
  the restored catalog at the next synchronization. Albums the library no longer has are fetched from nowhere –
  same case as 3.4.
- **Box with a library:** the library wins at the next synchronization, so a restore on such a box only lasts until
  then. The page says so before the restore. A restore on a box is meant for a box without a library.

## 4. Two schedules

Today there is one schedule with a tick "with MP3s". New: two schedules that are set apart from each other.

| Setting | Without MP3s | With MP3s |
| --- | --- | --- |
| regularly | on / off | on / off |
| every … at … | number and unit: days, weeks, months; time of day | the same |
| … backups are kept | 1–999 | 1–999 |
| folder | own folder, empty: the one of the config | own folder, empty: the one of the config |

The example: without MP3s every week, 52 kept; with MP3s every month, 2 kept (the current and the one before).

- "… are kept" counts only the backups of its own kind (the file name says which: `….tar.gz` or `…-albums.tar`),
  also when both write into the same folder.
- "Months": the same day of the month as the last backup; a day that the month does not have is its last day.
- If both are due at the same time, the one without MP3s is made first.
- "Back up now" stays, with the choice with or without MP3s; the backup goes into the folder of its kind and counts
  for its number.
- The page lists the backups of both folders in one table, with a column for the kind.
- `tinyjukebox-backup --library-folder` gets a second option for the folder of the backups with MP3s.
- The settings that exist are taken over: the schedule of today becomes the one of its kind (with or without MP3s),
  the other one is off with the defaults.

REST: `/api/library/backups` returns and takes `{"plain": {…}, "albums": {…}}` in place of the flat fields.

### 4.1 A box without a library

Today only the library makes backups by itself. New: a box that works without a library has the same two schedules,
on its page, tab "System", card "Backup", with "Back up now" and the table of the backups.

- Folder: `backups-folder` of the config, else `backups` beside the albums folder; each schedule can name its own.
- While the box works with a library the section is not shown and no schedule runs – the library has the data and
  makes the backups. The settings are kept and count again if the box is detached.
- The code of `tinyjukebox.library.backups` is used for both; it moves out of the library package.
- REST of the box: `/api/backups`, the same fields as `/api/library/backups`.

## 5. Order of the work

1. Covers into the images folder (section 1) – a step of its own, it changes the synchronization.
2. Journal lines of a box (section 2) – small, format stays 1.
3. Two schedules (section 4).
4. Restore (section 3).

Each step: version bump, CHANGELOG, operations.md section 10, rest-api.md, user manual (de, en), page texts.
Section 10.3 of operations.md (restore by hand) stays as the way for another NAS.

## 6. Decided (2026-10-10)

1. Restore on the box: yes, by upload. The page warns that a box with a library gets the state of the library again.
2. Schedules on the box: yes, while it works without a library (section 4.1).
3. No backup is made automatically before a restore. The page reminds to make one.
