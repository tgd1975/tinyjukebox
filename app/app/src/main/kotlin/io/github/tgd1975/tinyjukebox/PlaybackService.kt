package io.github.tgd1975.tinyjukebox

import android.app.PendingIntent
import android.content.Intent
import androidx.media3.common.ForwardingPlayer
import androidx.media3.common.Player
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import androidx.media3.session.SessionCommands

/**
 * Plays with the screen off: a foreground service with a media session - the lock screen, the notification and the
 * buttons of a headset. The buttons go to the jukebox, as the buttons of a box do: play/pause, next, previous.
 *
 * Every app may connect to the session (the lock screen, a headset, a watch, a car). It may press these buttons and read
 * what plays - not set the volume, another track or a place in it: the volume limit and the positions are the
 * jukebox's (app.md, section 3).
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

            // "stop" of a headset or a car: a pause, as with the button - only the jukebox stops its player
            override fun stop() {
                if (jukebox.snapshot.playing) jukebox.playPause()
            }
        }
        val open = PendingIntent.getActivity(
            this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val callback = object : MediaSession.Callback {
            override fun onConnect(session: MediaSession, controller: MediaSession.ControllerInfo): MediaSession.ConnectionResult =
                MediaSession.ConnectionResult.accept(SessionCommands.EMPTY, ALLOWED)
        }
        session = MediaSession.Builder(this, player).setSessionActivity(open).setCallback(callback).build()
    }

    override fun onGetSession(controllerInfo: MediaSession.ControllerInfo): MediaSession? = session

    override fun onTaskRemoved(rootIntent: Intent?) {
        // swiped away while nothing plays: the service ends; while something plays it goes on, as every player. The
        // player may be asked on the thread of the jukebox only: what the jukebox showed last is asked instead
        if (!TinyJukeboxApp.of(this).jukebox.active()) stopSelf()
    }

    override fun onDestroy() {
        session?.run {
            release()
        }
        session = null
        super.onDestroy()
    }

    companion object {
        /** The commands of the player that a controller is given: the buttons of a box, and what the lock screen and
         * the notification show. */
        val ALLOWED: Player.Commands = Player.Commands.Builder().addAll(
            Player.COMMAND_PLAY_PAUSE, Player.COMMAND_STOP,
            Player.COMMAND_SEEK_TO_NEXT, Player.COMMAND_SEEK_TO_NEXT_MEDIA_ITEM,
            Player.COMMAND_SEEK_TO_PREVIOUS, Player.COMMAND_SEEK_TO_PREVIOUS_MEDIA_ITEM,
            Player.COMMAND_GET_CURRENT_MEDIA_ITEM, Player.COMMAND_GET_TIMELINE, Player.COMMAND_GET_METADATA,
            Player.COMMAND_GET_AUDIO_ATTRIBUTES, Player.COMMAND_GET_VOLUME, Player.COMMAND_GET_DEVICE_VOLUME,
            Player.COMMAND_GET_TRACKS,
        ).build()
    }
}
