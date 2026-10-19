# Concept: the app – what is still open

Status: steps 0 to 5 and 7 of the plan are implemented (0.40.0); the app is described in
[library.md](../library.md), section 5.2. Open: the measurements of the probe, and Spotify on the phone (step 6). This
file goes once both are done.

## 1. The measurements of the probe

The probe is built (`app/probe/`, library.md section 5.2.10); what it is to answer:

| Question | How it is measured |
| --- | --- |
| How do phone and tag lie best? | the tag on the back of the phone, the phone on the tag, for the kinds of tags there are (a disc, a card, a little figure), with and without a case; the spot of the reader |
| How reliably is a tag found? | time from putting on to the UID, failed reads, for tags of both makers (`04…`, `1D…`) |
| Is the UID the one of the box? | compared with the UIDs of the catalog |
| Is the tag kept? | minutes without a false "gone", with the phone lying still and being moved |
| How late is a removal reported? | `NfcAdapter.ignore()` against asking every 250 ms, both timed |
| What happens when the screen goes off and on? | logged: gone, found again, how fast |
| What does the dimmed, lit screen cost? | battery per hour with a tag on the phone |

The log is shared from the phone as text (button "Share log"; the mode button switches between `ignore()` and asking
every 250 ms). **The result decides** between presence and touch and sets the times of library.md, section 5.2.4.

The app implements presence with the times of library.md, section 5.2.4. If the probe shows that presence is
unreliable, simple mode works by touch – a tag touched plays, touched again pauses or continues, nothing ends by taking
it away – and the desktop box gets the same answer for D1 ([desktop-box-concept.md](desktop-box-concept.md),
section 3). After the measurements the probe is removed.

## 2. Spotify on the phone

Until this is built, a tag of an entry of Spotify says on the phone "This tag plays on a box" (state `ELSEWHERE`).

- **Entries of Spotify** ([architecture.md](../architecture.md), section 4.1) play through the Spotify app of the
  phone (decided, section 3). The app steers it with Spotify's SDK for Android apps (App Remote): it starts the
  entry at the saved track and second, keeps the position, stops when the tag is taken away and writes the journal
  as a box does. The sound is the Spotify app's, so the volume limit is held by setting the volume of the phone back
  while an entry plays, as a box sets its mixer back. What it needs is Spotify's: the owner registers the app once
  on Spotify's developer page, as for the Soloist key of a box, and enters its client id on the page of the library,
  which hands it to the phones. The Spotify accounts of the phones are entered in that registration: since February
  2026 Spotify allows five and asks Premium of its owner. Without the Spotify app or the registration, the tile says
  that this tag plays on a box.

### Step 6 – Spotify on the phone

- First tried by hand: the registration on Spotify's developer page with an account of the family subscription
  (Spotify asks Premium of the owner; whether a member of a family subscription counts is written nowhere), and
  whether the registration of the Soloist key serves the app as well – Spotify gives one registration per person.
- The client id on the page of the library; App Remote in the app; the position, the end and the journal as on a
  box; the volume limit held by the volume of the phone.
- Done when: a tag of an entry of Spotify plays on a phone from its saved track, stops when it is taken away, and
  is counted in the statistics; on a phone without the Spotify app its tile says that it plays on a box.

What is known since: Spotify's App Remote SDK is not on Maven Central. Spotify ships it as an AAR in the releases of
its GitHub repository `spotify/android-sdk`; a community build is on JitPack. Gradle would fetch the AAR from the
release with its checksum, so that nothing foreign lies in the repository; the APK then contains Spotify's SDK under
Spotify's developer terms, and README.md names it.

## 3. Decided

1 to 6 on 2026-10-11, 7 and 8 on 2026-10-12, 9 on 2026-10-16:

1. Android only.
2. The app is no remote control: it is a stand-alone variant of the box and plays itself.
3. Pairing by QR code from the page of the library.
4. Updates of the app by hand – probably; looked at again once the app exists.
5. Presence of a tag is polled, where the phone can.
6. The albums are loaded as a cache.
7. One version for boxes, library and app (library.md, section 5.2.8).
8. Simple mode keeps the screen on while it is visible, dimmed and dark (library.md, section 5.2.4).
9. A Spotify tag on the phone plays in the Spotify app of that phone, steered by our app; built last (step 6), so
   that nothing else waits for it. Not chosen: Spotify on the boxes only, with a tile on the phone that says so.
