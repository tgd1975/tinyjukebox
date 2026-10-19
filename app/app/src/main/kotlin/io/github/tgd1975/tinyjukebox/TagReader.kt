package io.github.tgd1975.tinyjukebox

import android.app.Activity
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.tech.NfcA
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import java.io.IOException
import java.util.concurrent.Executors

/**
 * The NFC of the phone as the reader of a box (docs/developer/app.md, section 4): TAG_ON and TAG_OFF of rfid-reader.
 *
 * - Android reads tags only while the app is visible. Only a removal the app sees counts: when the app is visible
 *   again it looks for the tag for 2 seconds - there, nothing changes; not there, the tag counts as taken away.
 * - A tag that is lifted for less than 1 second is not a removal.
 * - Android reports when a tag is gone (NfcAdapter.ignore()); where that fails, the tag is asked every 250 ms.
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

    @Volatile
    private var current: String? = null

    /** Whether the phone can read tags at all. */
    val available: Boolean get() = adapter != null

    /** Whether NFC is switched on. */
    val enabled: Boolean get() = adapter?.isEnabled == true

    /** The app is visible: read tags. [lying] is the tag that was on the phone when the app left the screen. */
    fun resume(lying: String?) {
        val adapter = adapter ?: return
        visible = true
        current = null
        adapter.enableReaderMode(
            activity, this,
            NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or NfcAdapter.FLAG_READER_NFC_F or
                NfcAdapter.FLAG_READER_NFC_V or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK or NfcAdapter.FLAG_READER_NO_PLATFORM_SOUNDS,
            Bundle().apply { putInt(NfcAdapter.EXTRA_READER_PRESENCE_CHECK_DELAY, ASK_MS.toInt()) },
        )
        if (lying != null) {
            main.postDelayed({ if (visible && current != lying) off() }, LOOK_MS)
        }
    }

    /** The app leaves the screen: the playback goes on, as with every player on a phone. */
    fun pause() {
        visible = false
        main.removeCallbacksAndMessages(null)
        adapter?.disableReaderMode(activity)
    }

    fun close() = asking.shutdownNow()

    override fun onTagDiscovered(tag: Tag) {
        val uid = tag.id.joinToString(":") { "%02X".format(it) } // as rfid-reader reports it
        current = uid
        main.post { if (visible) on(uid) }
        val watched = runCatching { adapter?.ignore(tag, LIFT_MS.toInt(), { gone(uid) }, main) == true }.getOrDefault(false)
        if (!watched) ask(tag, uid)
    }

    private fun gone(uid: String) {
        if (!visible || current != uid) return
        current = null
        off()
    }

    /** Ask the tag every 250 ms; it is gone when it did not answer for a second. */
    private fun ask(tag: Tag, uid: String) {
        asking.execute {
            val nfcA = NfcA.get(tag)
            var read = nfcA != null
            var since = 0L
            try {
                nfcA?.connect()
                while (current == uid && visible) {
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
                        if (SystemClock.elapsedRealtime() - since >= LIFT_MS) break
                    }
                    Thread.sleep(ASK_MS)
                }
            } catch (error: Exception) {
                // the tag was gone at once
            } finally {
                runCatching { nfcA?.close() }
            }
            main.post { gone(uid) }
        }
    }

    companion object {
        /** A tag lifted for less than this is not a removal. */
        const val LIFT_MS = 1_000L

        /** How often a tag is asked whether it is still there. */
        const val ASK_MS = 250L

        /** How long the app looks for the tag when it is visible again. */
        const val LOOK_MS = 2_000L
    }
}
