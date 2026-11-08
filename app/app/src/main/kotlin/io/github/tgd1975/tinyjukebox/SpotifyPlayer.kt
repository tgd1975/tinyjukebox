package io.github.tgd1975.tinyjukebox

import android.content.Context
import android.os.Handler
import android.os.SystemClock
import android.util.Log
import com.spotify.android.appremote.api.ConnectionParams
import com.spotify.android.appremote.api.Connector
import com.spotify.android.appremote.api.SpotifyAppRemote
import com.spotify.android.appremote.api.error.AuthenticationFailedException
import com.spotify.android.appremote.api.error.CouldNotFindSpotifyApp
import com.spotify.android.appremote.api.error.UserNotAuthorizedException
import com.spotify.protocol.client.Subscription
import com.spotify.protocol.mappers.gson.GsonMapper
import com.spotify.protocol.types.PlayerContext
import com.spotify.protocol.types.PlayerState

/**
 * An entry of Spotify on the phone (docs/developer/app.md, section 3): it plays in the Spotify app of the
 * phone, steered with Spotify's App Remote - as the box plays it with Soloist (tinyjukebox/albumplayer/spotifyplayer.py).
 *
 * - **Start:** shuffle and repeat off; the entry at the saved place of its tracks, then the saved second. If the track
 *   there is another one than the saved one, the entry starts from the beginning.
 * - **The place of the track** follows from what the app asked for: next or the end of a track goes on by one,
 *   previous goes back by one.
 * - **The end:** Spotify plays something else by itself (autoplay: another context): the player pauses - finished.
 * - **A chapter of a calendar** ([single]): only the track at [first] plays - at its end, and with "next", the player is
 *   finished; "previous" goes to the beginning of the track, "previous" held to the beginning of the chapter.
 * - **Onward** (a growing calendar that was finished at the saved track): one track further; if Spotify plays what it
 *   added itself (autoplay), back to the saved track from its beginning.
 *
 * Everything is called and calls back on [handler], the thread of the jukebox.
 */
