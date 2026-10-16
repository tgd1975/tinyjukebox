package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonObject
import org.junit.jupiter.api.AfterAll
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertNotNull
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.BeforeAll
import org.junit.jupiter.api.MethodOrderer
import org.junit.jupiter.api.Order
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.TestInstance
import org.junit.jupiter.api.TestMethodOrder
import java.io.File
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.nio.file.Files

/**
 * The sync module against a real library: ``tinyjukebox-library --data DIR`` of this repository, with its passwords
 * from the environment (docs/developer/library.md, section 3.9). Pairing, a pass, an album on demand, a position that
 * moves on and one the library set, a journal line - all seen by the library. Needs ``uv``.
 */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
@TestMethodOrder(MethodOrderer.OrderAnnotation::class)
class LibraryTest {
    private val repository = File(System.getProperty("tinyjukebox.repository") ?: "../..")
    private val version = System.getProperty("tinyjukebox.version") ?: "0.0.0"
    private lateinit var root: File
    private lateinit var library: Process
    private lateinit var url: String
    private lateinit var admin: Admin
    private lateinit var sync: Sync
    private lateinit var album: String

    private lateinit var phone: File

    @BeforeAll
    fun startLibrary() {
        root = Files.createTempDirectory("tinyjukebox-library").toFile()
        phone = Files.createTempDirectory("tinyjukebox-phone").toFile()
        File(repository, "tests/data/albums/simple_album_a").copyRecursively(File(root, "albums/simple_album_a"))
        val port = ServerSocket(0).use { it.localPort }
        File(root, "config.yaml").writeText("library:\n  rest-api:\n    host: 127.0.0.1\n    port: $port\n")
        val builder = ProcessBuilder("uv", "run", "--quiet", "tinyjukebox-library", "--data", root.path)
            .directory(repository).redirectErrorStream(true).redirectOutput(File(root, "library.out"))
        builder.environment().remove("VIRTUAL_ENV")
        builder.environment()["TINYJUKEBOX_ADMIN_PASSWORD"] = ADMIN_PASSWORD
        builder.environment()["TINYJUKEBOX_BOOTSTRAP_PASSWORD"] = "bootstrap-password-of-the-tests"
        library = builder.start()
        val deadline = System.currentTimeMillis() + 120_000
        while (!reachable(port)) {
            check(library.isAlive) { "the library ended: " + File(root, "library.out").readText() }
            check(System.currentTimeMillis() < deadline) { "the library does not answer" }
            Thread.sleep(200)
        }
        url = "https://127.0.0.1:$port"
        admin = Admin(url, ADMIN_PASSWORD)
        sync = syncOf(phone, version) { println("app: $it") }
    }

    private fun reachable(port: Int): Boolean = runCatching {
        Socket().use { it.connect(InetSocketAddress("127.0.0.1", port), 200) }
    }.isSuccess

    @AfterAll
    fun stopLibrary() {
        library.destroy()
        library.waitFor()
        File(root, "log/library.log").copyTo(File("build/library.log"), overwrite = true)
        root.deleteRecursively()
        phone.deleteRecursively()
    }

    private fun passed() {
        while (sync.pass()) Unit
        assertTrue(sync.connected, sync.alone)
    }

    @Test
    @Order(1)
    fun `an album and a tag on the page of the library, then the phone is paired`() {
        admin.post("/api/albums/new", json())
        album = admin.get("/api/albums").array!!.map { it.obj!! }.single { it["folder"].string == "simple_album_a" }["id"].text!!
        admin.post("/api/tags", json("uid" to TAG, "album_id" to album))
        val content = admin.post("/api/library/pairings", json("url" to url)).obj!!["content"].string!!
        sync.identity.pair(Pairing.parse(content)!!, "Pixel 9a")
        assertTrue(sync.identity.paired)
        val boxes = admin.get("/api/library/overview").obj!!["boxes"].array!!.map { it.obj!! }
        val me = boxes.single { it["id"].string == sync.identity.boxId }
        assertEquals("app", me["kind"].string)
        assertEquals("Pixel 9a", me["name"].string)
    }

