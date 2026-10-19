package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.File

/** Version of the interface between box and library that this code speaks (``INTERFACE`` in sync/__init__.py). */
const val INTERFACE = 2

/** Who made a change that no person asked for: a position that moved on, a tag created for an unknown uid. */
const val SOFTWARE = "software"

val COPY = mapOf("tag" to "tags.json", "target" to "targets.json")
const val IMAGES = "images.json"
val CATALOG_FILES = listOf("tags.json", "targets.json", IMAGES)
const val STATE = "box.json"

/** What a new file of the copy means for the app. */
class Replaced {
    /** Positions the library lost, e.g. it was restored from a backup: (key, value, revision). */
    val lost = mutableListOf<Triple<String, JsonElement, Int>>()

    /** Targets that the library deleted, as the app had them. */
    val removed = mutableListOf<JsonObject>()
}

/**
 * What the app stores for the synchronization, in the same form as a box (tinyjukebox/sync/data.py,
 * docs/developer/library.md, section 4.2):
 *
 * - the copy: the catalog, and the positions and settings of this device, exactly as the library delivered them;
 * - the outbox: what changed here and is not confirmed yet - a position, and an unknown tag created by the software;
 * - what the app works with is the copy with the outbox applied on top ([working]);
 * - the journal: lines that are only appended, numbered, and deleted when the library has confirmed them.
 */
class AppData(private val folder: File, val boxId: String, val version: String) {
    private val lock = Any()
    private val copy = mutableMapOf<String, JsonObject>()
    private val raw = mutableMapOf<String, ByteArray>()
    private var outbox: MutableList<JsonObject>
    private var journal: MutableList<JsonObject>
    private val numbers: MutableMap<String, Int>
    private val started = System.nanoTime()

    /** Called whenever the working data changed: a new copy, or a change that left the outbox. */
    @Volatile
    var changed: (() -> Unit)? = null

    init {
        File(folder, "copy").mkdirs()
        for (name in CATALOG_FILES + STATE) loadCopy(name)
        outbox = readLines(file("outbox"))
        journal = readLines(file("journal"))
        numbers = mutableMapOf("outbox" to 0, "journal" to 0, "start" to 0)
        readJson(file("numbers.json")).obj?.forEach { (key, value) -> value.int?.let { numbers[key] = it } }
        numbers["start"] = numbers.getValue("start") + 1
        saveNumbers()
    }

    val start: Int get() = numbers.getValue("start")

    private fun file(name: String) = File(folder, name)

    private fun saveNumbers() = writeJson(file("numbers.json"), element(numbers.toSortedMap()))

    // --- copy --------------------------------------------------------------------------------------------------------

    private fun loadCopy(name: String) {
        val file = File(File(folder, "copy"), name)
        if (file.isFile) {
            raw[name] = file.readBytes()
            copy[name] = JSON.parseToJsonElement(raw.getValue(name).decodeToString()) as JsonObject
        } else {
            raw[name] = ByteArray(0)
            copy[name] = if (name == STATE) json("positions" to emptyMap<String, Any>(), "settings" to emptyMap<String, Any>())
            else json("entries" to emptyMap<String, Any>())
        }
    }

    private fun copied(kind: String): JsonObject {
        val name = COPY[kind]
        return if (name != null) copy.getValue(name)["entries"].obj ?: JsonObject(emptyMap())
        else copy.getValue(STATE)["${kind}s"].obj ?: JsonObject(emptyMap())
    }

    fun checksum(name: String): String = synchronized(lock) { sha256(raw.getValue(name)) }

    /**
     * Store a file as the library delivered it. The outbox stays on top, so no own change is lost. For the positions
     * of this device the result lists what the library has with a lower revision than the copy had: the library lost
     * data, and the app wins (section 4.12).
     */
    fun replaceCopy(name: String, data: ByteArray): Replaced {
        val result = Replaced()
        synchronized(lock) {
            val old = copy.getValue(name)
            JSON.parseToJsonElement(data.decodeToString()) as JsonObject // a file that is not JSON never becomes the copy
            writeAtomic(File(File(folder, "copy"), name), data)
            loadCopy(name)
            val new = copy.getValue(name)
            if (name == STATE) {
                val before = old["positions"].obj ?: JsonObject(emptyMap())
                val after = new["positions"].obj ?: JsonObject(emptyMap())
                for ((key, entry) in before) {
                    val revision = entry.obj?.get("rev").int ?: 0
                    val value = entry.obj?.get("value") ?: JsonNull
                    if ((after[key].obj?.get("rev").int ?: 0) < revision && !value.isNull) result.lost += Triple(key, value, revision)
                }
            } else if (name == COPY["target"]) {
                val after = new["entries"].obj ?: JsonObject(emptyMap())
                for ((key, entry) in old["entries"].obj ?: JsonObject(emptyMap())) {
                    val value = entry.obj?.get("value").obj ?: continue
                    if (after[key].obj?.get("value").isNull) result.removed += value
                }
            }
        }
        changed?.invoke()
        return result
    }

    /** What the catalog says about the files of an album: identity, checksum, files, size, what the library lacks. */
    fun album(key: String): JsonObject? = synchronized(lock) { copied("target")[key].obj?.get("album").obj }

