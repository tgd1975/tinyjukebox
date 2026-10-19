# Concept: central diagnostics and radio startup

Status: concept, not implemented. Written on 2026-10-17. Related: the box log and service
([operations.md](../operations.md), section 3), the box/library journal ([library.md](../library.md), section 7), the
box/library interface ([library.md](../library.md), sections 4.10 and 4.13), the radio pipeline
([architecture.md](../architecture.md), sections 2.3 and 9.3), and slots, recovery mode and tolerant data loading
([recovery-and-data-tolerance.md](recovery-and-data-tolerance.md)).

## 1. Why

There are two related operational problems:

- Technical logs live on each box. The file rotates, and the web log buffer holds only the latest 5,000 records in
  memory. If the box is unavailable or the useful lines have rotated out, diagnosis is difficult. The restart loops of
  2026-10-17 and 2026-10-18 ([recovery-and-data-tolerance.md](recovery-and-data-tolerance.md), section 1) could
  only be diagnosed over SSH.
- Radio playback can hang or stutter near the beginning, and the first sound can take a long time after a tag is
  placed. The existing latency measurement ends when ffmpeg starts, which is not the same as audible sound.

The library should retain enough diagnostic data to investigate a box without having that box in front of us. Radio
startup also needs measurements that locate the delay and initial dropouts before implementation chooses a fix.

## 2. Existing data and boundaries

The box currently has three different kinds of operational data:

| Data | Current handling | Meaning |
| --- | --- | --- |
| Application log | Rotating file on the box; latest 5,000 records in the in-memory web log buffer | Component messages, warnings, errors and tracebacks |
| Journal | Append-only file on the box, transferred to the library and stored in SQLite | Listening activity, playback outcomes, box starts, version switches and selected conflicts |
| Library log | Local to the library process | The library's own operation |

The play journal is an exact listening record and is not a replacement for technical logs. Keep diagnostic logs in a
separate store and UI. Do not copy journal contents into logs or expose either data set to GitHub. Central log access
is for administrators; ordinary users should not gain access to other boxes' diagnostics.

The library API remains authenticated and versioned as today. Log collection is one-way ingestion; it must not become
a remote command channel, and it must not require the library for playback.

## 3. Central diagnostic records

Prefer bounded, structured diagnostic records over blindly uploading arbitrary log text. A record should identify:

- box, software version, slot and boot/session;
- event time, monotonic time since boot, severity and component;
- a stable event code plus safe parameters;
- an exception class and a sanitized traceback when needed.

Existing logger records may be forwarded after redaction, but the implementation must exclude passwords, API keys,
tokens, authorization headers, certificate material and other credentials. Avoid collecting album/track titles, tag
UIDs, full Spotify URIs and query-bearing stream URLs unless a specific diagnostic requires them and the data is
explicitly treated as sensitive. A station ID and a redacted host are usually enough to correlate radio failures.

Collection must be asynchronous and bounded. Logging must never wait for DNS, TLS, the library, SQLite or an HTTP
response on a playback, state-machine, RFID or button thread. Batch records over the existing authenticated box
interface; retry later when offline, with deduplication by box and sequence number. Keep local logs as the immediate
source of truth while offline. Under storage pressure, discard low-priority DEBUG/INFO records first, count the loss,
and preserve a bounded tail of WARNING/ERROR records. A failure of the logger or uploader must not stop the box.

The library should store diagnostics separately from the listening journal, with per-box retention and size limits.
Its admin view should filter by box, time, severity, component, software version and event code, and allow copying or
exporting a bounded result. Retention duration and the default disk budget are implementation decisions; unbounded
log growth is not acceptable.

## 4. A box that cannot start

The uploader of section 3 runs inside the box application. A box whose application fails during startup and is
restarted by systemd, or a box in recovery mode, therefore sends nothing, although these are the cases in which
central diagnostics help most. Recovery mode deliberately speaks only the stable update interface and loads none of
the application's data ([recovery-and-data-tolerance.md](recovery-and-data-tolerance.md), section 3).

Such a box sends one short startup-failure report to the library (decided 2026-10-19). The report holds the box,
software version, slot, the number of failed starts and the last startup error (exception class and sanitized
traceback, redacted as in section 3). It is sent by a small sender that does not load the application's
configuration, catalog or journal, over an endpoint that stays as stable as the update endpoints; recovery mode
allows this one request besides updates. Each report replaces the previous one for that box, so a restart loop does
not fill the library. The administrator sees in the library why a box is down and since when.

## 5. Radio startup and stutter

The symptom to solve is an observed one: the beginning of radio playback may stutter, and audible playback may start
long after the tag is placed. The cause is not established. Do not assume that reducing `buffer-time` is the fix: a
smaller ALSA buffer can reduce startup delay but tolerate fewer decoder/network stalls.

The current pipeline has several distinct phases that must be measured separately:

1. Tag recognized and radio state enters `connecting`.
2. An M3U/PLS playlist is fetched and resolved. The current timeout is up to 10 seconds; direct stream URLs skip this
   step. HLS playlists are handed to ffmpeg.
3. ffmpeg and `aplay` are started. The current `RadioPlayer` reports `playing` and ends the tag reaction timer when
   ffmpeg has started.
4. ffmpeg produces PCM and `aplay` fills its ALSA buffer. The default ALSA buffer is commonly about 500 ms; sound is
   heard only after the buffer is filled.
5. The first audible sound and any dropouts/stutters follow.

The current `playing` state therefore does not prove that sound has started. Instrument the phase boundaries with a
monotonic clock and a playback/session ID. At minimum record playlist resolution duration/result, pipeline process
start, first PCM/write readiness if measurable, estimated or measured first audible output, initial dropout count and
duration, and retry reason/delay. Distinguish stream connection failures from decoder, ALSA and output-device
failures. Do not include the full stream URL in central diagnostics.

## 6. Investigation and fix criteria

First collect measurements from real stations and a controlled stream, then determine which phase accounts for the
delay and stutter. The fix may involve playlist resolution, connection/probe behavior, buffering, decoder readiness or
output startup; the logs must make those alternatives distinguishable.

Acceptance criteria:

1. From a tag placement, the system can report separate durations for resolution, pipeline startup and first audio;
   `PLAYING` alone is not recorded as audible playback.
2. Repeated initial dropouts are counted and attributed to the phase/component that detected them, without generating
   one unbounded log line per audio frame.
3. Measure a representative set of direct streams, M3U/PLS stations and HLS streams on the box hardware and network.
   Agree a startup target and acceptable initial-stutter rate from those measurements before tuning buffer defaults.
4. A box can start and play when the library is offline, the log uploader is unavailable, or the central log store is
   full. These conditions may lose bounded diagnostic detail but must not block audio.
5. An administrator can inspect the relevant records from the library after the box has gone offline, subject to the
   configured retention period. A box in a restart loop or in recovery mode shows its last startup error there.
6. Tests verify credential redaction, bounded buffering, retry/deduplication, per-box authorization and that no log
   network operation runs on playback-critical threads.

## 7. Non-goals

- Central logs do not promise remote control of a box.
- Logs do not replace the structured journal or the existing library statistics.
- A log record that says ffmpeg started does not prove that the listener heard audio.
- This concept does not promise zero latency or uninterrupted radio for every station and network. It requires enough
  evidence to diagnose and reduce the startup delay and initial stutters on supported setups.