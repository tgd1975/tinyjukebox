package io.github.tgd1975.tinyjukebox.sync

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

/** A pass never ends the app: whatever the library answers, the app works alone and tries again with the next pass. */
class SyncTest {
    @TempDir
    lateinit var folder: File

    private val certificate = JSON.parseToJsonElement(javaClass.getResource("/handshake.json")!!.readText()).obj!!["certificate"].string!!

    /** What the stand-in delivers as targets.json. */
    private var targets = """{"format": 1, "entries": {}}"""

    private fun library() = StandIn { request ->
        when (request.path) {
            "/api/sync/interface" -> StandIn.Answer(200, """{"serves": [$INTERFACE], "library": "0.44.0"}""")
            "$V/catalog" -> StandIn.Answer(200, """{"files": {"targets.json": {"sha256": "${sha256(targets.toByteArray())}"}}}""")
            "$V/catalog/targets.json" -> StandIn.Answer(200, targets)
            "$V/boxes/bphone01/state" -> StandIn.Answer(200, """{"positions": {}, "settings": {}}""")
            "$V/boxes/bphone01/status" -> StandIn.Answer(200, "{}")
            else -> StandIn.Answer(404, """{"error": "unknown"}""")
        }
    }

    /** The app, paired with [library]: plain HTTP, so the certificate is not asked for. */
    private fun sync(library: StandIn): Sync {
        val identity = File(folder, "identity").apply { mkdirs() }
        File(identity, "box-id").writeText("bphone01\n")
        File(identity, "library").writeText(library.url + "\n")
        File(identity, "key").writeText("key-of-the-tests\n")
        File(identity, "library.crt").writeText(certificate)
        return syncOf(folder, "0.44.0")
    }

    @Test
    fun `an answer that is no JSON`() {
        StandIn { StandIn.Answer(200, "<html>a captive portal</html>".toByteArray()) }.use { library ->
            val sync = sync(library)
            assertFalse(sync.pass())
            assertFalse(sync.connected)
            assertTrue(sync.alone.startsWith("the pass failed"), sync.alone)
        }
    }

    @Test
    fun `a file of the catalog that is no object never becomes the copy, and the next pass goes on`() {
        library().use { library ->
            val sync = sync(library)
            targets = "[1, 2]"
            assertFalse(sync.pass())
            assertTrue("targets.json is not what the library delivers" in sync.alone, sync.alone)
            assertEquals(sha256(ByteArray(0)), sync.data.checksum("targets.json"))
            targets = """{"format": 1, "entries": {"7": {"value": {"target-id": "7", "target-type": "radio", "name": "R", "url": "http://r"}, "rev": 1}}}"""
            while (sync.pass()) Unit
            assertTrue(sync.connected, sync.alone)
            assertEquals("R", sync.data.working("target").getValue("7")["name"].string)
        }
    }
}
