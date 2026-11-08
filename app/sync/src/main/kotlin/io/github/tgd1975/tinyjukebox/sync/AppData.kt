package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.File
import java.io.IOException

/** Version of the interface between box and library that this code speaks (``INTERFACE`` in sync/__init__.py). */
const val INTERFACE = 3

/** Who made a change that no person asked for: a position that moved on, a tag created for an unknown uid. */
const val SOFTWARE = "software"

/** The event of a journal line that stands for a lost line: no listening, only the number (sync/data.py). */
const val RECOVERY_GAP = "recovery_gap"

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
 *
 * A damaged file never stops the app (sync/data.py, docs/developer/library.md, section 4.21): a line that cannot be
 * read is left out, a number of the journal without its line becomes a [RECOVERY_GAP], a copy that cannot be read is
 * delivered again by the library.
 */
class AppData(
    private val folder: File,
    val boxId: String,
    val version: String,
    private val log: (String) -> Unit = {},
) {
    private val lock = Any()
    private val copy = mutableMapOf<String, JsonObject>()
    private val raw = mutableMapOf<String, ByteArray>()
    private val numbers = mutableMapOf("outbox" to 0, "journal" to 0, "start" to 0)
    private var outbox: MutableList<JsonObject>
    private var journal: MutableList<JsonObject>
    private val started = System.nanoTime()

    /** Called whenever the working data changed: a new copy, or a change that left the outbox. */
    @Volatile
    var changed: (() -> Unit)? = null

    init {
        File(folder, "copy").mkdirs()
        for (name in CATALOG_FILES + STATE) loadCopy(name)
        readJson(file("numbers.json")).obj?.forEach { (key, value) -> value.int?.let { numbers[key] = it } }
        numbers["start"] = numbers.getValue("start") + 1
        tidy("numbers.json") { saveNumbers() }
        val (changes, damaged) = readLines(file("outbox"))
        outbox = changes
        if (damaged > 0) {
            log("outbox: $damaged damaged change(s) of this phone left out - they do not reach the library")
            tidy("outbox") { writeLines(file("outbox"), outbox) }
        }
        journal = loadJournal()
    }

    val start: Int get() = numbers.getValue("start")

    private fun file(name: String) = File(folder, name)

    private fun saveNumbers() = writeJson(file("numbers.json"), element(numbers.toSortedMap()))

    /** A file written anew while the app starts: if the storage is full, it is written with the next change. */
    private fun tidy(name: String, write: () -> Unit) {
        try {
            write()
        } catch (error: IOException) {
            log("$name cannot be written: $error")
        }
    }

    // --- copy --------------------------------------------------------------------------------------------------------

    private fun loadCopy(name: String) {
        val file = File(File(folder, "copy"), name)
        raw[name] = ByteArray(0)
        copy[name] = if (name == STATE) json("positions" to emptyMap<String, Any>(), "settings" to emptyMap<String, Any>())
        else json("entries" to emptyMap<String, Any>())
        if (!file.isFile) return
        try {
            val bytes = file.readBytes()
            copy[name] = JSON.parseToJsonElement(bytes.decodeToString()) as? JsonObject
                ?: throw IllegalArgumentException("not what the library delivers")
            raw[name] = bytes
        } catch (error: Exception) {
            // the library delivers it again: the app asks for a file whose checksum differs from its own
            log("copy/$name cannot be read ($error) - the library delivers it again")
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
            // a file that is not JSON never becomes the copy
            require(JSON.parseToJsonElement(data.decodeToString()) is JsonObject) { "$name is not what the library delivers" }
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
            val others = outbox.filterNot { it["kind"].string == kind && it["key"].string == key }
            numbers["outbox"] = numbers.getValue("outbox") + 1
            saveNumbers() // a number is never given twice: if it cannot be saved, the outbox stays as it was
            val entry = json(
                "n" to numbers.getValue("outbox"), "op" to "set", "kind" to kind, "key" to key, "value" to value,
                "by" to by, "version" to version, "clock" to json("time" to timestamp(), "synchronized" to true),
                "base" to (base ?: copiedEntry?.get("rev").int ?: 0),
            )
            val replaced = others.size < outbox.size
            outbox = (others + entry).toMutableList()
            if (replaced) writeLines(file("outbox"), outbox) else appendLine(file("outbox"), entry)
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

    /** A line of the journal: the start, the number, the seconds since the start and the time of day - a phone knows
     * the time. */
    private fun line(number: Int, event: String, fields: Map<String, Any?> = emptyMap()): JsonObject {
        val uptime = Math.round((System.nanoTime() - started) / 1e8) / 10.0
        return json(
            "n" to number, "start" to numbers.getValue("start"), "uptime" to uptime,
            "clock" to json("time" to timestamp(), "synchronized" to true), "version" to version, "event" to event,
        ).with(fields)
    }

    /** Lines for numbers whose lines are lost: a mark that the statistics ignore and that lets the library go on.
     * What was lost is not invented (docs/developer/library.md, section 7). */
    private fun gaps(lost: IntRange): List<JsonObject> = lost.map { line(it, RECOVERY_GAP) }

    /**
     * The lines that are complete. A number without its line - a damaged line, or a number that was saved but whose
     * line never reached the storage - becomes a [RECOVERY_GAP], and the file is written anew (_load_journal of
     * sync/data.py).
     */
    private fun loadJournal(): MutableList<JsonObject> {
        val (lines, broken) = readLines(file("journal"))
        var damaged = broken
        val result = mutableListOf<JsonObject>()
        val lost = mutableListOf<Int>()
        for (line in lines) {
            val number = line["n"].int ?: continue
            val last = result.lastOrNull()?.get("n").int
            if (last != null && number <= last) {
                damaged++ // not in order: a line that cannot be placed
                continue
            }
            if (last != null) {
                lost += last + 1 until number
                result += gaps(last + 1 until number)
            }
            result += line
        }
        val last = result.lastOrNull()?.get("n").int
        if (last != null) {
            if (last > numbers.getValue("journal")) {
                numbers["journal"] = last
                tidy("numbers.json") { saveNumbers() }
            }
            lost += last + 1..numbers.getValue("journal")
            result += gaps(last + 1..numbers.getValue("journal"))
        }
        if (damaged > 0 || lost.isNotEmpty()) {
            log("journal: $damaged damaged line(s); lines lost: ${ranges(lost).ifEmpty { "none" }} - noted as $RECOVERY_GAP")
            tidy("journal") { writeLines(file("journal"), result) }
        }
        return result
    }

    /**
     * Append a line (docs/developer/app.md, section 5, docs/developer/library.md, section 7). The number is saved first,
     * then the line is appended: an app ended in between leaves a number without its line, a [RECOVERY_GAP] on the
     * next start.
     */
    fun journal(event: String, fields: Map<String, Any?> = emptyMap()) {
        synchronized(lock) {
            numbers["journal"] = numbers.getValue("journal") + 1
            saveNumbers()
            val line = line(numbers.getValue("journal"), event, fields)
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

    /**
     * The library has the lines up to [confirmed], and the app has none of the lines after it up to its first one:
     * they are lost on both sides - a damaged journal, a library restored from a backup, a phone that was unpaired
     * and paired again (its lines went, its numbers stayed). They become [RECOVERY_GAP], so that the library takes the
     * lines that follow (journal_gap of sync/data.py).
     */
    fun journalGap(confirmed: Int) {
        synchronized(lock) {
            val first = journal.firstOrNull()?.get("n").int ?: return
            if (first <= confirmed + 1) return
            log("journal: lines ${ranges((confirmed + 1 until first).toList())} are lost - noted as $RECOVERY_GAP")
            journal = (gaps(confirmed + 1 until first) + journal).toMutableList()
            writeLines(file("journal"), journal)
        }
    }

    /** Unpaired: copy, outbox and journal go. The numbers stay - a number is never given twice; a library that waits
     * for the lines that went gets them as [RECOVERY_GAP] ([journalGap]). */
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

/** 1, 2, 3, 7 -> "1-3, 7" */
private fun ranges(numbers: List<Int>): String {
    val result = mutableListOf<IntRange>()
    for (number in numbers) {
        val last = result.lastOrNull()
        if (last != null && number == last.last + 1) result[result.size - 1] = last.first..number else result += number..number
    }
    return result.joinToString(", ") { if (it.first == it.last) "${it.first}" else "${it.first}-${it.last}" }
}
