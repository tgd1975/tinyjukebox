# Concept: box recovery and tolerant data loading

Status: concept, not implemented. Written on 2026-10-17 after failures of a box running 0.39.0 and 0.39.1; updated on
2026-10-18 after the journal failed a second time. The API compatibility contract is in [library.md](../library.md),
sections 4.10 and 4.15; slots and recovery are in sections 6.3–6.8 and 9.5. How a box that cannot start reports
its failure to the library is in [central-diagnostics-and-radio-startup.md](central-diagnostics-and-radio-startup.md),
section 4.

## 1. What happened

Two independent failures exposed the same weakness: a local data error could prevent the box application from
starting. Both were diagnosed only over SSH, because the box sent nothing to the library while it restarted.

1. **Rollback to an application that could not read current data.** During the probation of 0.39.0, the Spotify test
   endpoint read the HTTP request body twice; the second read raised `ConnectionResetError` and the health check
   abandoned 0.39.0 (fixed in 0.39.1). The slot manager switched to the fallback 0.30.0, which could not load the
   box's current `targets.json` or its backup: its schema rejected the newer `cover` field and the `spotify` target
   type. The `oneOf` validation error misleadingly said that `album` was not one of `radio`. The application exited
   during initialization, systemd retried it, and neither the page nor the green LED became available. The rollback
   selected a fallback but did not restore a working box.
2. **Damaged end of the sync journal, twice.** On 2026-10-17 and again on 2026-10-18, the confirmed 0.39.1 entered a
   systemd restart loop while loading the local sync journal. Each time only the last line was invalid (on
   2026-10-17 it consisted of 405 NUL bytes), and the records before it were valid. Sequence numbers were missing on
   both sides of the valid records: between the library's confirmed cursor and the first valid record, and between
   the last valid record and the last number reserved in `numbers.json`. The missing numbers kept the library from
   confirming later records.

Each journal was repaired by hand the same way: keep every valid record, insert an explicit `recovery_gap` record for
each missing number, and rewrite the journal as valid JSONL with a contiguous sequence; the next local number stayed
unchanged. No listening events were invented; the contents of the lost events could not be reconstructed. After the
first repair the library confirmed the markers; after the second one this was not checked.

## 2. Requirements

- A failure in a catalog entry, cached file or journal record must not make the whole box unavailable when the rest of
  the box can still work.
- The box must report degraded or skipped data clearly in its log and status. It must not silently claim that every
  library feature is available.
- Valid local data must be retained. An old box must not erase fields or target types it does not understand when it
  writes its own state.
- The library remains the source of truth. The box consumes the catalog the library provides; it does not create a
  second, authoritative catalog to make rollback possible.
- The library API continues to gate requests by the interface version reported by the running box. It does not need
  to know which slot is the fallback.
- A crash or power loss while the journal is written must not leave a damaged line or unexplained missing numbers.
  The same damage occurred twice within two days, so tolerating it on reading (section 4) is not enough.

## 3. Slots and recovery

The two application slots and the fallback slot have different jobs.

| Slot | Job | May start the box application? |
| --- | --- | --- |
| `a` and `b` | Normal operation, probation, and rollback to the last confirmed application version | Yes |
| `fallback` | Recovery only: fetch and install a signed, compatible firmware version | No |

The fallback flag must keep the application from starting. In particular, fallback must not load the YAML
configuration, catalog, sync outbox or journal. Its only path to the library is the stable update interface: ask for
available versions, fetch the description and signed slot, verify it, install it into `a`, then restart into `a`.
Non-update requests remain unavailable in recovery mode; the one exception under consideration is a short report of
why the box is in recovery ([central-diagnostics-and-radio-startup.md](central-diagnostics-and-radio-startup.md),
section 4). If no suitable update can be installed, the box stays in a clear recovery state and requires
administrator intervention instead of restarting an unusable application forever.

The versioned API contract remains independent of this slot policy. The library permits requests supported by the
running application's interface and keeps the documented update endpoints available to an incompatible box. The
fallback itself never runs that application interface.

## 4. Tolerant data loading

Being able to boot an old application against data from the library does not mean it can implement every newer
feature. For example, if 0.30.0 is the other application slot (`a` or `b`) when a newer version fails its probation,
it cannot play a Spotify target. It should still be able to start and play compatible local albums and stations
instead of failing because the catalog contains one Spotify entry.

The data layer should degrade at the smallest useful scope:

- Validate the document structure, but isolate invalid records. A missing required field makes that record
  unavailable; it should not invalidate every other record in the file.
- Accept unknown optional fields and target types when reading. Preserve them as opaque data, or avoid writing the
  library-owned catalog from an old box, so an older version cannot delete data it does not understand.
- A malformed JSONL tail must not prevent loading its valid prefix. Keep the valid records, identify the lost sequence
  numbers, and report them explicitly. Do not invent playback events to fill a gap; use a recovery marker that
  statistics can ignore and that lets the library advance its contiguous journal cursor.
- The journal writer prevents that damage in the first place. The likely cause of the NUL-filled line is a crash or
  power loss after the file had grown but before its data reached the disk. A record is written as one complete line
  and flushed to disk before it counts as written. A sequence number that was reserved but never written is detected
  at the next start and recorded as a `recovery_gap`, instead of being left as a hole the library cannot pass.
- If both a data file and its normal backup are unusable, the application should enter a degraded mode with the
  unaffected data rather than fail during construction. Recovery must never overwrite the only valid copy
  automatically.
- Keep strict checks for data that the current operation actually needs. Tolerance means isolating unsupported or
  damaged data, not pretending it is valid or silently using incorrect values.

The box guarantees only that it can operate with the compatible subset of its data and recover/update itself. It does
not guarantee that old firmware can use new library features, play unknown target types, or synchronize every new
field.

## 5. API and data compatibility

The interface version answers whether the running box and library can exchange requests. It does not by itself prove
that an older application can parse every newer cached record after a rollback. Keep these contracts distinct:

- The library API gate remains based on the interface version spoken by the running application. No special
  fallback-slot check is added to the library.
- Data readers must tolerate additive fields and isolate unknown record types so that a supported subset can still
  run.
- A versioned data-format change that an older application cannot safely read must be staged or represented in a
  backward-compatible form until the new application is confirmed. It must not turn a routine rollback into a boot
  loop.
- The application must not rewrite the library's canonical data merely because it loaded a compatible subset.

## 6. Acceptance tests

1. A confirmed `a`/`b` application fails during probation; the other confirmed slot starts against the current
   library catalog and remains useful for the data types it understands.
2. The fallback flag starts recovery mode without importing the application, configuration, catalog, outbox or
   journal modules. It can fetch and verify a signed update through the stable update endpoints; it cannot serve the
   ordinary box API or page.
3. A catalog with `cover` fields and a Spotify target loads in an older application without invalidating its albums
   and radio stations. Unknown data remains intact after that application writes its own state.
4. One malformed target record is reported and skipped while valid records remain available.
5. A journal with a NUL-filled final line retains every valid entry, reports the missing sequence numbers, emits
   explicit recovery markers, starts the application and advances the library cursor without fabricating listening
   activity.
6. A journal write that is interrupted by a kill or power loss leaves the journal readable; at the next start every
   reserved but unwritten number becomes a `recovery_gap`, and the library cursor advances past it.
7. If all application slots fail, fallback does not enter the same restart loop: it either installs an update or
   remains in recovery for an administrator.
8. Tests assert the degraded behavior only. They do not assert that old firmware provides features introduced by a
   newer library.