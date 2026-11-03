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

    /** Bytes the storage of the phone has; free is what the albums leave of it. */
    private var storage = Long.MAX_VALUE / 2

    private lateinit var data: AppData

    private fun cache(limit: Long = 2500): Cache {
        data = AppData(File(folder, "sync"), "bphone01", "app-0.40.0")
        catalog(1..3)
        lateinit var cache: Cache
        cache = Cache(data, File(folder, "albums"), File(folder, "sync"), limit, { null }, { playing }, free = { storage - cache.used() })
        for (id in 1..3) {
            val file = File(folder, "albums/album$id/1.mp3").apply { parentFile.mkdirs(); writeBytes(ByteArray(1000)) }
            val manifest = Manifest(listOf(Manifest.Entry("1.mp3", 1000, sha256(file))), listOf("1.mp3"))
            writeJson(File(folder, "sync/manifests/$id.json"), manifest.toJson())
        }
        return cache
    }

    /** The catalog has the albums [ids], album<id> each. */
    private fun catalog(ids: IntRange) {
        val entries = ids.associate { id ->
            "$id" to json("value" to json("target-id" to "$id", "target-type" to "album", "album-folder" to "album$id", "playlist" to "p.m3u"), "rev" to 1)
        }
        data.replaceCopy("targets.json", json("entries" to entries).toString().toByteArray())
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
    fun `the storage of the phone keeps its free space, whatever the limit`() {
        storage = Cache.KEEP_FREE + 3500 // 500 bytes over what stays free while the three albums are here
        val cache = cache(limit = 1L shl 40)
        cache.played("2")
        cache.played("1")
        cache.played("3")
        cache.makeRoom(1000, keep = "3")
        assertFalse(cache.complete("2"))
        assertTrue(cache.complete("1"))
        assertTrue(cache.complete("3"))
    }

    @Test
    fun `folders that no album of the catalog names leave, not the one that plays`() {
        val cache = cache(limit = 1L shl 40)
        File(folder, "albums/renamed/1.mp3").apply { parentFile.mkdirs(); writeBytes(ByteArray(700)) }
        File(folder, "albums/artist/album4/1.mp3").apply { parentFile.mkdirs(); writeBytes(ByteArray(10)) }
        File(folder, "albums/artist/gone/1.mp3").apply { parentFile.mkdirs(); writeBytes(ByteArray(10)) }
        playing = "3"
        assertTrue(cache.file("3", "1.mp3")!!.isFile) // the jukebox finds the track it plays
        // the library names another folder for album 4, removes album 3 while it plays and renames album 1
        val entries = mapOf(
            "1" to json("value" to json("target-id" to "1", "target-type" to "album", "album-folder" to "album1-new"), "rev" to 2),
            "2" to json("value" to json("target-id" to "2", "target-type" to "album", "album-folder" to "album2"), "rev" to 1),
            "4" to json("value" to json("target-id" to "4", "target-type" to "album", "album-folder" to "artist/album4"), "rev" to 1),
        )
        data.replaceCopy("targets.json", json("entries" to entries).toString().toByteArray())
        cache.makeRoom(100, keep = "4")
        assertFalse(File(folder, "albums/renamed").exists())
        assertFalse(File(folder, "albums/artist/gone").exists())
        assertFalse(File(folder, "albums/album1").exists())
        assertTrue(File(folder, "sync/manifests/1.json").exists()) // album 1 is in the catalog: loaded into its new folder
        assertTrue(File(folder, "albums/artist/album4/1.mp3").exists())
        assertTrue(cache.complete("2"))
        assertTrue(File(folder, "albums/album3/1.mp3").exists()) // plays
        assertTrue(File(folder, "sync/manifests/3.json").exists())
        playing = null
        cache.makeRoom(100, keep = "4")
        assertFalse(File(folder, "albums/album3").exists())
        assertFalse(File(folder, "sync/manifests/3.json").exists())
        assertEquals(1010, cache.used())
    }

    @Test
    fun `a complete part is checked and named, also without the library`() {
        val cache = cache()
        // the app was ended after the last byte of the file had arrived and before it got its name
        val track = File(folder, "albums/album1/1.mp3")
        assertTrue(track.renameTo(File(track.parentFile, ".1.mp3.part")))
        assertFalse(cache.ready("1", 0))
        val done = Object()
        cache.subscribe { synchronized(done) { done.notifyAll() } }
        cache.need("1", 0)
        val deadline = System.currentTimeMillis() + 10_000
        synchronized(done) {
            while ("1" in cache.fetching() && System.currentTimeMillis() < deadline) done.wait(100)
        }
        assertEquals(null, cache.failed("1"))
        assertTrue(cache.ready("1", 0))
        assertFalse(File(track.parentFile, ".1.mp3.part").exists())
    }

    @Test
    fun `an album whose files changed in the library is taken over on the thread of its loading`() {
        val old = ByteArray(1000) { 1 }
        val new = ByteArray(1200) { 2 }
        val before = Manifest(listOf(Manifest.Entry("1.mp3", 1000, sha256(ByteArray(1000))), Manifest.Entry("2.mp3", 1000, sha256(old))), listOf("1.mp3", "2.mp3"))
        val after = Manifest(listOf(Manifest.Entry("1.mp3", 1000, sha256(ByteArray(1000))), Manifest.Entry("2.mp3", 1200, sha256(new))), listOf("1.mp3", "2.mp3"))
        StandIn { request ->
            when (request.path) {
                "$V/albums/1/manifest" -> StandIn.Answer(200, after.toJson().toString())
                "$V/albums/1/files/2.mp3" -> StandIn.Answer(200, new)
                else -> StandIn.Answer(404)
            }
        }.use { library ->
            data = AppData(File(folder, "sync"), "bphone01", "app-0.40.0")
            val value = json("target-id" to "1", "target-type" to "album", "album-folder" to "album1", "playlist" to "p.m3u")
            val entry = json("value" to value, "rev" to 2, "album" to json("checksum" to after.checksum))
            data.replaceCopy("targets.json", json("entries" to mapOf("1" to entry)).toString().toByteArray())
            val cache = Cache(data, File(folder, "albums"), File(folder, "sync"), 1L shl 30, { Client(library.url) }, { null })
            File(folder, "albums/album1/1.mp3").apply { parentFile.mkdirs(); writeBytes(ByteArray(1000)) }
            File(folder, "albums/album1/2.mp3").writeBytes(old)
            writeJson(File(folder, "sync/manifests/1.json"), before.toJson())
            assertTrue(cache.outdated("1"))
            val announced = mutableListOf<List<String>?>()
            val done = Object()
            cache.subscribe { synchronized(done) { announced += cache.lacking("1"); done.notifyAll() } }
            cache.need("1", 0, renew = true)
            val deadline = System.currentTimeMillis() + 10_000
            synchronized(done) {
                while ("1" in cache.fetching() && System.currentTimeMillis() < deadline) done.wait(100)
            }
            assertEquals(null, cache.failed("1"))
            assertEquals(listOf("2.mp3"), announced.first()) // taken over: the first track may play, 2.mp3 follows
            assertTrue(new.contentEquals(File(folder, "albums/album1/2.mp3").readBytes()))
            assertFalse(cache.outdated("1"))
        }
    }

    @Test
    fun `an empty file of an album is loaded as any other`() {
        StandIn { StandIn.Answer(200) }.use { library ->
            data = AppData(File(folder, "sync"), "bphone01", "app-0.40.0")
            catalog(1..1)
            val cache = Cache(data, File(folder, "albums"), File(folder, "sync"), 2500, { Client(library.url) }, { null })
            writeJson(File(folder, "sync/manifests/1.json"), Manifest(listOf(Manifest.Entry("empty.txt", 0, sha256(ByteArray(0)))), emptyList()).toJson())
            val done = Object()
            cache.subscribe { synchronized(done) { done.notifyAll() } }
            cache.need("1", 0)
            val deadline = System.currentTimeMillis() + 10_000
            synchronized(done) {
                while ("1" in cache.fetching() && System.currentTimeMillis() < deadline) done.wait(100)
            }
            assertEquals(null, cache.failed("1"))
            assertTrue(cache.complete("1"))
        }
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
