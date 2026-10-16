package io.github.tgd1975.tinyjukebox

import android.Manifest
import android.content.ComponentName
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.BitmapFactory
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.TextUtils
import android.view.Gravity
import android.view.MotionEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.FrameLayout
import android.widget.GridLayout
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.media3.session.MediaController
import androidx.media3.session.SessionToken
import com.google.common.util.concurrent.ListenableFuture
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import io.github.tgd1975.tinyjukebox.sync.AppData
import io.github.tgd1975.tinyjukebox.sync.HttpError
import io.github.tgd1975.tinyjukebox.sync.Pairing
import io.github.tgd1975.tinyjukebox.sync.Unreachable
import io.github.tgd1975.tinyjukebox.sync.WrongCertificate
import io.github.tgd1975.tinyjukebox.sync.obj
import io.github.tgd1975.tinyjukebox.sync.string
import io.github.tgd1975.tinyjukebox.sync.text
import java.io.File

/**
 * Simple mode (section 2.2 of the concept): what a box shows - the cover of the playing album, its title, track and
 * position, play/pause, next, previous - and below the tags as tiles to tap. A band of colour follows the LED of the
 * box. "Expert" asks for the login of the page; a child reaches neither the page nor the settings this way.
 *
 * Before the app is paired it shows nothing but the way to pair it (section 3.2).
 */
class MainActivity : ComponentActivity() {
    private val app by lazy { TinyJukeboxApp.of(this) }
    private val main = Handler(Looper.getMainLooper())
    private lateinit var reader: TagReader
    private var controller: ListenableFuture<MediaController>? = null

    // simple mode
    private lateinit var band: View
    private lateinit var coverView: ImageView
    private lateinit var titleView: TextView
    private lateinit var trackView: TextView
    private lateinit var messageView: TextView
    private lateinit var playButton: ImageButton
    private lateinit var tiles: GridLayout
    private lateinit var shade: View
    private var shownTiles: List<Any> = emptyList() // what the tiles show, with whether each cover is here
    private var bandStep = 0
    private var lastTouch = SystemClock.elapsedRealtime()
    private var dimmed = false
    private var byTile = false // the tag on the phone is a tile that was tapped, not a tag of NFC
    private var lying: String? = null // the tag of NFC that was on the phone when the app left the screen

    private val scan = registerForActivityResult(ScanContract()) { result -> result.contents?.let { paired(it) } }

