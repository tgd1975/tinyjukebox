# Concept: box recovery and tolerant data loading

Status: concept, not implemented. Written on 2026-10-17 after two failures on box `antonia` while running versions
0.39.0 and 0.39.1. The API compatibility contract is in [library.md](../library.md), sections 4.10 and 4.15; slots
and recovery are in sections 6.3–6.8 and 9.5.

## 1. What happened

Two independent failures exposed the same weakness: a local data error could prevent the box application from starting.

1. During the probation of 0.39.0, the Spotify test endpoint read the HTTP request body twice. The second read raised
   `ConnectionResetError`; the health check abandoned 0.39.0. This defect was fixed in 0.39.1 by parsing the body
   once.
2. The slot manager switched to fallback 0.30.0, but that version could not load the box's current `targets.json` or
   its backup. The old schema rejected the newer `cover` field and the `spotify` target type. Its `oneOf` validation
   error misleadingly said that `album` was not one of `radio`; 0.30.0 did know albums and radio stations. The
   application exited during initialization, systemd retried it, and neither the page nor the green LED became
   available. The rollback selected a fallback but did not restore a working box.
3. After 0.39.1 was confirmed, a later start failed while loading the local sync journal. Its last line contained 405
   NUL bytes. The preceding 76 records were valid (numbers 497–572), but the library had confirmed only through 495
   and the local counter was 574. Besides the malformed tail, the missing sequence numbers prevented the library
   from confirming later records.

For the second failure, the user chose not to make a separate backup. The 76 valid journal records were retained and
explicit `recovery_gap` records were inserted for 496, 573 and 574. The box started on 0.39.1, its page returned HTTP
200, and the library later confirmed through 584. The contents of the lost events could not be reconstructed.

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

## 3. Slots and recovery

The two application slots and the fallback slot have different jobs.

| Slot | Job | May start the box application? |
| --- | --- | --- |
| `a` and `b` | Normal operation, probation, and rollback to the last confirmed application version | Yes |
| `fallback` | Recovery only: fetch and install a signed, compatible firmware version | No |

The fallback flag must keep the application from starting. In particular, fallback must not load the YAML
configuration, catalog, sync outbox or journal. Its only path to the library is the stable update interface: ask for
available versions, fetch the description and signed slot, verify it, install it into `a`, then restart into `a`.
Non-update requests remain unavailable in recovery mode. If no suitable update can be installed, the box stays in a
clear recovery state and requires administrator intervention instead of restarting an unusable application forever.

The versioned API contract remains independent of this slot policy. The library permits requests supported by the
running application's interface and keeps the documented update endpoints available to an incompatible box. The
fallback itself never runs that application interface.

## 4. Tolerant data loading

Being able to boot an old application against data from the library does not mean it can implement every newer
feature. For example, 0.30.0 cannot play a Spotify target. It should still be able to start and play compatible local
albums and stations instead of failing because the catalog contains one Spotify entry.

The data layer should degrade at the smallest useful scope:

- Validate the document structure, but isolate invalid records. A missing required field makes that record
  unavailable; it should not invalidate every other record in the file.
- Accept unknown optional fields and target types when reading. Preserve them as opaque data, or avoid writing the
  library-owned catalog from an old box, so an older version cannot delete data it does not understand.
- A malformed JSONL tail must not prevent loading its valid prefix. Keep the valid records, identify the lost sequence
  numbers, and report them explicitly. Do not invent playback events to fill a gap; use a recovery marker that
  statistics can ignore and that lets the library advance its contiguous journal cursor.
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
6. If all application slots fail, fallback does not enter the same restart loop: it either installs an update or
   remains in recovery for an administrator.
7. Tests assert the degraded behavior only. They do not assert that old firmware provides features introduced by a
   newer library.