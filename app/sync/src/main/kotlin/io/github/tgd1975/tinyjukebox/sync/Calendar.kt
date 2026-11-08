package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonObject
import java.time.LocalDate

/**
 * The figures of an advent calendar of Spotify on the phone, with the rules of a box (data/opendays.py,
 * stm/playspotify.py; docs/developer/architecture.md, section 4.2).
 *
 * - Day n of a locked calendar opens on n December of its year, by the date of the phone. A day once open stays open:
 *   the phone keeps the highest day it opened per calendar ([Kept]).
 * - `chapters`: the figure of day n plays the n-th track of the album, from its own position; `growing`: all figures
 *   share the position of the calendar ([key]).
 */
object Calendar {
    const val DAYS = 24

    /** The highest day a phone has opened of a calendar, for its year. */
    data class Kept(val year: Int, val day: Int)

    /** The key of the one position of a growing calendar - no uid of a tag looks like it. */
    fun key(targetId: String) = "calendar:$targetId"

    fun growing(calendar: JsonObject) = calendar["mode"].string == "growing"

    fun locked(calendar: JsonObject) = calendar["locked"].boolean != false

    /** The highest day the date has opened: 0 before December of [year], 24 after the 24th. */
    fun reached(year: Int, today: LocalDate): Int = when {
        today.isBefore(LocalDate.of(year, 12, 1)) -> 0
        !today.isBefore(LocalDate.of(year, 12, DAYS)) -> DAYS
        else -> today.dayOfMonth
    }

    /**
     * Whether the figure of [day] may play on [today]; [kept] what the phone opened before. Returns the answer and what
     * the phone keeps from now on (null: nothing new to keep).
     */
    fun open(calendar: JsonObject, day: Int, today: LocalDate, kept: Kept?): Pair<Boolean, Kept?> {
        if (!locked(calendar)) return true to null
        val year = calendar["year"].int ?: return false to null
        val reached = reached(year, today)
        val before = if (kept?.year == year) kept.day else 0
        val keep = if (reached > before) Kept(year, reached) else null
        return (day <= maxOf(reached, before)) to keep
    }
}
