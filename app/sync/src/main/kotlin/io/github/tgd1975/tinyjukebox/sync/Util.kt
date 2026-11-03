package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.longOrNull
import java.io.File
import java.io.FileOutputStream
import java.io.IOException
import java.io.RandomAccessFile
import java.nio.charset.CharacterCodingException
import java.security.MessageDigest
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

// Helpers of the sync module: files written in one step, checksums, JSON, time (tinyjukebox/sync/util.py).

val JSON = Json { ignoreUnknownKeys = true }

fun hex(bytes: ByteArray): String = bytes.joinToString("") { "%02x".format(it) }

fun sha256(bytes: ByteArray): String = hex(MessageDigest.getInstance("SHA-256").digest(bytes))

fun sha256(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buffer = ByteArray(1 shl 20)
        while (true) {
            val read = input.read(buffer)
            if (read < 0) break
            digest.update(buffer, 0, read)
        }
    }
    return hex(digest.digest())
}

/** Write under a temporary name and rename: a power cut leaves the old or the new file, never half of one. */
fun writeAtomic(file: File, data: ByteArray) {
    file.absoluteFile.parentFile.mkdirs()
    val temporary = File(file.path + ".tmp")
    FileOutputStream(temporary).use { output ->
        output.write(data)
        output.flush()
        output.fd.sync()
    }
    if (!temporary.renameTo(file)) {
        file.delete()
        if (!temporary.renameTo(file)) throw IOException("cannot rename $temporary")
    }
}

fun writeAtomic(file: File, text: String) = writeAtomic(file, text.toByteArray(Charsets.UTF_8))

fun readJson(file: File): JsonElement? = if (file.isFile) JSON.parseToJsonElement(file.readText()) else null

fun writeJson(file: File, value: JsonElement) = writeAtomic(file, JSON.encodeToString(JsonElement.serializer(), value) + "\n")

fun readText(file: File): String = if (file.isFile) file.readText().trim() else ""

/**
 * One JSON object with a number "n" per line: the outbox and the journal. Returns the lines that are complete, and
 * how many are not: a process ended while it wrote, or a full storage, may leave a line half written or filled with
 * zeros. The other lines stay usable (_lines of tinyjukebox/sync/data.py).
 */
fun readLines(file: File): Pair<MutableList<JsonObject>, Int> {
    val lines = mutableListOf<JsonObject>()
    var damaged = 0
    val raw = if (file.isFile) file.readBytes() else ByteArray(0)
    var start = 0
    while (start <= raw.size) {
        var end = start
        while (end < raw.size && raw[end] != '\n'.code.toByte()) end++
        val text = raw.copyOfRange(start, end)
        start = end + 1
        if (text.all { it == ' '.code.toByte() || it == '\t'.code.toByte() || it == '\r'.code.toByte() || it == 0.toByte() }) {
            if (0.toByte() in text) damaged++ // zeros only: a line that never reached the storage
            continue
        }
        val line = try {
            JSON.parseToJsonElement(text.decodeToString(throwOnInvalidSequence = true)) as? JsonObject
        } catch (error: IllegalArgumentException) {
            null // no JSON
        } catch (error: CharacterCodingException) {
            null // no UTF-8
        }
        if (line != null && line["n"].int != null) lines += line else damaged++
    }
    return lines to damaged
}

fun writeLines(file: File, lines: List<JsonObject>) =
    writeAtomic(file, lines.joinToString("") { it.toString() + "\n" })

/** Append a line and sync it. A line that was cut off before - the process was ended, the storage was full - stays a
 * line of its own: the new one is not lost with it. */
fun appendLine(file: File, line: JsonObject) {
    file.absoluteFile.parentFile.mkdirs()
    val cut = file.length() > 0 && RandomAccessFile(file, "r").use { it.seek(it.length() - 1); it.read() != '\n'.code }
    FileOutputStream(file, true).use { output ->
        output.write(((if (cut) "\n" else "") + line.toString() + "\n").toByteArray(Charsets.UTF_8))
        output.flush()
        output.fd.sync()
    }
}

/** The time of day as the boxes write it: ``2026-10-06T10:00:00Z``. */
fun timestamp(at: Date = Date()): String =
    SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.ROOT).apply { timeZone = TimeZone.getTimeZone("UTC") }.format(at)

/** The time a tag was created, as ``tinyjukebox.data.common.now`` writes it. */
fun created(at: Date = Date()): String =
    SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'000Z'", Locale.ROOT)
        .apply { timeZone = TimeZone.getTimeZone("UTC") }.format(at)

// --- reading JSON without ceremony -----------------------------------------------------------------------------------

fun json(vararg pairs: Pair<String, Any?>): JsonObject = JsonObject(pairs.associate { (key, value) -> key to element(value) })

fun element(value: Any?): JsonElement = when (value) {
    null -> JsonNull
    is JsonElement -> value
    is String -> JsonPrimitive(value)
    is Number -> JsonPrimitive(value)
    is Boolean -> JsonPrimitive(value)
    is Map<*, *> -> JsonObject(value.entries.associate { (key, item) -> key.toString() to element(item) })
    is Iterable<*> -> JsonArray(value.map { element(it) })
    else -> error("no JSON for $value")
}

val JsonElement?.obj: JsonObject? get() = this as? JsonObject
val JsonElement?.array: JsonArray? get() = this as? JsonArray
val JsonElement?.string: String? get() = (this as? JsonPrimitive)?.takeIf { it.isString }?.contentOrNull

/** A string or a number as text: the ids of targets are both. */
val JsonElement?.text: String? get() = (this as? JsonPrimitive)?.takeIf { this !is JsonNull }?.contentOrNull
val JsonElement?.int: Int? get() = (this as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull
val JsonElement?.long: Long? get() = (this as? JsonPrimitive)?.takeIf { !it.isString }?.longOrNull
val JsonElement?.double: Double? get() = (this as? JsonPrimitive)?.takeIf { !it.isString }?.doubleOrNull
val JsonElement?.boolean: Boolean? get() = (this as? JsonPrimitive)?.takeIf { !it.isString }?.booleanOrNull
val JsonElement?.isNull: Boolean get() = this == null || this is JsonNull

/** This object with [fields] added or replaced. */
fun JsonObject.with(fields: Map<String, Any?>): JsonObject =
    JsonObject(LinkedHashMap<String, JsonElement>(this).apply { fields.forEach { (key, value) -> put(key, element(value)) } })