    /** The covers that the library has: checksum and size each, or the mark of a removed one. */
    fun images(): JsonObject = synchronized(lock) { copy.getValue(IMAGES)["images"].obj ?: JsonObject(emptyMap()) }

    // --- outbox ------------------------------------------------------------------------------------------------------

    /**
     * A change made here: the entry [key] of [kind] is [value] now. The app makes two kinds of change: a position,
     * and a tag that the software created as not linked. The entry carries a running number, the revision it is based
     * on, who made it and the version; an earlier change of the same entry that is still in the outbox is replaced.
     */
    fun add(kind: String, key: String, value: JsonElement, base: Int? = null, by: String = SOFTWARE): JsonObject {
        require(kind == "position" || kind == "tag") { "the app changes positions and tags only" }
        synchronized(lock) {
            val copiedEntry = copied(kind)[key].obj
            val before = outbox.size
            outbox = outbox.filterNot { it["kind"].string == kind && it["key"].string == key }.toMutableList()
            numbers["outbox"] = numbers.getValue("outbox") + 1
            saveNumbers()
            val entry = json(
                "n" to numbers.getValue("outbox"), "op" to "set", "kind" to kind, "key" to key, "value" to value,
                "by" to by, "version" to version, "clock" to json("time" to timestamp(), "synchronized" to true),
                "base" to (base ?: copiedEntry?.get("rev").int ?: 0),
            )
            outbox.add(entry)
            if (outbox.size == before + 1) appendLine(file("outbox"), entry) else writeLines(file("outbox"), outbox)
            return entry
        }
    }

    fun outbox(): List<JsonObject> = synchronized(lock) { outbox.toList() }

    /** The library answered these entries, and the copy has what it made of them: they leave the outbox. */
    fun confirm(numbers: Set<Int>) {
        if (numbers.isEmpty()) return
        synchronized(lock) {
            outbox = outbox.filterNot { it["n"].int in numbers }.toMutableList()
            writeLines(file("outbox"), outbox)
        }
        changed?.invoke()
    }

    // --- what the app works with -------------------------------------------------------------------------------------

    /** The entries of [kind] ("tag", "target", "position", "setting"): the copy with the outbox applied on top. */
    fun working(kind: String): Map<String, JsonObject> = synchronized(lock) {
        val result = linkedMapOf<String, JsonObject>()
        for ((key, entry) in copied(kind)) {
            val value = entry.obj?.get("value")
            if (value is JsonObject) result[key] = value
        }
        for (entry in outbox) {
            if (entry["kind"].string != kind) continue
            val value = entry["value"]
            val key = entry["key"].string ?: continue
            if (value is JsonObject) result[key] = value else result.remove(key)
        }
        result
    }

    /** A setting of this device that is no object: the name, the client id of Spotify. */
    fun setting(key: String): JsonElement? = synchronized(lock) { copied("setting")[key].obj?.get("value") }

    // --- journal -----------------------------------------------------------------------------------------------------

    /**
     * Append a line (docs/developer/app.md, section 5, docs/developer/library.md, section 7). It carries the start, a running
     * number, the seconds since the start and the time of day - a phone knows the time.
     */
    fun journal(event: String, fields: Map<String, Any?> = emptyMap()) {
        synchronized(lock) {
            numbers["journal"] = numbers.getValue("journal") + 1
            saveNumbers()
            val uptime = Math.round((System.nanoTime() - started) / 1e8) / 10.0
            val line = json(
                "n" to numbers.getValue("journal"), "start" to numbers.getValue("start"), "uptime" to uptime,
                "clock" to json("time" to timestamp(), "synchronized" to true), "version" to version, "event" to event,
            ).with(fields)
            journal.add(line)
            appendLine(file("journal"), line)
        }
    }

    fun journalLines(limit: Int = 200): List<JsonObject> = synchronized(lock) { journal.take(limit) }

    /** The library has the lines up to [number]: they are deleted here. */
    fun journalConfirmed(number: Int) = synchronized(lock) {
        journal = journal.filter { (it["n"].int ?: 0) > number }.toMutableList()
        writeLines(file("journal"), journal)
    }

    /** Unpaired: copy and outbox go. Numbers and journal stay - a number is never given twice. */
    fun forgetLibrary() {
        synchronized(lock) {
            for (name in CATALOG_FILES + STATE) {
                File(File(folder, "copy"), name).delete()
                loadCopy(name)
            }
            outbox = mutableListOf()
            writeLines(file("outbox"), outbox)
            journal = mutableListOf()
            writeLines(file("journal"), journal)
        }
        changed?.invoke()
    }

    companion object {
        /** The target of a tag that is not linked: -1, "-1", or none. */
        fun unlinked(target: JsonElement?): Boolean {
            val id = target.obj?.get("id")
            return id.isNull || id.text == "-1"
        }

        /** A tag as a box creates it for an unknown uid: not linked (Tag.create_tag of tinyjukebox.data). */
        fun unknownTag(uid: String): JsonObject = json(
            "tag" to json("uid" to uid, "name" to ""),
            "target" to json("type" to "album", "id" to -1),
            "created" to created(),
        )

    }
}
