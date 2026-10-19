# Concept: radio startup and stutter

Status: measurement implemented in 0.41.0; since 0.44.0 the moment the sound is heard is measured and shown by the
LED. Investigation and fix open. Written on 2026-10-17 together with central
diagnostics, which is implemented ([library.md](../library.md), section 4.22). Related: the radio pipeline
([architecture.md](../architecture.md), sections 2.3 and 9.3).

## 1. The symptom

The beginning of radio playback may stutter, and audible playback may start long after the tag is placed. The cause
is not established. Do not assume that reducing `buffer-time` is the fix: a smaller ALSA buffer can reduce startup
delay but tolerates fewer decoder or network stalls.

## 2. What is measured now

Per connection to a stream, on a clock that does not jump ([library.md](../library.md), section 4.22): resolving the
playlist, starting ffmpeg and aplay, the first audio from ffmpeg, when it is heard (`audible_ms`: ffmpeg has delivered
as much audio as aplay's buffer holds – aplay starts to play then), and the breaks of the sound (aplay's `underrun`) in
the first 30 s and in total. A connection that ends names the part that failed: playlist, start, connection, decoder,
output or the end of the stream. The records `radio.startup` and `radio.retry` reach the library with the log of the
box; the host of the station identifies it, never its whole address.

`audible_ms` comes from ffmpeg's `-progress` (every 0.25 s), not from aplay, which gives no sign: it may be up to
0.25 s late, plus what still lies in the pipe between the two. Records of 0.41.0 to 0.43.0 have
`audible_ms_estimated` and `buffer_ms` 1000 times too large (the buffer of the configuration, in ms, was read as
seconds); their other durations are right.

Until the sound is heard the station is "connecting" and the LED flows green, aqua, blue – as while an album waits
for a file: the box shows that it is getting ready instead of showing blue while it is silent.

## 3. Investigation

1. Collect the records from the box for a representative set of stations: direct streams, M3U/PLS stations and HLS
   streams, on the speaker and on the headset, over the WLAN of the box (power saving on).
2. Determine which phase accounts for the delay and which part detects the initial breaks.
3. Agree a startup target and an acceptable rate of initial breaks from those numbers, then choose the fix:
   playlist resolution, connection and probing of ffmpeg, buffering, decoder readiness or output startup.

## 4. Acceptance

1. From a tag placement, the box reports separate durations for resolution, pipeline startup and first audio;
   "playing" alone is not recorded as audible playback. (Done.)
2. Initial breaks are counted and attributed to the phase or part that detected them, without one log line per
   audio frame. (Done.)
3. The measurements of point 3.1 exist, and a target is agreed before buffer defaults are tuned.
4. The fix reaches the target on the box hardware and network.

## 5. Non-goals

This concept does not promise zero latency or uninterrupted radio for every station and network. It requires enough
evidence to diagnose and reduce the startup delay and initial breaks on supported setups.
