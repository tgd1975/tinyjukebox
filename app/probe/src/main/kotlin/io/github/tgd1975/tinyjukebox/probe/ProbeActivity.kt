package io.github.tgd1975.tinyjukebox.probe

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.graphics.Color
import android.nfc.NfcAdapter
import android.nfc.Tag
import android.nfc.tech.NfcA
import android.os.BatteryManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.view.MotionEvent
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.concurrent.Executors

/**
 * The probe of step 0 (concept of the app, section 9): how does a tag behave on a phone? One screen and a log, no
 * library, no sound. It reports when a tag is found and its UID in the form of rfid-reader, whether it is kept, how
 * late a removal is reported - by NfcAdapter.ignore() or by asking the tag every 250 ms -, what happens when the screen
 * goes off and on, and the battery while the screen is kept on and dimmed. The log is shared as text.
 */
class ProbeActivity : Activity(), NfcAdapter.ReaderCallback {
    private val main = Handler(Looper.getMainLooper())
    private val asking = Executors.newSingleThreadExecutor()
    private val lines = StringBuilder()
    private lateinit var logView: TextView
    private lateinit var modeButton: Button
    private var adapter: NfcAdapter? = null
    private var polling = false // false: NfcAdapter.ignore(); true: asking the tag every 250 ms
    private var current: String? = null
    private var foundAt = 0L
    private var lastTouch = SystemClock.elapsedRealtime()
    private var dimmed = false
    private var resumedAt = 0L

