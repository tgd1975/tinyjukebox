package io.github.tgd1975.tinyjukebox

import android.content.Context
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.MediaMetadata
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.exoplayer.ExoPlayer
import io.github.tgd1975.tinyjukebox.sync.AppData
import io.github.tgd1975.tinyjukebox.sync.Sync
import io.github.tgd1975.tinyjukebox.sync.boolean
import io.github.tgd1975.tinyjukebox.sync.double
import io.github.tgd1975.tinyjukebox.sync.int
import io.github.tgd1975.tinyjukebox.sync.json
import io.github.tgd1975.tinyjukebox.sync.obj
import io.github.tgd1975.tinyjukebox.sync.string
import io.github.tgd1975.tinyjukebox.sync.text
import kotlinx.serialization.json.JsonObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.CountDownLatch

/**
 * Simple mode: the phone as a box (docs/developer/library.md, section 5.2.3). One small state machine with the states of
 * STMOperation that apply, and the colours of the LED as a band on the screen. A tag put on - read by NFC or a tile
 * tapped - plays its album from the saved position, or its station; taken away, the playback ends and the position is
 * saved. Positions and the journal are written as a box writes them.
 *
 * Everything runs on the thread of the jukebox, which is also the thread of the player.
 */
class Jukebox(private val context: Context, private val sync: Sync, private val loop: SyncLoop) {
    enum class State { WAIT_FOR_TAG, CHECK_TAG, UNMAPPED_TAG, UNKNOWN_ALBUM, FETCH_ALBUM, PLAY_ALBUM, BROKEN_ALBUM, ELSEWHERE }

    /** What an album does while its tag lies on the phone (STMPlayAlbum). */
    enum class Play { NONE, PLAY, PAUSED, WAIT, FINISHED }

    /** The band of colour: the LED of the box. Colours alternate every [stepMs]. */
    enum class Band(val colors: List<Int>, val stepMs: Long) {
        GREEN(listOf(0xff2fbf4f.toInt()), 0),
        YELLOW(listOf(0xfff6c419.toInt()), 0),
        YELLOW_RED(listOf(0xfff6c419.toInt(), 0xffe0303a.toInt()), 1000),
        FLOW(listOf(0xff2fbf4f.toInt(), 0xff27c4c4.toInt(), 0xff4f8ff7.toInt(), 0xff27c4c4.toInt()), 400),
        AQUA(listOf(0xff27c4c4.toInt()), 0),
        BLUE(listOf(0xff4f8ff7.toInt()), 0),
        WHITE_BLUE(listOf(0xfff4f1f3.toInt(), 0xff4f8ff7.toInt()), 1000),
        BLUE_BLINK(listOf(0xff4f8ff7.toInt(), 0xff000000.toInt()), 1000),
        RED_BLACK(listOf(0xffe0303a.toInt(), 0xff000000.toInt()), 1000),
    }

    /** What the screen shows. The position moves on by itself while [playing]: [positionMs] at [positionAt]. */
    data class Snapshot(
        val state: State = State.WAIT_FOR_TAG,
        val play: Play = Play.NONE,
        val band: Band = Band.GREEN,
        val uid: String? = null,
        val title: String? = null,
        val cover: File? = null,
        val track: Int = 0,
        val tracks: Int = 0,
        val radio: Boolean = false,
        val positionMs: Long = 0,
        val positionAt: Long = 0,
        val playing: Boolean = false,
        val message: Int? = null,
    )

    private val thread = HandlerThread("Jukebox").apply { start() }
    private val handler = Handler(thread.looper)
    private val main = Handler(Looper.getMainLooper())
    private val audio = context.getSystemService(Context.AUDIO_SERVICE) as AudioManager
    lateinit var player: ExoPlayer
        private set

    @Volatile
    var snapshot = Snapshot()
        private set
    private val listeners = mutableListOf<(Snapshot) -> Unit>()

    // the tag on the phone, and what it plays
    private var uid: String? = null
    private var state = State.WAIT_FOR_TAG
    private var play = Play.NONE
    private var signature: Any? = null
    private var target: JsonObject? = null
    private var album: String? = null
    private var tracks: List<String> = emptyList()
    private var track = 0
    private var finished = false
    private var started: Pair<Int, Double>? = null // track and position at which the playback began
    private var radio = false
    private var message: Int? = null

    @Volatile
    private var playingAlbum: String? = null

