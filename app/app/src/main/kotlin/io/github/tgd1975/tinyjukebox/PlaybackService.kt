package io.github.tgd1975.tinyjukebox

import android.app.PendingIntent
import android.content.Intent
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.Player
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService

/**
 * Plays with the screen off: a foreground service with a media session - the lock screen, the notification and the
 * buttons of a headset. The buttons go to the jukebox, as the buttons of a box do: play/pause, next, previous.
 */
class PlaybackService : MediaSessionService() {
    private var session: MediaSession? = null

    override fun onCreate() {
        super.onCreate()
        val jukebox = TinyJukeboxApp.of(this).jukebox
        val player = object : ForwardingPlayer(jukebox.player) {
            override fun getAvailableCommands(): Player.Commands = super.getAvailableCommands().buildUpon()
                .addAll(Player.COMMAND_PLAY_PAUSE, Player.COMMAND_SEEK_TO_NEXT, Player.COMMAND_SEEK_TO_PREVIOUS)
                .build()

            override fun isCommandAvailable(command: Int): Boolean = availableCommands.contains(command)

            override fun play() = jukebox.playPause().let { }

            override fun pause() = jukebox.playPause().let { }

            override fun setPlayWhenReady(playWhenReady: Boolean) {
                if (playWhenReady != jukebox.player.playWhenReady) jukebox.playPause()
            }

            override fun seekToNext() = jukebox.next().let { }

            override fun seekToNextMediaItem() = jukebox.next().let { }

            override fun seekToPrevious() = jukebox.previous().let { }

            override fun seekToPreviousMediaItem() = jukebox.previous().let { }
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        session = MediaSession.Builder(this, player).setSessionActivity(open).build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        // swiped away while nothing plays: the service ends; while something plays it goes on, as every player
        if (session?.player?.playWhenReady != true) stopSelf()
    }

    override fun onDestroy() {
        session?.run {
            release()
        }
        session = null
        super.onDestroy()
    }
}