    @Test
    @Order(2)
    fun `a pass brings the catalog and the covers`() {
        passed()
        assertEquals(Fit.OK, sync.fit)
        val tag = sync.data.working("tag").getValue(TAG)
        assertEquals(album, tag["target"].obj!!["id"].text)
        assertEquals("simple_album_a", sync.data.working("target").getValue(album)["album-folder"].string)
        assertEquals("Pixel 9a", sync.data.setting("name").string)
        assertEquals("app-$version", sync.version)
        val status = admin.get("/api/library/overview").obj!!["boxes"].array!!.single { it.obj!!["id"].string == sync.identity.boxId }
        assertEquals("app-$version", status.obj!!["software"].string)
    }

    @Test
    @Order(3)
    fun `an album is loaded on demand into the cache`() {
        assertFalse(sync.cache.ready(album, 0))
        val done = Object()
        sync.cache.subscribe { synchronized(done) { done.notifyAll() } }
        sync.cache.need(album, 2)
        val deadline = System.currentTimeMillis() + 30_000
        synchronized(done) {
            while (!sync.cache.complete(album) && System.currentTimeMillis() < deadline) done.wait(200)
        }
        assertTrue(sync.cache.complete(album), sync.cache.failed(album) ?: "")
        assertEquals(listOf("1.mp3", "2.mp3", "3.mp3", "4.mp3", "5.mp3"), sync.cache.tracks(album))
        for (name in sync.cache.tracks(album)) {
            val file = sync.cache.file(album, name)!!
            assertEquals(sha256(File(repository, "tests/data/albums/simple_album_a/$name")), sha256(file))
        }
        assertTrue(sync.cache.ready(album, 4))
    }

    @Test
    @Order(4)
    fun `a position moves on and is in the library`() {
        sync.data.add("position", TAG, position(2, 7.5))
        passed()
        assertTrue(sync.data.outbox().isEmpty())
        val state = admin.get("/api/library/boxes/${sync.identity.boxId}").obj!!
        val value = state["positions"].obj!![TAG].obj!!["value"].obj!!
        assertEquals(2, value["track-id"].int)
        assertEquals(7.5, value["track-time"].double)
    }

    @Test
    @Order(5)
    fun `a position set in the library wins, and stays when the album leaves the cache`() {
        admin.put("/api/library/boxes/${sync.identity.boxId}/positions/${Cache.quoted(TAG)}", json("value" to position(0, 0.0)))
        sync.data.add("position", TAG, position(3, 1.0), base = 1) // based on the revision before the library's
        passed()
        assertEquals(0, sync.data.working("position").getValue(TAG)["track-id"].int)
        sync.cache.leave(album)
        assertFalse(sync.cache.ready(album, 0))
        assertEquals(0, sync.data.working("position").getValue(TAG)["track-id"].int)
    }

    @Test
    @Order(6)
    fun `the journal reaches the statistics and an unknown tag the catalog`() {
        sync.data.journal("start")
        sync.data.journal("tag", mapOf("uid" to TAG, "kind" to "album", "target" to album, "output" to "headphones"))
        sync.data.journal("track", mapOf("album" to album, "track" to 0, "seconds" to 12.0, "to_end" to true))
        sync.data.journal("end", mapOf("uid" to TAG, "reason" to "tag removed"))
        sync.data.add("tag", UNKNOWN, AppData.unknownTag(UNKNOWN))
        passed()
        assertTrue(sync.data.journalLines().isEmpty())
        val lines = admin.get("/api/library/journal?box=${sync.identity.boxId}").obj!!["lines"].array!!.map { it.obj!! }
        assertTrue(lines.any { it["event"].string == "tag" && it["output"].string == "headphones" }, lines.toString())
        val statistics = admin.get("/api/library/statistics?box=${sync.identity.boxId}").toString()
        assertTrue(TAG in statistics, statistics)
        assertTrue("app-$version" in statistics, statistics)
        val tags = admin.get("/api/tags").array!!.map { it.obj!!["uid"].string }
        assertTrue(UNKNOWN in tags)
        assertTrue(AppData.unlinked(sync.data.working("tag").getValue(UNKNOWN)["target"]))
    }