    private val showSnapshot: (Jukebox.Snapshot) -> Unit = { show(it) }
    private val showChanged: () -> Unit = { if (app.sync.identity.paired) refreshTiles() else showPairing() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        reader = TagReader(this, ::tagOn, ::tagOff)
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 1)
        }
        pairFromIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        pairFromIntent(intent)
    }

    /** A debug build takes the content of the QR code from the intent: the emulator has no camera to scan with.
     * ``adb shell am start -n io.github.tgd1975.tinyjukebox/.MainActivity --es pair 'tinyjukebox:pair?...'`` */
    private fun pairFromIntent(intent: Intent?) {
        if (!BuildConfig.DEBUG || app.sync.identity.paired) return
        intent?.getStringExtra("pair")?.let { content -> main.post { paired(content) } }
    }

    override fun onStart() {
        super.onStart()
        if (app.sync.identity.paired) showSimple() else showPairing()
        app.listen(showChanged)
        app.jukebox.listen(showSnapshot)
        app.loop.visible(true)
        // the session of the player: playing goes on with the screen off and the app in the background
        controller = MediaController.Builder(this, SessionToken(this, ComponentName(this, PlaybackService::class.java))).buildAsync()
    }

    override fun onResume() {
        super.onResume()
        if (app.sync.identity.paired) reader.resume(if (byTile) null else lying ?: app.jukebox.currentUid())
        // while simple mode is visible the screen stays on, dark and dimmed after 30 seconds without a touch
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        lastTouch = SystemClock.elapsedRealtime()
        main.post(tick)
    }

    override fun onPause() {
        lying = if (byTile) null else app.jukebox.currentUid()
        reader.pause()
        main.removeCallbacks(tick)
        super.onPause()
    }

    override fun onStop() {
        app.unlisten(showChanged)
        app.jukebox.unlisten(showSnapshot)
        app.loop.visible(false)
        controller?.let { MediaController.releaseFuture(it) }
        controller = null
        super.onStop()
    }

    override fun onDestroy() {
        reader.close()
        super.onDestroy()
    }

    override fun dispatchTouchEvent(event: MotionEvent): Boolean {
        lastTouch = SystemClock.elapsedRealtime()
        if (dimmed) {
            dim(false)
            return true // the first touch only wakes the screen
        }
        return super.dispatchTouchEvent(event)
    }

    // --- tags ------------------------------------------------------------------------------------------------------------

    private fun tagOn(uid: String) {
        byTile = false
        lying = null
        app.jukebox.tagOn(uid)
        app.loop.wake()
        if (dimmed) dim(false)
    }

    private fun tagOff() {
        if (byTile) return // a tile stays until it is tapped again
        lying = null
        app.jukebox.tagOff()
    }

    /** A tile tapped: as if its tag were put on; a second tap, or a tap on another one, takes it away. */
    private fun tapped(uid: String) {
        if (app.jukebox.currentUid() == uid) {
            byTile = false
            app.jukebox.tagOff()
        } else {
            byTile = true
            lying = null
            app.jukebox.tagOn(uid)
            app.loop.wake()
        }
    }

    // --- pairing ---------------------------------------------------------------------------------------------------------

    private fun showPairing() {
        val status = text("", 15f)
        val button = Button(this).apply {
            text = getString(R.string.pair_scan)
            setOnClickListener {
                scan.launch(ScanOptions().setPrompt(getString(R.string.pair_prompt)).setBeepEnabled(false).setOrientationLocked(false))
            }
        }
        pairingStatus = status
        setContentView(ScrollView(this).apply {
            addView(column(padding = 24).apply {
                addView(logo())
                addView(text(getString(R.string.pair_title), 24f, bold = true))
                addView(text(getString(R.string.pair_help), 16f))
                addView(button)
                addView(status)
            })
        })
    }

    private var pairingStatus: TextView? = null

    private fun paired(content: String) {
        val pairing = Pairing.parse(content)
        if (pairing == null) {
            pairingStatus?.text = getString(R.string.pair_not_library)
            return
        }
        pairingStatus?.text = getString(R.string.pair_busy)
        Thread {
            val error = runCatching { app.sync.identity.pair(pairing, Build.MODEL) }.exceptionOrNull()
            main.post {
                if (error == null) {
                    app.sync.data.journal("start")
                    app.loop.wake()
                    showSimple()
                    reader.resume(null)
                } else {
                    pairingStatus?.text = when (error) {
                        is WrongCertificate -> if ("used up" in (error.message ?: "")) getString(R.string.pair_used)
                        else getString(R.string.pair_failed, error.message)
                        is Unreachable -> getString(R.string.pair_unreachable)
                        is HttpError -> getString(R.string.pair_failed, error.message)
                        else -> getString(R.string.pair_failed, error.toString())
                    }
                }
            }
        }.start()
    }

    // --- simple mode -----------------------------------------------------------------------------------------------------

    private fun showSimple() {
        band = View(this)
        coverView = ImageView(this).apply { scaleType = ImageView.ScaleType.CENTER_CROP }
        titleView = text("", 22f, bold = true).apply { gravity = Gravity.CENTER }
        trackView = text("", 16f).apply { gravity = Gravity.CENTER }
        messageView = text("", 16f).apply { gravity = Gravity.CENTER }
        val previous = roundButton(android.R.drawable.ic_media_previous, R.string.previous) { app.jukebox.previous() }
        previous.setOnLongClickListener { app.jukebox.restart(); true }
        playButton = roundButton(android.R.drawable.ic_media_play, R.string.play) { app.jukebox.playPause() }
        val next = roundButton(android.R.drawable.ic_media_next, R.string.next) { app.jukebox.next() }
        val expert = Button(this).apply {
            text = getString(R.string.expert)
            alpha = 0.6f
            setOnClickListener { startActivity(Intent(this@MainActivity, ExpertActivity::class.java)) }
        }
        tiles = GridLayout(this).apply { columnCount = 3; useDefaultMargins = true }
        shade = View(this).apply { setBackgroundColor(Color.BLACK); alpha = 0.85f; visibility = View.GONE }
        val controls = LinearLayout(this).apply {
            gravity = Gravity.CENTER
            addView(previous)
            addView(playButton)
            addView(next)
        }
        val content = column(padding = 16).apply {
            addView(LinearLayout(this@MainActivity).apply {
                gravity = Gravity.CENTER_VERTICAL
                addView(text(getString(R.string.app_name), 14f).apply { alpha = 0.7f }, LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f))
                addView(expert)
            })
            addView(coverView, LinearLayout.LayoutParams(dp(200), dp(200)).apply { gravity = Gravity.CENTER_HORIZONTAL; topMargin = dp(8) })
            addView(titleView)
            addView(trackView)
            addView(controls)
            addView(messageView)
            addView(tiles)
        }
        val root = FrameLayout(this).apply {
            setBackgroundColor(0xff16111a.toInt())
            addView(LinearLayout(this@MainActivity).apply {
                orientation = LinearLayout.VERTICAL
                addView(band, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(16)))
                addView(ScrollView(this@MainActivity).apply { addView(content) })
            })
            addView(shade, FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT))
        }
        setContentView(root)
        shownTiles = emptyList()
        refreshTiles()
        show(app.jukebox.snapshot)
    }

    private fun refreshTiles() {
        if (!::tiles.isInitialized) return
        val targets = app.sync.data.working("target")
        val entries = app.sync.data.working("tag").mapNotNull { (uid, tag) ->
            val link = tag["target"].obj ?: return@mapNotNull null
            if (AppData.unlinked(link)) return@mapNotNull null
            val target = targets[link["id"].text] ?: return@mapNotNull null
            val name = tag["tag"].obj?.get("name").string?.takeIf { it.isNotBlank() } ?: app.jukebox.title(target) ?: uid
            Triple(uid, name, target["cover"].string)
        }.sortedBy { it.second.lowercase() }
        // a cover arrives after the catalog: the tiles are drawn again when it is here
        val shown = entries.map { (uid, name, cover) -> listOf(uid, name, cover, cover?.let { app.sync.images.file(it) } != null) }
        if (shown == shownTiles) return
        shownTiles = shown
        tiles.removeAllViews()
        val size = (resources.displayMetrics.widthPixels - dp(32)) / 3 - dp(8)
        for ((uid, name, cover) in entries) {
            val image = ImageView(this).apply {
                scaleType = ImageView.ScaleType.CENTER_CROP
                setBackgroundColor(0xff3a1f33.toInt())
                cover?.let { app.sync.images.file(it) }?.let { setImageBitmap(decode(it, size)) }
            }
            val tile = FrameLayout(this).apply {
                tag = uid
                contentDescription = name
                addView(image, FrameLayout.LayoutParams(size, size))
                addView(text(name, 12f).apply {
                    setBackgroundColor(0xaa000000.toInt())
                    maxLines = 2
                    ellipsize = TextUtils.TruncateAt.END
                    setPadding(dp(4), dp(2), dp(4), dp(2))
                }, FrameLayout.LayoutParams(size, ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM))
                setOnClickListener { tapped(uid) }
            }
            tiles.addView(tile, GridLayout.LayoutParams().apply { width = size; height = size })
        }
        markTile(app.jukebox.currentUid())
    }

    private fun markTile(uid: String?) {
        for (index in 0 until tiles.childCount) {
            val tile = tiles.getChildAt(index)
            tile.foreground = if (tile.tag == uid) GradientDrawable().apply { setStroke(dp(4), 0xfff4f1f3.toInt()) } else null
        }
    }

    private fun show(snapshot: Jukebox.Snapshot) {
        if (!::band.isInitialized || !app.sync.identity.paired) return
        val cover = snapshot.cover
        coverView.setImageBitmap(cover?.let { decode(it, dp(200)) })
        coverView.setBackgroundColor(if (cover == null && snapshot.title != null) 0xff3a1f33.toInt() else Color.TRANSPARENT)
        titleView.text = snapshot.title ?: ""
        val idle = when {
            !reader.available -> getString(R.string.no_nfc)
            !reader.enabled -> getString(R.string.nfc_off)
            else -> getString(R.string.hold_tag)
        }
        messageView.text = snapshot.message?.let { getString(it) }
            ?: if (snapshot.state == Jukebox.State.WAIT_FOR_TAG) idle
            else if (snapshot.state == Jukebox.State.CHECK_TAG) getString(R.string.checking) else ""
        playButton.setImageResource(if (snapshot.playing) android.R.drawable.ic_media_pause else android.R.drawable.ic_media_play)
        playButton.contentDescription = getString(if (snapshot.playing) R.string.pause else R.string.play)
        markTile(snapshot.uid)
        showTrack()
        bandStep = 0
        paintBand()
    }

    private fun showTrack() {
        val snapshot = app.jukebox.snapshot
        val position = snapshot.positionMs + if (snapshot.playing) SystemClock.elapsedRealtime() - snapshot.positionAt else 0
        trackView.text = when {
            snapshot.radio -> getString(R.string.station)
            snapshot.tracks > 0 && snapshot.state == Jukebox.State.PLAY_ALBUM ->
                getString(R.string.track_of, snapshot.track + 1, snapshot.tracks) + "   " + minutes(position)
            else -> ""
        }
    }

    private fun paintBand() {
        val colors = app.jukebox.snapshot.band.colors
        band.setBackgroundColor(colors[bandStep % colors.size])
    }

    /** Twice a second: the position, the band, and the dimmed screen. */
    private val tick = object : Runnable {
        private var last = 0L

        override fun run() {
            if (::band.isInitialized && app.sync.identity.paired) {
                showTrack()
                val step = app.jukebox.snapshot.band.stepMs
                val now = SystemClock.elapsedRealtime()
                if (step > 0 && now - last >= step) {
                    last = now
                    bandStep++
                    paintBand()
                }
                if (!dimmed && now - lastTouch > DIM_MS) dim(true)
            }
            main.postDelayed(this, 200)
        }
    }

    private fun dim(on: Boolean) {
        dimmed = on
        if (::shade.isInitialized) shade.visibility = if (on) View.VISIBLE else View.GONE
        window.attributes = window.attributes.apply {
            screenBrightness = if (on) 0.01f else WindowManager.LayoutParams.BRIGHTNESS_OVERRIDE_NONE
        }
    }

    // --- small helpers ---------------------------------------------------------------------------------------------------

    private fun dp(value: Int) = (value * resources.displayMetrics.density).toInt()

    private fun minutes(ms: Long): String = "%d:%02d".format(ms / 60_000, ms / 1000 % 60)

    private fun decode(file: File, size: Int) = runCatching {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(file.path, bounds)
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= size) sample *= 2
        BitmapFactory.decodeFile(file.path, BitmapFactory.Options().apply { inSampleSize = sample })
    }.getOrNull()

    private fun column(padding: Int) = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(dp(padding), dp(padding), dp(padding), dp(padding))
    }

    private fun text(value: String, size: Float, bold: Boolean = false) = TextView(this).apply {
        text = value
        textSize = size
        setTextColor(0xfff4f1f3.toInt())
        if (bold) typeface = Typeface.DEFAULT_BOLD
        setPadding(0, dp(6), 0, dp(6))
    }

    private fun logo() = ImageView(this).apply {
        setImageResource(R.mipmap.ic_launcher)
        layoutParams = LinearLayout.LayoutParams(dp(96), dp(96))
    }

    private fun roundButton(icon: Int, label: Int, action: () -> Unit) = ImageButton(this).apply {
        setImageResource(icon)
        contentDescription = getString(label)
        background = GradientDrawable().apply { shape = GradientDrawable.OVAL; setColor(0xff8e2f66.toInt()) }
        scaleType = ImageView.ScaleType.CENTER_INSIDE
        setOnClickListener { action() }
        layoutParams = LinearLayout.LayoutParams(dp(72), dp(72)).apply { setMargins(dp(12), dp(12), dp(12), dp(12)) }
    }

    companion object {
        const val DIM_MS = 30_000L
    }
}
