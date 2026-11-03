package io.github.tgd1975.tinyjukebox.sync

import com.sun.net.httpserver.HttpServer
import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File
import java.net.InetSocketAddress

/**
 * A transfer that broke off is continued (docs/developer/app.md, section 6): the app asks for the rest of the file and
 * appends it - whether the library answers with 206, as it does now, or with 200 and the Content-Range, as it did
 * before. The library itself is in LibraryTest; here a stand-in answers both ways.
 */
class ClientTest {
    @TempDir
    lateinit var folder: File

    private val content = ByteArray(10_000) { (it * 7 % 251).toByte() }

    /** The file, as a library answers ``Range: bytes=N-``: with [partial] for a range inside the file, with 200, the
     * Content-Range and nothing in the body for a range at its end. */
    private fun library(partial: Int) = StandIn { request ->
        val start = Regex("""bytes=(\d+)-""").matchEntire(request.range ?: "")?.groupValues?.get(1)?.toInt() ?: 0
        val from = minOf(start, content.size)
        if (from == 0) StandIn.Answer(200, content)
        else StandIn.Answer(
            if (from < content.size) partial else 200, content.copyOfRange(from, content.size),
            mapOf("Content-Range" to "bytes $from-${content.size - 1}/${content.size}"),
        )
    }

    private fun part(bytes: Int) = File(folder, ".1.mp3.part").apply { writeBytes(content.copyOf(bytes)) }

    @Test
    fun `a transfer that broke off is continued, as a library answers now and as it answered before`() {
        for (status in listOf(206, 200)) {
            library(status).use { library ->
                val part = part(4_000)
                Client(library.url).request("GET", "/file", sink = part)
                assertArrayEquals(content, part.readBytes(), "answered with $status")
            }
        }
    }

    @Test
    fun `a complete part stays as it is`() {
        for (status in listOf(206, 200)) {
            library(status).use { library ->
                val part = part(content.size)
                Client(library.url).request("GET", "/file", sink = part)
                assertArrayEquals(content, part.readBytes(), "answered with $status")
            }
        }
    }

    @Test
    fun `a server that knows no ranges sends the whole file, and it replaces the part`() {
        StandIn { StandIn.Answer(200, content) }.use { server ->
            val part = part(4_000)
            Client(server.url).request("GET", "/file", sink = part)
            assertArrayEquals(content, part.readBytes())
        }
    }

    @Test
    fun `when an answer continues what is there`() {
        assertTrue(Client.continues(206, "bytes 4000-9999/10000", 4000))
        assertTrue(Client.continues(206, null, 4000))
        assertTrue(Client.continues(200, "bytes 4000-9999/10000", 4000)) // an older library
        assertTrue(Client.continues(200, "bytes 10000-9999/10000", 10000)) // the end of a complete file
        assertFalse(Client.continues(200, null, 4000)) // the whole file
        assertFalse(Client.continues(200, "bytes 0-9999/10000", 4000))
        assertFalse(Client.continues(200, "bytes 40000-49999/50000", 4000))
        assertFalse(Client.continues(200, "bytes 10000-9999/10000", 12000)) // the part is longer than the file
    }
}

/** A server on this computer, plain HTTP, that answers as a test says: a stand-in for the library. */
class StandIn(private val answer: (Request) -> Answer) : AutoCloseable {
    class Request(val method: String, val path: String, val range: String?, val body: String)

    class Answer(val status: Int, val body: ByteArray = ByteArray(0), val headers: Map<String, String> = emptyMap()) {
        constructor(status: Int, body: String) : this(status, body.toByteArray(), mapOf("Content-Type" to "application/json"))
    }

    private val server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0).apply {
        createContext("/") { exchange ->
            try {
                val body = exchange.requestBody.use { it.readBytes() }.decodeToString()
                val request = Request(exchange.requestMethod, exchange.requestURI.path, exchange.requestHeaders.getFirst("Range"), body)
                val reply = answer(request)
                // one connection per request, as the library answers the app: a connection kept would be stale next time
                exchange.responseHeaders.set("Connection", "close")
                reply.headers.forEach { (key, value) -> exchange.responseHeaders.add(key, value) }
                exchange.sendResponseHeaders(reply.status, if (reply.body.isEmpty()) -1 else reply.body.size.toLong())
                exchange.responseBody.use { if (reply.body.isNotEmpty()) it.write(reply.body) }
            } catch (error: Throwable) {
                System.err.println("the stand-in failed: ${exchange.requestMethod} ${exchange.requestURI}: $error")
                throw error
            }
        }
        start()
    }

    val url = "http://127.0.0.1:${server.address.port}"

    override fun close() = server.stop(0)
}