    init {
        val built = CountDownLatch(1)
        handler.post {
            player = ExoPlayer.Builder(context).setLooper(thread.looper)
                .setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_MUSIC).build(), true)
                .setHandleAudioBecomingNoisy(true)
                .setWakeMode(C.WAKE_MODE_NETWORK)
                .build()
            player.addListener(object : Player.Listener {
                override fun onPlaybackStateChanged(playbackState: Int) {
                    if (playbackState == Player.STATE_ENDED) trackEnded()
                }

                override fun onPlayerError(error: PlaybackException) = failed(error)

                override fun onPlayWhenReadyChanged(playWhenReady: Boolean, reason: Int) {
                    // the headphones were taken off, or another app took the sound: a pause as with the button
                    if (!playWhenReady && play == Play.PLAY && reason != Player.PLAY_WHEN_READY_CHANGE_REASON_USER_REQUEST) paused()
                }
            })
            audio.registerAudioDeviceCallback(object : AudioDeviceCallback() {
                override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>) = limitVolume()
                override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>) = limitVolume()
            }, handler)
            built.countDown()
        }
        built.await()
        sync.cache.subscribe { album -> handler.post { fileArrived(album) } }
    }

    // --- what the screens and the media session ask -------------------------------------------------------------------

    fun listen(listener: (Snapshot) -> Unit) = synchronized(listeners) { listeners.add(listener) }

    fun unlisten(listener: (Snapshot) -> Unit) = synchronized(listeners) { listeners.remove(listener) }

    /** The album that is playing: it never leaves the cache. */
    fun playingAlbum(): String? = playingAlbum

    /** The tag on the phone, or the tile that was tapped. */
    fun currentUid(): String? = snapshot.uid

    private fun later(action: () -> Unit) {
        if (Looper.myLooper() == thread.looper) action() else handler.post(action)
    }

    /** A tag is put on, or a tile tapped (library.md, section 5.2.3). The same tag again changes nothing while it lies there. */
    fun tagOn(uid: String) = later {
        if (uid == this.uid) return@later
        this.uid?.let { old ->
            note("end", "uid" to old, "reason" to "tag replaced")
            stopPlayback()
        }
        this.uid = uid
        note("tag", "uid" to uid, *linkedTo(uid), "output" to output())
        check()
    }

    /** The tag is taken away: the playback ends and the position is saved. */
    fun tagOff() = later {
        val old = uid ?: return@later
        note("end", "uid" to old, "reason" to "tag removed")
        stopPlayback()
        uid = null
        target = null
        set(State.WAIT_FOR_TAG)
        loop.wake()
    }

    fun playPause() = later {
        when (play) {
            Play.PLAY -> { control("pause"); paused() }
            Play.PAUSED -> { control("play"); resume() }
            else -> if (radio && state == State.PLAY_ALBUM) { if (player.isPlaying) player.pause() else player.play(); publish() }
        }
    }

    fun next() = later {
        if (album == null || state != State.PLAY_ALBUM || play == Play.FINISHED) return@later
        control("next")
        leaveTrack(toEnd = false)
        if (track + 1 < tracks.size) startTrack(track + 1, 0.0) else albumFinished()
    }

    /** Within the first 10 seconds of a track "previous" goes to the previous track, later to the start of this one. */
    fun previous() = later {
        if (album == null || state != State.PLAY_ALBUM || play == Play.FINISHED) return@later
        control("prev")
        val position = player.currentPosition / 1000.0
        leaveTrack(toEnd = false)
        if (position <= REWIND_MAX_SECONDS && track > 0) startTrack(track - 1, 0.0) else startTrack(track, 0.0)
    }

    /** "Previous" held: the album from the start. */
    fun restart() = later {
        if (album == null || state != State.PLAY_ALBUM) return@later
        control("restart")
        leaveTrack(toEnd = false)
        finished = false
        startTrack(0, 0.0)
    }

    /** The catalog or the settings changed: the tag on the phone is evaluated again if what it plays changed
     * (recheck_tag() of a box) - a tag linked on the page meanwhile plays. Nothing else reacts. */
    fun catalogChanged() = later {
        limitVolume()
        val uid = uid ?: return@later
        if (signature(uid) != signature) {
            stopPlayback()
            check()
        } else {
            publish()
        }
    }

    // --- checking the tag (CheckTag of a box) -------------------------------------------------------------------------

    private fun linkedTo(uid: String): Array<Pair<String, Any?>> {
        val target = sync.data.working("tag")[uid]?.get("target").obj
        if (target == null || AppData.unlinked(target)) return emptyArray()
        return arrayOf("kind" to target["type"].string, "target" to target["id"].text)
    }

    private fun signature(uid: String): Any? {
        val tag = sync.data.working("tag")[uid] ?: return null
        val id = tag["target"].obj?.get("id").text
        return Pair(tag["target"], id?.let { sync.data.working("target")[it] })
    }

    private fun check() {
        val uid = uid ?: return
        set(State.CHECK_TAG)
        if (uid !in sync.data.working("tag")) {
            // an unknown tag: ask the library before it is taken for a new one
            loop.waitPass(LOOKUP_MS)
            if (this.uid != uid) return
            if (uid !in sync.data.working("tag")) {
                Log.i(TinyJukeboxApp.TAG, "new tag $uid - reported to the library, not linked")
                sync.data.add("tag", uid, AppData.unknownTag(uid))
                loop.wake()
            }
        }
        signature = signature(uid)
        val tag = sync.data.working("tag")[uid]
        val link = tag?.get("target").obj
        if (link == null || AppData.unlinked(link)) return set(State.UNMAPPED_TAG, R.string.unmapped)
        val target = sync.data.working("target")[link["id"].text]
        if (target == null || target["target-type"].string != link["type"].string) {
            return set(State.UNKNOWN_ALBUM, R.string.unknown_album)
        }
        this.target = target
        when (target["target-type"].string) {
            "album" -> startAlbum(link["id"].text!!, target)
            "radio" -> startRadio(target)
            else -> set(State.ELSEWHERE, R.string.spotify_box)
        }
    }

    // --- an album ------------------------------------------------------------------------------------------------------

    private fun startAlbum(id: String, value: JsonObject) {
        album = id
        radio = false
        val position = sync.data.working("position")[uid]?.takeIf {
            it["target-id"].text == id && it["target-type"].string == "album"
        }
        finished = position?.get("album-finished").boolean == true
        track = if (finished) 0 else position?.get("track-id").int ?: 0
        val seconds = if (finished) 0.0 else position?.get("track-time").double ?: 0.0
        sync.cache.takeOver(id)
        sync.cache.played(id)
        if (!sync.cache.ready(id, track)) {
            set(State.FETCH_ALBUM, R.string.fetching)
            sync.cache.need(id, track)
            pending = seconds
            return
        }
        begin(value, seconds)
    }

    private var pending = 0.0 // the position at which a fetched album begins

    private fun begin(value: JsonObject, seconds: Double) {
        val album = album ?: return
        tracks = sync.cache.tracks(album)
        if (tracks.isEmpty()) return set(State.BROKEN_ALBUM, R.string.broken)
        if (track !in tracks.indices) {
            track = 0
            finished = false
        }
        playingAlbum = album
        loop.playing = true
        state = State.PLAY_ALBUM
        target = value
        finished = false
        startTrack(track, seconds)
        scheduleAlive()
    }

    private fun startTrack(index: Int, seconds: Double) {
        val album = album ?: return
        track = index
        if (!sync.cache.ready(album, index)) {
            // the next track has not arrived: wait at the end of the track; "next" pulls it forward
            play = Play.WAIT
            message = R.string.waiting_track
            sync.cache.need(album, index)
            savePosition(0.0, tell = false)
            publish()
            return
        }
        val file = sync.cache.file(album, tracks[index]) ?: return set(State.BROKEN_ALBUM, R.string.broken)
        player.setMediaItem(mediaItem(Uri.fromFile(file), tracks[index]), (seconds * 1000).toLong())
        player.prepare()
        player.volume = volumeLimit()
        player.play()
        play = Play.PLAY
        message = null
        started = index to seconds
        savePosition(seconds, tell = true)
        scheduleUpdate()
        publish()
    }

    private fun resume() {
        if (album == null) return
        player.play()
        play = Play.PLAY
        started = track to player.currentPosition / 1000.0
        scheduleUpdate()
        publish()
    }

    private fun paused() {
        if (play != Play.PLAY) return
        if (radio) return publish()
        leaveTrack(toEnd = false)
        play = Play.PAUSED
        loop.wake()
        publish()
    }

    /** The track is left: a line for the journal, which track was listened to, for how long, whether to its end. */
    private fun leaveTrack(toEnd: Boolean) {
        val begun = started ?: return
        started = null
        player.pause()
        val position = if (toEnd) player.duration.coerceAtLeast(0) / 1000.0 else player.currentPosition / 1000.0
        note("track", "album" to album, "track" to begun.first, "seconds" to Math.round(maxOf(0.0, position - begun.second) * 10) / 10.0, "to_end" to toEnd)
        savePosition(if (toEnd) 0.0 else position, tell = true)
    }

    private fun trackEnded() {
        if (radio) return failed(null)
        if (play != Play.PLAY) return
        leaveTrack(toEnd = true)
        if (track + 1 < tracks.size) startTrack(track + 1, 0.0) else albumFinished()
    }

    /** The album has played to its end: the band blinks blue; the tag put on again starts it from the beginning. */
    private fun albumFinished() {
        play = Play.FINISHED
        finished = true
        player.stop()
        savePosition(0.0, tell = true, done = true)
        note("end", "album" to album, "reason" to "album finished")
        playingAlbum = null
        loop.playing = false
        publish()
    }

    private fun fileArrived(id: String) {
        if (id != album) return
        val failed = sync.cache.failed(id)
        when {
            state == State.FETCH_ALBUM && sync.cache.ready(id, track) -> target?.let { begin(it, pending) }
            state == State.FETCH_ALBUM && failed != null && id !in sync.cache.fetching() ->
                set(State.BROKEN_ALBUM, if (sync.connected) R.string.broken else R.string.broken_offline)
            state == State.PLAY_ALBUM && play == Play.WAIT && sync.cache.ready(id, track) -> startTrack(track, 0.0)
            else -> publish()
        }
    }

    // --- a station -----------------------------------------------------------------------------------------------------

    private fun startRadio(value: JsonObject) {
        radio = true
        album = null
        state = State.PLAY_ALBUM
        play = Play.NONE
        target = value
        val url = stream(value["url"].string ?: "")
        if (url == null) return set(State.BROKEN_ALBUM, R.string.station_failed)
        player.setMediaItem(mediaItem(Uri.parse(url), value["name"].string ?: ""))
        player.prepare()
        player.volume = volumeLimit()
        player.play()
        loop.playing = true
        scheduleAlive()
        publish()
    }

    /** The address of the stream: a playlist (m3u, pls) names it. */
    private fun stream(url: String): String? {
        if (!url.startsWith("http")) return null
        if (!Regex("""\.(m3u8?|pls)(\?.*)?$""", RegexOption.IGNORE_CASE).containsMatchIn(url) || url.contains(".m3u8")) return url
        return runCatching {
            val connection = URL(url).openConnection() as HttpURLConnection
            connection.connectTimeout = 5000
            connection.readTimeout = 5000
            connection.inputStream.bufferedReader().use { reader ->
                reader.lineSequence().map { it.trim().substringAfter("=", it.trim()) }.firstOrNull { it.startsWith("http") }
            }
        }.getOrNull()
    }

    private fun failed(error: PlaybackException?) {
        Log.w(TinyJukeboxApp.TAG, "playback failed: $error")
        if (radio) {
            player.stop()
            loop.playing = false
            return set(State.BROKEN_ALBUM, R.string.station_failed)
        }
        // a track that cannot be played is skipped, as ffmpeg plays on through a damaged file
        if (play == Play.PLAY) trackEnded()
    }

    // --- ending ----------------------------------------------------------------------------------------------------------

    private fun stopPlayback() {
        if (state == State.PLAY_ALBUM && !radio && play == Play.PLAY) leaveTrack(toEnd = false)
        else if (state == State.PLAY_ALBUM && !radio && play == Play.WAIT) savePosition(0.0, tell = true)
        player.stop()
        player.clearMediaItems()
        handler.removeCallbacks(update)
        handler.removeCallbacks(alive)
        album = null
        tracks = emptyList()
        radio = false
        play = Play.NONE
        playingAlbum = null
        loop.playing = false
        started = null
    }

    // --- positions, journal, volume ----------------------------------------------------------------------------------

    /** The position of the tag: a change reaches the library with a change of track, a pause or the end. */
    private fun savePosition(seconds: Double, tell: Boolean, done: Boolean = false) {
        val uid = uid ?: return
        val album = album ?: return
        val value = json(
            "tag-uid" to uid, "target-id" to album, "track-id" to track, "track-time" to Math.round(seconds * 10) / 10.0,
            "target-type" to "album", "album-finished" to done,
        )
        sync.data.add("position", uid, value)
        if (tell) loop.wake()
    }

    private val update = object : Runnable {
        override fun run() {
            if (play != Play.PLAY || album == null) return
            savePosition(player.currentPosition / 1000.0, tell = false)
            publish()
            handler.postDelayed(this, UPDATE_MS)
        }
    }

    private fun scheduleUpdate() {
        handler.removeCallbacks(update)
        handler.postDelayed(update, UPDATE_MS)
    }

    /** A journal line every ten minutes while something plays: an app that is merely installed counts as off. */
    private val alive = object : Runnable {
        override fun run() {
            if (state != State.PLAY_ALBUM) return
            if (player.isPlaying) sync.data.journal("alive")
            handler.postDelayed(this, ALIVE_MS)
        }
    }

    private fun scheduleAlive() {
        handler.removeCallbacks(alive)
        handler.postDelayed(alive, ALIVE_MS)
    }

    private fun note(event: String, vararg fields: Pair<String, Any?>) {
        sync.data.journal(event, fields.filter { it.second != null }.toMap())
    }

    private fun control(action: String) = note("control", "album" to album, "action" to action, "by" to "button")

    /** Speaker or headphones: what the phone plays on now. */
    fun output(): String {
        val headphones = setOf(
            AudioDeviceInfo.TYPE_WIRED_HEADPHONES, AudioDeviceInfo.TYPE_WIRED_HEADSET, AudioDeviceInfo.TYPE_BLUETOOTH_A2DP,
            AudioDeviceInfo.TYPE_USB_HEADSET,
        ) + (if (Build.VERSION.SDK_INT >= 31) setOf(AudioDeviceInfo.TYPE_BLE_HEADSET) else emptySet())
        return if (audio.getDevices(AudioManager.GET_DEVICES_OUTPUTS).any { it.type in headphones }) "headphones" else "speaker"
    }

    /** The app plays with at most max-volume of the settings of this device, of what the phone is set to. */
    private fun volumeLimit(): Float {
        val limits = sync.data.working("setting")["volume.${output()}"]
        return ((limits?.get("max-volume").int ?: 100).coerceIn(0, 100)) / 100f
    }

    private fun limitVolume() = later { if (::player.isInitialized) player.volume = volumeLimit() }

    // --- what the screen shows ---------------------------------------------------------------------------------------

    private fun mediaItem(uri: Uri, name: String): MediaItem {
        val value = target
        val title = title(value)
        val cover = cover(value)
        return MediaItem.Builder().setUri(uri).setMediaMetadata(
            MediaMetadata.Builder().setTitle(title).setArtist(if (radio) null else name.substringAfterLast('/').substringBeforeLast('.'))
                .setArtworkUri(cover?.let { Uri.fromFile(it) }).build(),
        ).build()
    }

    fun title(value: JsonObject?): String? =
        value?.get("title").string ?: value?.get("name").string ?: value?.get("album-folder").string?.substringAfterLast('/')

    fun cover(value: JsonObject?): File? = value?.get("cover").string?.let { sync.images.file(it) }

    private fun set(new: State, text: Int? = null) {
        state = new
        message = text
        if (new != State.PLAY_ALBUM) play = Play.NONE
        publish()
    }

    private fun band(): Band = when (state) {
        State.WAIT_FOR_TAG -> Band.GREEN
        State.CHECK_TAG -> Band.AQUA
        State.UNMAPPED_TAG, State.ELSEWHERE -> Band.YELLOW
        State.UNKNOWN_ALBUM -> Band.YELLOW_RED
        State.FETCH_ALBUM -> Band.FLOW
        State.BROKEN_ALBUM -> Band.RED_BLACK
        State.PLAY_ALBUM -> when (play) {
            Play.PLAY, Play.NONE -> Band.BLUE
            Play.PAUSED -> Band.WHITE_BLUE
            Play.WAIT -> Band.FLOW
            Play.FINISHED -> Band.BLUE_BLINK
        }
    }

    private fun publish() {
        val playing = if (::player.isInitialized) player.isPlaying || player.playWhenReady && play == Play.PLAY else false
        val text = message ?: if (play == Play.FINISHED) R.string.finished else null
        snapshot = Snapshot(
            state, play, band(), uid, title(target), cover(target), track, tracks.size, radio,
            if (::player.isInitialized && (play == Play.PLAY || play == Play.PAUSED)) player.currentPosition else 0,
            SystemClock.elapsedRealtime(), playing, text,
        )
        val current = snapshot
        main.post { synchronized(listeners) { listeners.toList() }.forEach { it(current) } }
    }

    companion object {
        const val REWIND_MAX_SECONDS = 10.0
        const val UPDATE_MS = 5_000L
        const val ALIVE_MS = 600_000L
        const val LOOKUP_MS = 3_000L
    }
}
