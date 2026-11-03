package io.github.tgd1975.tinyjukebox

import android.app.Application
import android.content.Context
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import io.github.tgd1975.tinyjukebox.sync.Fit
import io.github.tgd1975.tinyjukebox.sync.Identity
import io.github.tgd1975.tinyjukebox.sync.Sync
import java.io.File
import java.io.IOException
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * The app: the synchronization with the library (module sync) and the jukebox of simple mode. Everything of it lies
 * in the private folder of the app, in the same form as on a box (docs/developer/app.md, section 6).
 */
class TinyJukeboxApp : Application() {
    lateinit var sync: Sync
        private set
    lateinit var jukebox: Jukebox
        private set
    lateinit var loop: SyncLoop
        private set

    /** The page is logged in in expert mode: the settings of the app may be opened. */
    @Volatile
    var expert = false

    private val main = Handler(Looper.getMainLooper())
    private val listeners = mutableListOf<() -> Unit>()

    override fun onCreate() {
        super.onCreate()
        val files = filesDir
        sync = Sync(
            Identity(File(files, "identity")), File(files, "sync"), File(files, "albums"), File(files, "images"),
            "app-${BuildConfig.VERSION_NAME}", Build.MODEL, cacheLimit(),
            playing = { jukebox.playingAlbum() },
            log = { Log.i(TAG, it) },
        )
        loop = SyncLoop(sync) { changed() }
        jukebox = Jukebox(this, sync, loop)
        sync.data.changed = { jukebox.catalogChanged(); changed() }
        if (sync.identity.paired) started()
        loop.start()
    }

    /** The line "start" of the journal (app.md, section 5): the process starts paired, or was paired just now. A
     * storage that is full does not stop the app. */
    fun started() {
        try {
            sync.data.journal("start")
        } catch (error: IOException) {
            Log.e(TAG, "the journal line start cannot be stored: $error")
        }
    }

    /** Something the screens show changed: the catalog, the connection, the state of the jukebox. */
    fun changed() = main.post { listeners.toList().forEach { it() } }

    fun listen(listener: () -> Unit) = listeners.add(listener)

    fun unlisten(listener: () -> Unit) = listeners.remove(listener)

    /** The limit of the cache in bytes: "at most ... GB", 4 GB at first (app.md, section 6). */
    fun cacheLimit(): Long = (preferences().getFloat(CACHE_GB, 4f) * (1L shl 30)).toLong()

    fun setCacheLimit(gigabytes: Float) {
        preferences().edit().putFloat(CACHE_GB, gigabytes).apply()
        sync.cache.limit = cacheLimit()
    }

    private fun preferences() = getSharedPreferences("app", Context.MODE_PRIVATE)

    /** What the expert mode and the settings say when app and library do not fit together (app.md, section 5). */
    fun fitText(): String? = when {
        sync.fit.startsWith(Fit.APP_TOO_OLD) -> getString(R.string.app_too_old)
        sync.fit.startsWith(Fit.LIBRARY_TOO_OLD) -> getString(R.string.library_too_old)
        else -> null
    }

    companion object {
        const val TAG = "TinyJukebox"
        private const val CACHE_GB = "cache-gb"

        fun of(context: Context) = context.applicationContext as TinyJukeboxApp
    }
}

/**
 * When the app talks to the library (docs/developer/app.md, section 5): a pass on start, when a tag is put on, after a change
 * and every minute while something plays; while the app is visible it also listens to the events of the library - from
 * the first pass that reaches it, and not while app and library do not fit together.
 */
class SyncLoop(private val sync: Sync, private val changed: () -> Unit) {
    private val lock = ReentrantLock()
    private val woken = lock.newCondition()
    private val passed = lock.newCondition()
    private var wake = false
    private var passes = 0

    @Volatile
    var playing = false

    @Volatile
    private var visible = false
    private var listener: Thread? = null

    fun start() {
        Thread({ run() }, "SyncLoop").apply { isDaemon = true }.start()
    }

    private fun run() {
        while (true) {
            val again = sync.pass()
            lock.withLock {
                passes++
                passed.signalAll()
            }
            changed()
            if (again) continue
            lock.withLock {
                if (!wake) woken.await(if (playing) PLAYING_MS else IDLE_MS, java.util.concurrent.TimeUnit.MILLISECONDS)
                wake = false
            }
        }
    }

    fun wake() = lock.withLock {
        wake = true
        woken.signalAll()
    }

    /** Trigger a pass and wait until it is over, at most [timeoutMs]. */
    fun waitPass(timeoutMs: Long): Boolean = lock.withLock {
        val before = passes
        wake = true
        woken.signalAll()
        var left = timeoutMs * 1_000_000
        while (passes <= before && left > 0) left = passed.awaitNanos(left)
        passes > before
    }

    /** The app is visible: listen to the events of the library; not visible: stop listening. */
    fun visible(on: Boolean) {
        visible = on
        if (on && listener?.isAlive != true) {
            listener = Thread({ listen() }, "SyncLoop.events").apply { isDaemon = true; start() }
        } else if (!on) {
            sync.stopListening()
            lock.withLock { passed.signalAll() } // the listener does not wait for a pass any more
        }
    }

    private fun listen() {
        while (visible) {
            if (sync.connected && sync.fit == Fit.OK && sync.identity.paired) {
                runCatching { sync.listen { wake() } }
                if (!visible) break
                wake() // the connection broke: the pass finds out whether the library is gone
                Thread.sleep(AGAIN_MS)
            } else if (!awaitPass(if (sync.connected) UNFIT_MS else ALONE_MS) && visible) {
                // no pass for a while: one asks whether the library can be reached - or fits again, after an update
                wake()
            }
        }
    }

    /** Wait until a pass is over, at most [timeoutMs] and while the app is visible; whether one was. */
    private fun awaitPass(timeoutMs: Long): Boolean = lock.withLock {
        val before = passes
        var left = timeoutMs * 1_000_000
        while (passes <= before && left > 0 && visible) left = passed.awaitNanos(left)
        passes > before
    }

    companion object {
        /** A pass this often while something plays (app.md, section 5). */
        const val PLAYING_MS = 60_000L

        /** ... and otherwise: the app runs a pass on start, on a tag and after a change, rarely by itself. */
        const val IDLE_MS = 30 * 60_000L

        /** While the app is visible: the events again after a broken connection, ... */
        const val AGAIN_MS = 2_000L

        /** ... a pass after this long without one while the library cannot be reached, ... */
        const val ALONE_MS = 30_000L

        /** ... and while app and library do not fit together: only an update of one of them changes that. */
        const val UNFIT_MS = 10 * 60_000L
    }
}
