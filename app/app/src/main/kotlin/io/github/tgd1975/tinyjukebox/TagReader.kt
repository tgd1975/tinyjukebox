package io.github.tgd1975.tinyjukebox

import android.app.Activity
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.tech.NfcA
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import java.io.IOException
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger

/**
 * The NFC of the phone as the reader of a box (docs/developer/app.md, section 4): TAG_ON and TAG_OFF of rfid-reader.
 *
 * - Android reads tags only while the app is visible. Only a removal the app sees counts: when the app is visible
 *   again it looks for the tag for 2 seconds - there, nothing changes; no tag at all, the tag counts as taken away;
 *   another tag in its place has replaced it and plays.
 * - A tag that is gone for less than 1 second is not a removal: it is reported only when the tag was not found again
 *   within that second, however Android lost it. The same tag found again while it lies there changes nothing.
 * - Android reports when a tag is gone (NfcAdapter.ignore()); where that fails, the tag is asked every 250 ms. Only the
 *   watch of the tag found last counts.
 * - Tags are read only: only the UID counts, nothing is ever written.
 */
class TagReader(
    private val activity: Activity,
    private val on: (String) -> Unit,
    private val off: () -> Unit,
) : NfcAdapter.ReaderCallback {
    private val adapter: NfcAdapter? = NfcAdapter.getDefaultAdapter(activity)
    private val main = Handler(Looper.getMainLooper())
    private val asking = Executors.newSingleThreadExecutor()

    @Volatile
    private var visible = false

    /** The tag on the phone, as reported with [on] - also while its removal waits for the second to pass. Main thread. */
    private var current: String? = null

    /** Counts the tags found: a watch of an earlier one ends and reports nothing. */
    private val found = AtomicInteger()

    private val removal = Runnable { removed() }

    /** The tag found last, for the log: Android may find the same tag ten times a second. */
    @Volatile
    private var logged: String? = null

    /** Whether the phone can read tags at all. */
    val available: Boolean get() = adapter != null

    /** Whether NFC is switched on. */
    val enabled: Boolean get() = adapter?.isEnabled == true

    /** The app is visible: read tags. [lying] is the tag that was on the phone when the app left the screen. */
    fun resume(lying: String?) {
        val adapter = adapter ?: return
        visible = true
        current = null
        found.incrementAndGet()
        adapter.enableReaderMode(
            activity, this,
            NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or NfcAdapter.FLAG_READER_NFC_F or
                NfcAdapter.FLAG_READER_NFC_V or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK or NfcAdapter.FLAG_READER_NO_PLATFORM_SOUNDS,
            Bundle().apply { putInt(NfcAdapter.EXTRA_READER_PRESENCE_CHECK_DELAY, ASK_MS.toInt()) },
        )
        if (lying != null) {
            // no tag found at all: the one that lay there was taken away. One that was found and lifted again within
            // these seconds has ended its playback itself (gone); another tag found has replaced it
            main.postDelayed({ if (visible && current == null) off() }, LOOK_MS)
        }
    }

    /** The app leaves the screen: the playback goes on, as with every player on a phone. */
    fun pause() {
        visible = false
        found.incrementAndGet()
        main.removeCallbacksAndMessages(null)
        adapter?.disableReaderMode(activity)
    }

    fun close() = asking.shutdownNow()

    override fun onTagDiscovered(tag: Tag) {
        val uid = tag.id.joinToString(":") { "%02X".format(it) } // as rfid-reader reports it
        val number = found.incrementAndGet()
        main.post { discovered(uid) }
        val watched = runCatching { adapter?.ignore(tag, IGNORE_MS, { gone(uid, number) }, main) == true }
            .getOrDefault(false)
        if (logged != uid) Log.i(TAG, "found $uid, watched by ${if (watched) "Android" else "asking"}")
        logged = uid
        if (!watched) ask(tag, uid, number)
    }

    /** A tag found. The same one again - also while its removal waits - changes nothing. */
    private fun discovered(uid: String) {
        if (!visible) return
        main.removeCallbacks(removal)
        if (current == uid) return
        current = uid
        on(uid)
    }

    /** The watch of tag [number] lost it: removed, unless it is found again within a second. */
    private fun gone(uid: String, number: Int) {
        if (!visible || number != found.get() || current != uid) return
        main.removeCallbacks(removal)
        main.postDelayed(removal, LIFT_MS)
    }

    private fun removed() {
        val uid = current ?: return
        if (!visible) return
        Log.i(TAG, "removed $uid")
        current = null
        logged = null
        off()
    }

    /** Ask the tag every 250 ms; it is gone when it did not answer for half a second. */
    private fun ask(tag: Tag, uid: String, number: Int) {
        asking.execute {
            val nfcA = NfcA.get(tag)
            var read = nfcA != null
            var since = 0L
            try {
                nfcA?.connect()
                while (number == found.get() && visible) {
                    try {
                        if (nfcA != null && read) nfcA.transceive(byteArrayOf(0x30, 0x00)) else {
                            nfcA?.close()
                            nfcA?.connect()
                        }
                        since = 0
                    } catch (error: IOException) {
                        if (read) {
                            read = false // a tag that does not answer READ: connecting again is the question
                            continue
                        }
                        if (since == 0L) since = SystemClock.elapsedRealtime()
                        if (SystemClock.elapsedRealtime() - since >= IGNORE_MS) break
                    }
                    Thread.sleep(ASK_MS)
                }
            } catch (error: Exception) {
                // the tag was gone at once
            } finally {
                runCatching { nfcA?.close() }
            }
            main.post { gone(uid, number) }
        }
    }

    companion object {
        private const val TAG = "TagReader"

        /** A tag gone for less than this is not a removal. */
        const val LIFT_MS = 1_000L

        /** How long a watch waits before it calls a tag lost; the removal follows [LIFT_MS] later. */
        const val IGNORE_MS = 500

        /** How often a tag is asked whether it is still there. */
        const val ASK_MS = 250L

        /** How long the app looks for the tag when it is visible again. */
        const val LOOK_MS = 2_000L
    }
}
