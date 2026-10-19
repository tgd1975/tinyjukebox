package io.github.tgd1975.tinyjukebox.sync

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

/** The files of the app, in the same form as on a box (docs/developer/app.md, section 6). */
class AppDataTest {
    @TempDir
    lateinit var folder: File

    private fun position(uid: String, track: Int) = json(
        "tag-uid" to uid, "target-id" to "1", "track-id" to track, "track-time" to 3.5,
        "target-type" to "album", "album-finished" to false,
    )

    @Test
    fun `the outbox lies on top of the copy and survives a restart`() {
        val data = AppData(folder, "bphone01", "app-0.40.0")
        data.replaceCopy(STATE, """{"format": 1, "positions": {"04:01": {"value": ${position("04:01", 1)}, "rev": 3}}, "settings": {"name": {"value": "Pixel", "rev": 1}}}""".toByteArray())
        data.add("position", "04:01", position("04:01", 2))
        data.add("position", "04:01", position("04:01", 4)) // replaces the one before
        val again = AppData(folder, "bphone01", "app-0.40.0")
        assertEquals(1, again.outbox().size)
        val entry = again.outbox().single()
        assertEquals(3, entry["base"].int)
        assertEquals("app-0.40.0", entry["version"].string)
        assertEquals(SOFTWARE, entry["by"].string)
        assertEquals(4, again.working("position").getValue("04:01")["track-id"].int)
        assertEquals("Pixel", again.setting("name").string)
        assertEquals(2, again.start) // every process is a start of its own
        again.confirm(setOf(entry["n"].int!!))
        assertEquals(1, again.working("position").getValue("04:01")["track-id"].int)
    }

    @Test
    fun `a position the library lost is found, a target it removed too`() {
        val data = AppData(folder, "bphone01", "app-0.40.0")
        data.replaceCopy(STATE, """{"positions": {"04:01": {"value": ${position("04:01", 2)}, "rev": 5}}, "settings": {}}""".toByteArray())
        val lost = data.replaceCopy(STATE, """{"positions": {}, "settings": {}}""".toByteArray()).lost
        assertEquals(listOf("04:01" to 5), lost.map { it.first to it.third })
        data.replaceCopy("targets.json", """{"entries": {"1": {"value": {"target-id": "1", "target-type": "album", "album-folder": "a"}, "rev": 1}}}""".toByteArray())
        val removed = data.replaceCopy("targets.json", """{"entries": {"1": {"value": null, "rev": 2}}}""".toByteArray()).removed
        assertEquals("a", removed.single()["album-folder"].string)
        assertTrue(data.working("target").isEmpty())
    }

    @Test
    fun `the journal is numbered and confirmed`() {
        val data = AppData(folder, "bphone01", "app-0.40.0")
        data.journal("start")
        data.journal("tag", mapOf("uid" to "04:01", "kind" to "album", "target" to "1", "output" to "speaker"))
        val lines = data.journalLines()
        assertEquals(listOf(1, 2), lines.map { it["n"].int })
        assertEquals("speaker", lines[1]["output"].string)
        assertEquals(true, lines[1]["clock"].obj!!["synchronized"].boolean)
        data.journalConfirmed(1)
        assertEquals(listOf(2), AppData(folder, "bphone01", "app-0.40.0").journalLines().map { it["n"].int })
    }

    @Test
    fun `an unknown tag as a box creates it`() {
        val tag = AppData.unknownTag("04:AA:BB:CC:DD:EE:01")
        assertTrue(AppData.unlinked(tag["target"]))
        assertTrue(Regex("""\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z""").matches(tag["created"].string!!))
    }
}
