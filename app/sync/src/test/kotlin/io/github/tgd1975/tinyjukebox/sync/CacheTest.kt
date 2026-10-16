package io.github.tgd1975.tinyjukebox.sync

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

/** The limit of the cache: the album played longest ago leaves first, never the one that plays. */
class CacheTest {
    @TempDir
    lateinit var folder: File

    private var playing: String? = null

    private fun cache(): Cache {
        val data = AppData(File(folder, "sync"), "bphone01", "app-0.40.0")
        val entries = (1..3).associate { id ->
            "$id" to json("value" to json("target-id" to "$id", "target-type" to "album", "album-folder" to "album$id", "playlist" to "p.m3u"), "rev" to 1)
        }
        data.replaceCopy("targets.json", json("entries" to entries).toString().toByteArray())
        val cache = Cache(data, File(folder, "albums"), File(folder, "sync"), 2500, { null }, { playing })
        for (id in 1..3) {
            val file = File(folder, "albums/album$id/1.mp3").apply { parentFile.mkdirs(); writeBytes(ByteArray(1000)) }
            val manifest = Manifest(listOf(Manifest.Entry("1.mp3", 1000, sha256(file))), listOf("1.mp3"))
            writeJson(File(folder, "sync/manifests/$id.json"), manifest.toJson())
        }
        return cache
    }

    @Test
    fun `the album played longest ago leaves`() {
        val cache = cache()
        cache.played("2")
        cache.played("1")
        cache.played("3")
        cache.makeRoom(500, keep = "3")
        assertFalse(cache.complete("2"))
        assertTrue(cache.complete("1"))
        assertTrue(cache.complete("3"))
        assertEquals(2000, cache.used())
    }

    @Test
    fun `never the album that plays`() {
        val cache = cache()
        cache.played("1")
        cache.played("2")
        cache.played("3")
        playing = "1"
        cache.makeRoom(1500, keep = "3")
        assertTrue(cache.complete("1"))
        assertFalse(cache.complete("2"))
        assertTrue(cache.complete("3"))
    }

    @Test
    fun `the manifest of the library and the files that are here`() {
        val cache = cache()
        assertTrue(cache.ready("1", 0))
        File(folder, "albums/album1/1.mp3").delete()
        assertEquals(listOf("1.mp3"), cache.lacking("1"))
        assertFalse(cache.ready("1", 0))
        assertEquals(null, cache.lacking("9"))
    }
}
