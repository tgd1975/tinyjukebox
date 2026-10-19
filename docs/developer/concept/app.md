# Concept: the app – what is still open

Status: the plan is implemented (0.42.0); the app is described in [app.md](../app.md). Open:
the measurements of the probe. This file goes once they are written into library.md.

## 1. The measurements of the probe

The probe is built (`app/probe/`, app.md, section 10); what it is to answer:

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
every 250 ms). **The result decides** between presence and touch and sets the times of app.md, section 4.

The app implements presence with the times of app.md, section 4. If the probe shows that presence is
unreliable, simple mode works by touch – a tag touched plays, touched again pauses or continues, nothing ends by taking
it away – and the desktop box gets the same answer for D1 ([desktop-box-concept.md](desktop-box-concept.md),
section 3). After the measurements the probe is removed.

## 2. Spotify on the phone

Built (app.md, section 3); open is the registration at Spotify with an account of the family subscription
and the try on a phone (roadmap.md, section 1).

## 3. Decided

1 to 6 on 2026-10-11, 7 and 8 on 2026-10-12, 9 on 2026-10-16:

1. Android only.
2. The app is no remote control: it is a stand-alone variant of the box and plays itself.
3. Pairing by QR code from the page of the library.
4. Updates of the app by hand – probably; looked at again once the app exists.
5. Presence of a tag is polled, where the phone can.
6. The albums are loaded as a cache.
7. One version for boxes, library and app (app.md, section 8).
8. Simple mode keeps the screen on while it is visible, dimmed and dark (app.md, section 4).
9. A Spotify tag on the phone plays in the Spotify app of that phone, steered by our app; built last (step 6), so
   that nothing else waits for it. Not chosen: Spotify on the boxes only, with a tile on the phone that says so.