class SpotifyPlayer(
    private val context: Context,
    private val clientId: String,
    private val uri: String,
    private val handler: Handler,
    private val listener: Listener,
    private val first: Int = 0,
    private val single: Boolean = false,
) {
    data class Track(val index: Int, val uri: String?, val title: String?)

    interface Listener {
        /** Playing or paused - also when the account plays elsewhere or another app took the sound. */
        fun playing(playing: Boolean)

        /** A track was left: listened for [seconds], to its end or not. */
        fun trackLeft(track: Track, seconds: Double, toEnd: Boolean)

        fun finished()

        /** It cannot play: [elsewhere] means without the Spotify app or the registration - the tag plays on a box. */
        fun failed(reason: String, elsewhere: Boolean)
    }

    private var remote: SpotifyAppRemote? = null
    private var states: Subscription<PlayerState>? = null
    private var contexts: Subscription<PlayerContext>? = null
    private var track = Track(0, null, null)
    private var duration = 0L
    private var position = 0L
    private var positionAt = 0L
    private var paused = true
    private var began = 0L // position at which the playback of this track began
    private var step = 1 // what the next new track means: +1 (next, the end of a track) or -1 (previous)
    private var starting: Pair<Track, Double>? = null // the saved place, until the first state arrives
    private var stopped = false
    private var done = false
    private var onward: Track? = null // looking whether a track follows this one (a growing calendar)
    private var candidate: Track? = null // ... the track that followed, unless Spotify added it itself

    /** Connect and play from track [saved] at [seconds] - [onward]: one track further if the entry has one now. */
    fun start(saved: Track, seconds: Double, onward: Boolean = false) {
        this.onward = if (onward && saved.uri != null) saved else null
        starting = saved to seconds
        track = saved
        val params = ConnectionParams.Builder(clientId).setRedirectUri(REDIRECT).showAuthView(true)
            .setJsonMapper(GsonMapper.create()).build()
        SpotifyAppRemote.connect(context, params, object : Connector.ConnectionListener {
            override fun onConnected(appRemote: SpotifyAppRemote) = post { connected(appRemote) }

            override fun onFailure(error: Throwable) = post {
                Log.w(TinyJukeboxApp.TAG, "Spotify: $error")
                val elsewhere = error is CouldNotFindSpotifyApp || error is UserNotAuthorizedException ||
                    error is AuthenticationFailedException
                if (!stopped) listener.failed(error.message ?: error.toString(), elsewhere)
            }
        })
    }

    private fun post(action: () -> Unit) {
        handler.post(action)
    }

    private fun connected(appRemote: SpotifyAppRemote) {
        if (stopped) return SpotifyAppRemote.disconnect(appRemote)
        remote = appRemote
        val player = appRemote.playerApi
        player.setShuffle(false)
        player.setRepeat(0)
        val (saved, _) = starting ?: return
        if (saved.index > 0) {
            player.skipToIndex(uri, saved.index).setErrorCallback { post { player.play(uri) } }
        } else {
            player.play(uri)
        }
        states = player.subscribeToPlayerState().setEventCallback { state -> post { state(state) } }
            as Subscription<PlayerState>
        contexts = player.subscribeToPlayerContext().setEventCallback { context -> post { context(context) } }
            as Subscription<PlayerContext>
    }

    private fun state(state: PlayerState) {
        if (stopped) return
        val current = state.track ?: return
        val start = starting
        if (start != null) {
            // the first state of the entry: the saved place, or the beginning if the track there is another one
            val (saved, seconds) = start
            starting = null
            if (saved.uri != null && saved.index > 0 && current.uri != saved.uri) {
                track = Track(0, null, null)
                remote?.playerApi?.skipToIndex(uri, 0)?.setErrorCallback { post { remote?.playerApi?.play(uri) } }
                return
            }
            track = Track(saved.index, current.uri, current.name)
            if (seconds > 0) remote?.playerApi?.seekTo((seconds * 1000).toLong())
            began = (seconds * 1000).toLong()
            if (onward != null) {
                remote?.playerApi?.skipNext()
                handler.postDelayed({ nothingNew() }, ONWARD_MS)
            }
        } else if (onward != null && current.uri != track.uri) {
            // a track follows the saved one: the new chapter - unless the context says that Spotify added it
            if (candidate == null) handler.postDelayed({ acceptOnward() }, CONTEXT_MS)
            candidate = Track(track.index + 1, current.uri, current.name)
            began = state.playbackPosition
        } else if (single && current.uri != track.uri) {
            // the chapter has ended: the next track belongs to another day
            return finishChapter()
        } else if (current.uri != track.uri) {
            // another track: the one before was left
            listener.trackLeft(track, (position - began) / 1000.0, step > 0 && position >= duration - END_TOLERANCE_MS)
            track = Track(maxOf(0, track.index + step), current.uri, current.name)
            step = 1
            began = state.playbackPosition
        }
        duration = current.duration
        position = state.playbackPosition
        positionAt = SystemClock.elapsedRealtime()
        if (state.isPaused != paused) {
            paused = state.isPaused
            listener.playing(!paused)
        }
    }

    /** Onward: nothing of the entry follows the saved track - it plays again from its beginning. */
    private fun nothingNew() {
        val saved = onward ?: return
        if (candidate != null) return // a track came: acceptOnward() decides
        onward = null
        track = saved
        remote?.playerApi?.skipToIndex(uri, saved.index)
    }

    /** Onward: the track that followed is of the entry itself. */
    private fun acceptOnward() {
        val next = candidate ?: return
        if (onward == null || stopped) return
        onward = null
        candidate = null
        track = next
    }

    private fun finishChapter() {
        if (done) return
        done = true
        remote?.playerApi?.pause()
        listener.trackLeft(track, (position - began) / 1000.0, true)
        listener.finished()
    }

    private fun context(playing: PlayerContext) {
        // Spotify plays something else by itself after the last track (autoplay): the entry is finished
        if (stopped || done || starting != null || playing.uri.isNullOrEmpty() || uri.contains(":track:")) return
        if (playing.uri != uri && onward != null) { // Spotify added what followed: nothing new after the saved track
            candidate = null
            return nothingNew()
        }
        if (playing.uri != uri) {
            done = true
            remote?.playerApi?.pause()
            listener.trackLeft(track, (position - began) / 1000.0, true)
            listener.finished()
        }
    }

    /** The place in the entry now: the track and the second in it. */
    fun position(): Pair<Track, Double> {
        val moved = if (paused) 0 else SystemClock.elapsedRealtime() - positionAt
        return track to (position + moved) / 1000.0
    }

    val connected: Boolean get() = remote?.isConnected == true && starting == null

    fun pause() {
        remote?.playerApi?.pause()
    }

    fun resume() {
        remote?.playerApi?.resume()
    }

    fun next() {
        if (single) return finishChapter() // the next track is the next day
        step = 1
        remote?.playerApi?.skipNext()
    }

    fun previous() {
        if (single) { // the track before is the day before
            remote?.playerApi?.seekTo(0)
            return
        }
        step = -1
        remote?.playerApi?.skipPrevious()
    }

    /** "Previous" held: the entry from the beginning - a chapter from its beginning. */
    fun restart() {
        val (current, seconds) = position()
        if (!done) listener.trackLeft(current, seconds - began / 1000.0, false)
        done = false
        onward = null
        candidate = null
        track = Track(first, null, null)
        starting = Track(first, null, null) to 0.0
        remote?.playerApi?.skipToIndex(uri, first)?.setErrorCallback { post { remote?.playerApi?.play(uri) } }
    }

    /** The tag is taken away: the track that plays is left, the Spotify app pauses. */
    fun stop() {
        if (stopped) return
        if (remote != null && starting == null && !done) {
            val (current, seconds) = position()
            listener.trackLeft(current, seconds - began / 1000.0, false)
        }
        stopped = true
        remote?.playerApi?.pause()
        states?.cancel()
        contexts?.cancel()
        remote?.let { SpotifyAppRemote.disconnect(it) }
        remote = null
    }

    companion object {
        /** The redirect URI the app is registered with at Spotify. */
        const val REDIRECT = "tinyjukebox://spotify"

        /** A track counts as played to its end if no more than this is left. */
        const val END_TOLERANCE_MS = 3_000L

        /** Onward: how long the player waits for the track after the saved one. */
        const val ONWARD_MS = 3_000L

        /** Onward: how long the player waits for the context to say that Spotify added the track that followed. */
        const val CONTEXT_MS = 1_500L

        fun installed(context: Context) = SpotifyAppRemote.isSpotifyInstalled(context)
    }
}