    private val screen = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            log(if (intent.action == Intent.ACTION_SCREEN_OFF) "screen off" else "screen on")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        adapter = NfcAdapter.getDefaultAdapter(this)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        logView = TextView(this).apply { textSize = 12f; setTextIsSelectable(true) }
        modeButton = Button(this).apply { setOnClickListener { polling = !polling; showMode(); log("mode: ${mode()}") } }
        val share = Button(this).apply {
            text = "Share log"
            setOnClickListener {
                startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_SUBJECT, "Tiny Jukebox probe ${Build.MANUFACTURER} ${Build.MODEL}")
                    putExtra(Intent.EXTRA_TEXT, lines.toString())
                }, "Share log"))
            }
        }
        val clear = Button(this).apply { text = "Clear"; setOnClickListener { lines.clear(); logView.text = ""; header() } }
        val buttons = LinearLayout(this).apply { addView(modeButton); addView(share); addView(clear) }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(buttons)
            addView(ScrollView(this@ProbeActivity).apply { addView(logView) })
        })
        showMode()
        header()
        registerReceiver(screen, IntentFilter().apply { addAction(Intent.ACTION_SCREEN_OFF); addAction(Intent.ACTION_SCREEN_ON) })
        main.post(object : Runnable {
            override fun run() {
                val idle = SystemClock.elapsedRealtime() - lastTouch
                if (!dimmed && idle > 30_000) dim(true)
                main.postDelayed(this, 1000)
            }
        })
        main.post(object : Runnable {
            override fun run() {
                val battery = (getSystemService(BATTERY_SERVICE) as BatteryManager).getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
                log("battery $battery %, screen ${if (dimmed) "dimmed" else "lit"}, tag ${current ?: "none"}")
                main.postDelayed(this, 60_000)
            }
        })
    }

    private fun header() {
        log("${Build.MANUFACTURER} ${Build.MODEL}, Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT}), NFC ${
            when { adapter == null -> "missing"; adapter?.isEnabled == true -> "on"; else -> "off" }
        }")
    }

    private fun mode() = if (polling) "asking the tag every 250 ms" else "NfcAdapter.ignore()"

    private fun showMode() {
        modeButton.text = "Mode: ${mode()}"
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        lastTouch = SystemClock.elapsedRealtime()
        if (dimmed) dim(false)
        return super.dispatchTouchEvent(event)
    }

    /** Dark and dimmed after 30 seconds without a touch, as simple mode keeps the screen (section 2.3). */
    private fun dim(on: Boolean) {
        dimmed = on
        window.attributes = window.attributes.apply { screenBrightness = if (on) 0.01f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE }
        logView.setBackgroundColor(if (on) Color.BLACK else Color.TRANSPARENT)
        log(if (on) "screen dimmed" else "screen lit")
    }

    override fun onResume() {
        super.onResume()
        resumedAt = SystemClock.elapsedRealtime()
        log("visible; looking for the tag")
        adapter?.enableReaderMode(
            this, this,
            NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or NfcAdapter.FLAG_READER_NFC_F or
                NfcAdapter.FLAG_READER_NFC_V or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK or NfcAdapter.FLAG_READER_NO_PLATFORM_SOUNDS,
            Bundle().apply { putInt(NfcAdapter.EXTRA_READER_PRESENCE_CHECK_DELAY, 250) },
        )
        main.postDelayed({
            if (current == null) log("2 s after becoming visible: no tag")
        }, 2000)
    }

    override fun onPause() {
        super.onPause()
        adapter?.disableReaderMode(this)
        log("not visible any more (tag ${current ?: "none"})")
    }

    override fun onDestroy() {
        unregisterReceiver(screen)
        asking.shutdownNow()
        super.onDestroy()
    }

    /** A tag was found: the UID as rfid-reader reports it, upper case with colons. */
    override fun onTagDiscovered(tag: Tag) {
        val uid = tag.id.joinToString(":") { "%02X".format(it) }
        val now = SystemClock.elapsedRealtime()
        val sinceVisible = now - resumedAt
        current = uid
        foundAt = now
        log("found $uid (${tag.id.size} bytes, ${tag.techList.joinToString { it.substringAfterLast('.') }}), ${sinceVisible} ms after becoming visible")
        if (polling) ask(tag, uid) else watch(tag, uid)
    }

    private fun watch(tag: Tag, uid: String) {
        val adapter = adapter ?: return
        try {
            adapter.ignore(tag, 50, {
                gone(uid, "ignore()")
            }, main)
        } catch (error: Exception) {
            log("ignore() failed: $error - asking instead")
            ask(tag, uid)
        }
    }

    /** Ask the tag every 250 ms; a failed answer is "gone". READ of page 0 for NFC-A tags, else connect again. */
    private fun ask(tag: Tag, uid: String) {
        asking.execute {
            val nfcA = NfcA.get(tag)
            var read = nfcA != null
            var failures = 0
            try {
                nfcA?.connect()
                while (current == uid) {
                    try {
                        if (nfcA != null && read) {
                            nfcA.transceive(byteArrayOf(0x30, 0x00))
                        } else {
                            nfcA?.close()
                            nfcA?.connect()
                        }
                        failures = 0
                    } catch (error: IOException) {
                        if (read && SystemClock.elapsedRealtime() - foundAt < 1000 && failures == 0) {
                            read = false // a tag that does not answer READ: connecting again is the question
                            log("READ not answered, asking by connecting again")
                            continue
                        }
                        failures++
                        if (failures >= 2) break
                    }
                    Thread.sleep(250)
                }
            } catch (error: Exception) {
                log("asking ended: $error")
            } finally {
                runCatching { nfcA?.close() }
            }
            main.post { if (current == uid) gone(uid, "asking") }
        }
    }

    private fun gone(uid: String, how: String) {
        if (current != uid) return
        val kept = SystemClock.elapsedRealtime() - foundAt
        current = null
        log("gone $uid (reported by $how) after ${kept / 1000.0} s on the phone")
    }

    private fun log(message: String) {
        val line = SimpleDateFormat("HH:mm:ss.SSS", Locale.ROOT).format(Date()) + "  " + message
        main.post {
            lines.append(line).append('\n')
            logView.append(line + "\n")
        }
    }
}
