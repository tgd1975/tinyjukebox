package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonObject
import java.io.File
import java.io.IOException
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

const val V = "/api/sync/v$INTERFACE"

/** Whether app and library fit together (docs/developer/library.md, section 5.2.5). */
object Fit {
    const val OK = "ok"
    const val APP_TOO_OLD = "app-too-old"
    const val LIBRARY_TOO_OLD = "library-too-old"
}

/**
 * The synchronization of the app with the library: one pass as a box makes it (tinyjukebox/sync/link.py,
 * docs/developer/library.md, section 4.17) - do we fit together; send the outbox; fetch what differs; drop what was
 * answered; the covers; deliver the journal; report the status.
 *
 * A pass runs on start, when a tag is put on, after a change and every minute while something plays; while the app is
 * visible it also listens to the events of the library ([listen]).
 */
class Sync(
    val identity: Identity,
    folder: File,
    albums: File,
    images: File,
    /** ``app-<version>``: how the app names itself towards the library. */
    val version: String,
    /** The name the device has at first: the model of the phone. */
    private val host: String,
    cacheLimit: Long,
    playing: () -> String? = { null },
    private val log: (String) -> Unit = {},
) {
    private val folder = folder.also { it.mkdirs() }
    val data = AppData(folder, identity.boxId, version)
    val cache = Cache(data, albums, folder, cacheLimit, { if (connected) client else null }, playing, log)
    val images = Images(data, images, log)
    private val passing = ReentrantLock()
    private var reported: JsonObject? = null
    private var said = ""

    @Volatile
    var client: Client? = null
        private set

    @Volatile
    var connected = false
        private set

    /** Why the app works without the library, empty while it is connected. */
    @Volatile
    var alone = ""
        private set

    /** ``ok``, or why app and library do not fit together; the state survives a restart. */
    @Volatile
    var fit: String = readText(File(folder, "fit")).ifEmpty { Fit.OK }
        private set

    /** The version of the library, as it said it last. */
    @Volatile
    var libraryVersion = ""
        private set

    /** The version of the app that the library hands out (/app.apk), empty if it has none. */
    @Volatile
    var offeredApp = ""
        private set

    private fun setFit(new: String) {
        if (new == fit) return
        fit = new
        writeAtomic(File(folder, "fit"), new + "\n")
        data.journal("fit", mapOf("state" to new))
    }

    /** One pass; returns whether another one should follow at once. Never throws: a failure means working alone. */
    fun pass(): Boolean = passing.withLock {
        try {
            val again = run()
            alone = ""
            said = ""
            again
        } catch (error: IOException) {
            connected = false
            alone = when (error) {
                is HttpError -> if (error.status == 401) "the library rejects the key of this phone" else "the library answered $error"
                is WrongCertificate -> error.message ?: "another certificate"
                else -> "the library cannot be reached: ${error.message}"
            }
            if (alone != said) log("working without the library: $alone")
            said = alone
            false
        }
    }

    private fun run(): Boolean {
        if (!identity.paired) throw Unreachable("not paired")
        val client = identity.client(version).also { this.client = it }
        val answer = client.get("/api/sync/interface").obj
        val serves = answer?.get("serves").array?.mapNotNull { it.int } ?: emptyList()
        libraryVersion = answer?.get("library").string ?: ""
        offeredApp = answer?.get("app").string ?: ""
        if (!connected) log("connected to the library")
        connected = true
        when {
            serves.isEmpty() -> throw Unreachable("the library names no interface")
            serves.max() < INTERFACE -> {
                setFit("${Fit.LIBRARY_TOO_OLD}: this app speaks version $INTERFACE, the library serves $serves")
                return false
            }
            serves.min() > INTERFACE -> {
                setFit("${Fit.APP_TOO_OLD}: this app speaks version $INTERFACE, the library serves $serves")
                return false
            }
        }
        setFit(Fit.OK)
        val done = push(client)
        val again = pull(client) ?: return true
        data.confirm(done)
        images.balance(client)
        deliverJournal(client)
        report(client)
        return again
    }

    /** Send the outbox in order; returns the numbers that the library answered (sections 4.4, 4.5). */
    private fun push(client: Client): Set<Int> {
        val done = mutableSetOf<Int>()
        for (batch in data.outbox().chunked(BATCH)) {
            val results = client.post("$V/boxes/${data.boxId}/changes", json("entries" to batch)).obj?.get("results").array
                ?: throw Unreachable("no results")
            for ((entry, result) in batch.zip(results)) {
                val kind = result.obj?.get("result").string
                if (kind == "waiting") continue
                done += entry["n"].int ?: continue
                when (kind) {
                    // a position the library changed on purpose: its value stays
                    "conflict" -> log("${result.obj?.get("what").string} was changed in the library, its value stays")
                    "rejected" -> {
                        log("the library rejected the change ${entry["n"]}: ${result.obj?.get("reason").string}")
                        data.journal("rejected", mapOf("entry" to entry, "reason" to result.obj?.get("reason")))
                    }
                }
            }
        }
        return done
    }

    /** Compare the checksums of the copy with the library and fetch every file that differs, whole (library.md, section 5.2.6).
     * Returns whether another pass should follow, null if this one could not finish. */
    private fun pull(client: Client): Boolean? {
        val files = client.get("$V/catalog").obj?.get("files").obj ?: throw Unreachable("no catalog")
        for ((name, info) in files) {
            if (name !in CATALOG_FILES || data.checksum(name) == info.obj?.get("sha256").string) continue
            val body = client.request("GET", "$V/catalog/$name").body
            if (sha256(body) != info.obj?.get("sha256").string) return null // the file changed in between: once more
            for (target in data.replaceCopy(name, body).removed) cache.removed(target)
            log("catalog file $name fetched")
        }
        val state = client.request("GET", "$V/boxes/${data.boxId}/state").body
        var again = false
        if (sha256(state) != data.checksum(STATE)) {
            for ((key, value, revision) in data.replaceCopy(STATE, state).lost) {
                log("the library lost the position $key of this phone: sent again")
                data.add("position", key, value, base = revision)
                again = true
            }
        }
        return again
    }

    /** Send the journal in batches; the library confirms up to which number it has the lines (section 7). */
    private fun deliverJournal(client: Client) {
        while (true) {
            val lines = data.journalLines()
            if (lines.isEmpty()) return
            val confirmed = client.post("$V/boxes/${data.boxId}/journal", json("lines" to lines)).obj?.get("confirmed").int
                ?: return
            data.journalConfirmed(confirmed)
            if (confirmed < (lines.last()["n"].int ?: 0)) return
        }
    }

    /** What runs here: the version, no slots and no update mode. */
    private fun report(client: Client) {
        val report = json("version" to version, "interface" to INTERFACE, "host" to host, "fit" to fit)
        if (report == reported) return
        client.put("$V/boxes/${data.boxId}/status", report)
        reported = report
    }

    /**
     * Listen to the events of the library until [stop] says so or the connection breaks; [event] is called for every
     * one, "connected" first. Throws [IOException] when the connection breaks.
     */
    fun listen(event: (String) -> Unit): Nothing {
        val client = identity.client(version, timeoutMs = 10_000)
        listening = client
        try {
            client.events("$V/events", event)
        } finally {
            listening = null
        }
    }

    @Volatile
    private var listening: Client? = null

    /** End [listen] from another thread. */
    fun stopListening() {
        listening?.interrupt()
    }

    /** "Unpair": key, certificate, copy, positions and cache go. The library keeps the device until it is removed. */
    fun unpair() = passing.withLock {
        identity.unpair()
        data.forgetLibrary()
        cache.clear()
        images.clear()
        File(folder, "fit").delete()
        fit = Fit.OK
        connected = false
        client = null
        reported = null
    }

    companion object {
        /** Entries of the outbox in one request. */
        const val BATCH = 20
    }
}
