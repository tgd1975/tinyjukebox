package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import java.util.Base64
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLException
import javax.net.ssl.X509TrustManager

/** The other side cannot be reached, or the connection broke. */
class Unreachable(message: String) : IOException(message)

/** The server presented another certificate than the one that is expected. */
class WrongCertificate(message: String) : IOException(message)

/** An answer with a status of 400 or above. */
class HttpError(val status: Int, message: String, val details: JsonObject = JsonObject(emptyMap())) :
    IOException("$status $message".trim())

class Response(val status: Int, val body: ByteArray) {
    fun json(): JsonElement? = if (body.isEmpty()) null else JSON.parseToJsonElement(body.decodeToString())
}

/** A certificate as the library stores it, PEM, and back. */
object Pem {
    fun encode(der: ByteArray): String {
        val base64 = Base64.getEncoder().encodeToString(der).chunked(64).joinToString("\n")
        return "-----BEGIN CERTIFICATE-----\n$base64\n-----END CERTIFICATE-----\n"
    }

    fun decode(pem: String): ByteArray = Base64.getDecoder().decode(
        pem.lines().filter { it.isNotBlank() && !it.startsWith("-----") }.joinToString(""),
    )
}

/**
 * Requests to one server, as tinyjukebox/sync/client.py makes them. With [certificate] (DER) only a server that
 * presents exactly this one is accepted; the name in it does not matter. Without it, the certificate the server
 * presented is kept in [peerCertificate] - the handshake proves it.
 */