    @Test
    @Order(7)
    fun `the volume limits set on the page reach the phone`() {
        admin.patch("/api/library/boxes/${sync.identity.boxId}", json("volume" to json("speaker" to json("max-volume" to 60))))
        passed()
        assertEquals(60, sync.data.working("setting").getValue("volume.speaker")["max-volume"].int)
    }

    @Test
    @Order(8)
    fun `a phone removed on the page works alone, and unpaired it is empty`() {
        admin.delete("/api/library/boxes/${sync.identity.boxId}")
        assertFalse(sync.pass())
        assertFalse(sync.connected)
        assertTrue("rejects the key" in sync.alone, sync.alone)
        assertNotNull(sync.data.working("tag")[TAG]) // what it has stays
        sync.unpair()
        assertFalse(sync.identity.paired)
        assertTrue(sync.data.working("tag").isEmpty())
    }

    private fun position(track: Int, seconds: Double): JsonObject = json(
        "tag-uid" to TAG, "target-id" to album, "track-id" to track, "track-time" to seconds,
        "target-type" to "album", "album-finished" to false,
    )

    companion object {
        const val ADMIN_PASSWORD = "admin-password-of-the-tests"
        const val TAG = "04:11:22:33:44:55:66"
        const val UNKNOWN = "04:AA:BB:CC:DD:EE:01"
    }
}

/** The page of the library, as an admin: PATCH is no method of HttpURLConnection, so the tests use their own client. */
class Admin(private val url: String, password: String) {
    private val http: java.net.http.HttpClient = java.net.http.HttpClient.newBuilder().sslContext(
        javax.net.ssl.SSLContext.getInstance("TLS").apply {
            init(null, arrayOf(object : javax.net.ssl.X509TrustManager {
                override fun checkClientTrusted(chain: Array<out java.security.cert.X509Certificate>, authType: String) = Unit
                override fun checkServerTrusted(chain: Array<out java.security.cert.X509Certificate>, authType: String) = Unit
                override fun getAcceptedIssuers(): Array<java.security.cert.X509Certificate> = emptyArray()
            }), null)
        },
    ).build()
    private var token = ""

    init {
        System.setProperty("jdk.internal.httpclient.disableHostnameVerification", "true")
        token = post("/api/login", json("username" to "admin", "password" to password)).obj!!["token"].string!!
    }

    private fun send(method: String, path: String, body: kotlinx.serialization.json.JsonElement?): kotlinx.serialization.json.JsonElement? {
        val request = java.net.http.HttpRequest.newBuilder(java.net.URI(url + path))
            .header("Authorization", "Bearer $token").header("Content-Type", "application/json")
            .method(method, body?.let { java.net.http.HttpRequest.BodyPublishers.ofString(it.toString()) }
                ?: java.net.http.HttpRequest.BodyPublishers.noBody())
            .build()
        val answer = http.send(request, java.net.http.HttpResponse.BodyHandlers.ofString())
        check(answer.statusCode() < 400) { "$method $path: ${answer.statusCode()} ${answer.body()}" }
        return answer.body().takeIf { it.isNotBlank() }?.let { JSON.parseToJsonElement(it) }
    }

    fun get(path: String) = send("GET", path, null)
    fun post(path: String, body: kotlinx.serialization.json.JsonElement) = send("POST", path, body)
    fun put(path: String, body: kotlinx.serialization.json.JsonElement) = send("PUT", path, body)
    fun patch(path: String, body: kotlinx.serialization.json.JsonElement) = send("PATCH", path, body)
    fun delete(path: String) = send("DELETE", path, null)
}
