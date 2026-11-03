package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonObject
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertThrows
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

    // --- damaged files (as tests/sync/test_recovery.py does it for a box) ----------------------------------------------

    private fun journalLine(n: Int, event: String = "track") = json(
        "n" to n, "start" to 1, "uptime" to n.toDouble(), "clock" to json("time" to "2026-10-17T10:00:00Z", "synchronized" to true),
        "version" to "app-0.40.0", "event" to event,
    )

    /** The journal as an app that was ended left it: [lines], then [tail]; [reserved]: the last number in numbers.json. */
    private fun writeJournal(lines: List<JsonObject>, tail: ByteArray = ByteArray(0), reserved: Int = lines.last()["n"].int!!) {
        File(folder, "journal").writeBytes(lines.joinToString("") { "$it\n" }.toByteArray() + tail)
        File(folder, "numbers.json").writeText("""{"journal": $reserved, "outbox": 0, "start": 1}""")
    }

    private fun numbersAndEvents(data: AppData) = data.journalLines(Int.MAX_VALUE).map { it["n"].int to it["event"].string }

    @Test
    fun `a journal with a line of zeros keeps every complete line`() {
        writeJournal(listOf(journalLine(912), journalLine(913)), tail = ByteArray(405), reserved = 915)
        val logged = mutableListOf<String>()
        val data = AppData(folder, "bphone01", "app-0.40.0") { logged += it }
        assertEquals(listOf(912 to "track", 913 to "track", 914 to RECOVERY_GAP, 915 to RECOVERY_GAP), numbersAndEvents(data))
        assertEquals(listOf("journal: 1 damaged line(s); lines lost: 914-915 - noted as recovery_gap"), logged)
        // the file is valid JSON lines again, and the next line follows the numbers that were reserved
        data.journal("start")
        val lines = File(folder, "journal").readLines().map { JSON.parseToJsonElement(it).obj!! }
        assertEquals(listOf(912, 913, 914, 915, 916), lines.map { it["n"].int })
    }

    @Test
    fun `a number without its line becomes a gap`() {
        // the number was saved, then the app was ended before its line reached the storage
        writeJournal(listOf(journalLine(1), journalLine(3)), reserved = 4)
        val data = AppData(folder, "bphone01", "app-0.40.0")
        assertEquals(listOf(1 to "track", 2 to RECOVERY_GAP, 3 to "track", 4 to RECOVERY_GAP), numbersAndEvents(data))
        val gap = data.journalLines()[1]
        assertEquals(2 to "app-0.40.0", gap["start"].int to gap["version"].string) // written by this start
    }

    @Test
    fun `a line cut off at the end is left out, and the next one is a line of its own`() {
        writeJournal(listOf(journalLine(1), journalLine(2)), tail = """{"n": 3, "start": 1, "upt""".toByteArray(), reserved = 3)
        val data = AppData(folder, "bphone01", "app-0.40.0")
        assertEquals(listOf(1 to "track", 2 to "track", 3 to RECOVERY_GAP), numbersAndEvents(data))
        data.journal("alive")
        assertEquals(listOf(1, 2, 3, 4), readLines(File(folder, "journal")).first.map { it["n"].int })
        // cut off while the app runs (the storage was full): the next line does not stick to it
        val file = File(folder, "lines")
        file.writeText("{\"n\": 1}\n{\"n\": 2, \"st")
        appendLine(file, json("n" to 3))
        val (lines, damaged) = readLines(file)
        assertEquals(listOf(1, 3), lines.map { it["n"].int })
        assertEquals(1, damaged)
    }

    @Test
    fun `a complete journal is left as it is`() {
        writeJournal(listOf(journalLine(1), journalLine(2)))
        val before = File(folder, "journal").readBytes()
        val data = AppData(folder, "bphone01", "app-0.40.0")
        assertEquals(listOf(1 to "track", 2 to "track"), numbersAndEvents(data))
        assertTrue(before.contentEquals(File(folder, "journal").readBytes()))
    }

    @Test
    fun `a damaged outbox keeps every complete change`() {
        val change = json("n" to 7, "op" to "set", "kind" to "position", "key" to "04:01", "value" to position("04:01", 2), "base" to 0)
        val other = json("n" to 8, "op" to "set", "kind" to "position", "key" to "04:02", "value" to position("04:02", 5), "base" to 0)
        File(folder, "outbox").writeBytes("$change\n[1, 2]\n\u0000\u0000\u0000\n$other\n{\"n\": 9, \"op\"".toByteArray() + byteArrayOf(-1, -2, 10))
        File(folder, "numbers.json").writeText("""{"journal": 0, "outbox": 9, "start": 1}""")
        val logged = mutableListOf<String>()
        val data = AppData(folder, "bphone01", "app-0.40.0") { logged += it }
        assertEquals(listOf(7, 8), data.outbox().map { it["n"].int })
        assertEquals(5, data.working("position").getValue("04:02")["track-id"].int)
        assertEquals(listOf("outbox: 3 damaged change(s) of this phone left out - they do not reach the library"), logged)
        assertEquals(listOf(7, 8), File(folder, "outbox").readLines().map { JSON.parseToJsonElement(it).obj!!["n"].int })
        data.add("position", "04:03", position("04:03", 1))
        assertEquals(listOf(7, 8, 10), AppData(folder, "bphone01", "app-0.40.0").outbox().map { it["n"].int })
    }

    @Test
    fun `a copy that cannot be read is fetched again`() {
        File(folder, "copy").mkdirs()
        File(folder, "copy/targets.json").writeText("""{"entries": {"1": {"value": {"target-id": "1"""")
        File(folder, "copy/box.json").writeText("[]")
        val data = AppData(folder, "bphone01", "app-0.40.0")
        assertTrue(data.working("target").isEmpty())
        assertEquals(sha256(ByteArray(0)), data.checksum("targets.json")) // differs from the library's: delivered again
        assertTrue(data.working("position").isEmpty())
        assertThrows(IllegalArgumentException::class.java) { data.replaceCopy("tags.json", "[1]".toByteArray()) }
    }

    @Test
    fun `lines the library waits for and the phone does not have become gaps`() {
        val data = AppData(folder, "bphone01", "app-0.40.0")
        repeat(6) { data.journal("track") }
        data.journalConfirmed(4) // lines 5 and 6 are left; the library has 2 - it was restored from a backup
        data.journalGap(2)
        assertEquals(listOf(3 to RECOVERY_GAP, 4 to RECOVERY_GAP, 5 to "track", 6 to "track"), numbersAndEvents(data))
        data.journalGap(2) // nothing is missing any more
        assertEquals(4, data.journalLines().size)
        assertEquals(listOf(3, 4, 5, 6), AppData(folder, "bphone01", "app-0.40.0").journalLines().map { it["n"].int })
    }
}