class Client(
    url: String,
    private val certificate: ByteArray? = null,
    val headers: Map<String, String> = emptyMap(),
    private val timeoutMs: Int = 10_000,
) {
    private val base = url.trimEnd('/')

    @Volatile
    var peerCertificate: ByteArray? = null
        private set

    @Volatile
    private var stream: HttpURLConnection? = null

    private inner class Pinned : X509TrustManager {
        var refused = false

        override fun checkClientTrusted(chain: Array<out X509Certificate>, authType: String) = Unit

        override fun checkServerTrusted(chain: Array<out X509Certificate>, authType: String) {
            val presented = chain.first().encoded
            peerCertificate = presented
            if (certificate != null && !presented.contentEquals(certificate)) {
                refused = true
                throw CertificateException("another certificate than the stored one")
            }
        }

        override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
    }

    private fun open(method: String, path: String, timeout: Int): Pair<HttpURLConnection, Pinned?> {
        val connection = URL(base + path).openConnection() as HttpURLConnection
        var pinned: Pinned? = null
        if (connection is HttpsURLConnection) {
            pinned = Pinned()
            val context = SSLContext.getInstance("TLS")
            context.init(null, arrayOf(pinned), null)
            connection.sslSocketFactory = context.socketFactory
            connection.hostnameVerifier = javax.net.ssl.HostnameVerifier { _, _ -> true } // the certificate is pinned
        }
        connection.requestMethod = method
        connection.connectTimeout = timeoutMs
        connection.readTimeout = timeout
        connection.useCaches = false
        // one connection per request, as the client of a box: each has its own TLS context, so a connection kept alive
        // would never be used again and only hold one of the connections the server allows at once
        connection.setRequestProperty("Connection", "close")
        headers.forEach { (key, value) -> connection.setRequestProperty(key, value) }
        return connection to pinned
    }

    private fun failed(error: IOException, pinned: Pinned?): IOException = when {
        error is HttpError || error is WrongCertificate -> error
        pinned?.refused == true -> WrongCertificate("$base presents another certificate than the stored one")
        error is SSLException -> Unreachable("$base: $error")
        else -> Unreachable("$base: $error")
    }

    /**
     * One request. [body] is sent as JSON, [upload] names a file to send, [sink] a file for the answer. A [sink] that
     * exists already is continued: the rest of the file is asked for, and the answer is appended if it is the rest
     * ([continues]), otherwise it replaces the sink. An answer with a status of 400 or above raises [HttpError].
     */
    fun request(
        method: String,
        path: String,
        body: JsonElement? = null,
        sink: File? = null,
        upload: File? = null,
    ): Response {
        val (connection, pinned) = open(method, path, timeoutMs)
        try {
            val offset = if (sink != null && sink.isFile) sink.length() else 0L
            if (offset > 0) connection.setRequestProperty("Range", "bytes=$offset-")
            when {
                upload != null -> {
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/octet-stream")
                    connection.setFixedLengthStreamingMode(upload.length())
                    connection.outputStream.use { output -> upload.inputStream().use { it.copyTo(output) } }
                }
                body != null -> {
                    val data = body.toString().toByteArray(Charsets.UTF_8)
                    connection.doOutput = true
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.setFixedLengthStreamingMode(data.size)
                    connection.outputStream.use { it.write(data) }
                }
                method in setOf("POST", "PUT") -> {
                    connection.doOutput = true
                    connection.setFixedLengthStreamingMode(0)
                    connection.outputStream.close()
                }
            }
            val status = connection.responseCode
            if (status >= 400) {
                val content = connection.errorStream?.use { it.readBytes() } ?: ByteArray(0)
                val details = runCatching { JSON.parseToJsonElement(content.decodeToString()) as JsonObject }
                    .getOrDefault(JsonObject(emptyMap()))
                val message = details["error"].string ?: details["message"].string ?: ""
                throw HttpError(status, message, details)
            }
            if (sink != null && (status == 200 || status == 206)) {
                sink.absoluteFile.parentFile.mkdirs()
                FileOutputStream(sink, continues(status, connection.getHeaderField("Content-Range"), offset)).use { output ->
                    connection.inputStream.use { it.copyTo(output, 1 shl 16) }
                    output.flush()
                    output.fd.sync()
                }
                return Response(status, ByteArray(0))
            }
            return Response(status, connection.inputStream.use { it.readBytes() })
        } catch (error: IOException) {
            throw failed(error, pinned)
        } finally {
            connection.disconnect()
        }
    }

    fun get(path: String): JsonElement? = request("GET", path).json()

    fun post(path: String, body: JsonElement? = null): JsonElement? = request("POST", path, body).json()

    fun put(path: String, body: JsonElement? = null): JsonElement? = request("PUT", path, body).json()

    /**
     * Server-sent events until the connection breaks or [interrupt] is called; then [Unreachable] is raised. Every
     * event is given to [event] by its name.
     */
    fun events(path: String, event: (String) -> Unit): Nothing {
        val (connection, pinned) = open("GET", path, 3 * KEEP_ALIVE_MS)
        try {
            stream = connection
            val status = connection.responseCode
            if (status != 200) throw HttpError(status, "no events")
            var name = ""
            connection.inputStream.bufferedReader(Charsets.UTF_8).use { reader ->
                while (true) {
                    val line = reader.readLine() ?: break
                    when {
                        line.startsWith("event: ") -> name = line.substring(7)
                        line.startsWith("data: ") -> event(name)
                    }
                }
            }
        } catch (error: IOException) {
            throw failed(error, pinned)
        } finally {
            stream = null
            connection.disconnect()
        }
        throw Unreachable("$base: the event stream ended")
    }

    /** End [events] from another thread: its connection is cut. */
    fun interrupt() {
        stream?.disconnect()
    }

    companion object {
        /** Seconds without an event after which the library sends a comment, so that both ends notice a cut. */
        const val KEEP_ALIVE_MS = 5_000

        /**
         * Whether the answer to ``Range: bytes=<offset>-`` is the rest of the file, to be appended to what is there
         * (docs/developer/app.md, section 6): a 206, or a 200 whose Content-Range begins at [offset] - so an older
         * library answers a range, and every library a range at the end of a complete file, with nothing in the body.
         * Anything else is the whole file.
         */
        fun continues(status: Int, contentRange: String?, offset: Long): Boolean =
            offset > 0 && (status == 206 || status == 200 && contentRange?.trim()?.startsWith("bytes $offset-") == true)
    }
}
