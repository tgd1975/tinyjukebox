package io.github.tgd1975.tinyjukebox.sync

import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.io.File
import java.io.IOException
import java.net.URLEncoder
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList

/** The manifest of an album (tinyjukebox/sync/manifest.py): files with size and checksum, the playlist. */
class Manifest(val files: List<Entry>, val order: List<String>) {
    class Entry(val name: String, val size: Long, val sha256: String)

    private val byName = files.associateBy { it.name }

    fun entry(name: String): Entry? = byName[name]

    /** The files that are not played: the playlist, an image. Needed before the first track. */
    val extras: List<String> get() = files.map { it.name }.filter { it !in order }

    /** Changes whenever a file of the album or the order of its tracks changes (manifest.checksum). */
    val checksum: String
        get() {
            val lines = files.map { "${it.sha256} ${it.name}" }.sorted()
            return sha256((lines + "" + order).joinToString("\n").toByteArray(Charsets.UTF_8))
        }

    val size: Long get() = files.sumOf { it.size }

    fun toJson(): JsonObject = json(
        "files" to files.map { json("name" to it.name, "size" to it.size, "sha256" to it.sha256) },
        "order" to order,
    )

    companion object {
        fun of(value: JsonElement?): Manifest? {
            val manifest = value.obj ?: return null
            val files = manifest["files"].array?.mapNotNull { item ->
                val entry = item.obj ?: return@mapNotNull null
                val name = entry["name"].string ?: return@mapNotNull null
                if (!safe(name)) return null
                Manifest.Entry(name, entry["size"].long ?: return@mapNotNull null, entry["sha256"].string ?: return@mapNotNull null)
            } ?: return null
            val order = manifest["order"].array?.mapNotNull { it.string } ?: return null
            return Manifest(files, order)
        }

        /** A name of a manifest stays inside the album folder. */
        private fun safe(name: String): Boolean =
            name.isNotEmpty() && !name.startsWith("/") && '\\' !in name &&
                name.split("/").all { it.isNotEmpty() && it != ".." && it != "." }
    }
}

/**
 * The albums on the phone (docs/developer/app.md, section 6): the app holds no copy of the library, albums are loaded when
 * they are played and kept as a cache - the albums on demand of a box (tinyjukebox/sync/files.py,
 * docs/developer/library.md, section 4.8), with a limit of the size in place of the space a box keeps free - and
 * [KEEP_FREE] that stay free on the phone.
 *
 * Positions are no part of the cache: they are kept and sent whether the album is here or not.
 */
class Cache(
    private val data: AppData,
    private val albums: File,
    private val folder: File,
    /** Bytes the albums may take at most. */
    @Volatile var limit: Long,
    private val client: () -> Client?,
    /** The album that is playing: it never leaves the cache. */
    private val playing: () -> String?,
    private val log: (String) -> Unit = {},
    /** Bytes free on the storage of the phone. */
    private val free: () -> Long = { albums.usableSpace },
) {
    private val manifests = File(folder, "manifests")
    private val playedFile = File(folder, "played.json")
    private val lock = Any()
    private val wanted = mutableMapOf<String, Int>()
    private val fetching = mutableSetOf<String>()
    private val failed = mutableMapOf<String, String>()
    private val known = ConcurrentHashMap<String, File>() // the folder of an album as it was found last
    private val listeners = CopyOnWriteArrayList<(String) -> Unit>()

    init {
        manifests.mkdirs()
        albums.mkdirs()
    }

    // --- what is here ------------------------------------------------------------------------------------------------

    private fun value(album: String): JsonObject? = data.working("target")[album]?.takeIf { it["target-type"].string == "album" }

    /** The folder of an album; null if the catalog names one outside the cache. */
    fun folder(album: String): File? = inside(value(album)?.get("album-folder").string)?.also { known[album] = it }

    /** The folder [name] below the cache; null if it lies outside. */
    private fun inside(name: String?): File? {
        val folder = File(albums, name ?: return null).canonicalFile
        return folder.takeIf { it.path.startsWith(albums.canonicalPath + File.separator) }
    }

    fun file(album: String, name: String): File? = folder(album)?.let { File(it, name) }

    private fun manifestFile(album: String) = File(manifests, "$album.json")

    /** A file of the cache that cannot be read is not there: a manifest is fetched again, the order starts anew. */
    private fun read(file: File): JsonElement? = try {
        readJson(file)
    } catch (error: Exception) {
        log("the cache: ${file.name} cannot be read ($error)")
        null
    }

    fun manifest(album: String): Manifest? = Manifest.of(read(manifestFile(album)))

    /** The files the phone lacks: first the files that are not played, then the tracks in the order of the
     * playlist; null if the manifest was never fetched. */
    fun lacking(album: String): List<String>? {
        val manifest = manifest(album) ?: return null
        val folder = folder(album) ?: return null
        return (manifest.extras + manifest.order).filter { name ->
            val file = File(folder, name)
            !(file.isFile && file.length() == manifest.entry(name)?.size)
        }
    }

    /** Whether the track [track] of the playlist can be played: it is here, and so are the files that are not played. */
    fun ready(album: String, track: Int): Boolean {
        val manifest = manifest(album) ?: return false
        val lacking = lacking(album) ?: return false
        val needed = manifest.extras + manifest.order.drop(track).take(1)
        return needed.none { it in lacking }
    }

    /** The tracks of an album in the order of its playlist, once the manifest is here. */
    fun tracks(album: String): List<String> = manifest(album)?.order ?: emptyList()

    fun complete(album: String): Boolean = lacking(album)?.isEmpty() == true

    fun failed(album: String): String? = synchronized(lock) { failed[album] }

    fun fetching(): List<String> = synchronized(lock) { fetching.sorted() }

    /** [listener] (album) is called whenever a file of an album arrived or its download ended. */
    fun subscribe(listener: (String) -> Unit) = listeners.add(listener)

    fun unsubscribe(listener: (String) -> Unit) = listeners.remove(listener)

    private fun announce(album: String) = listeners.forEach { it(album) }

    /** The albums in the cache with what of them is here, for the settings of the app. */
    fun contents(): List<Triple<String, Long, Boolean>> =
        (manifests.listFiles() ?: emptyArray()).map { it.name.removeSuffix(".json") }.sorted().mapNotNull { album ->
            val folder = folder(album) ?: return@mapNotNull null
            Triple(album, size(folder), complete(album))
        }

    fun used(): Long = size(albums)

    private fun size(folder: File): Long = folder.walkTopDown().filter { it.isFile }.sumOf { it.length() }

    // --- the limit ---------------------------------------------------------------------------------------------------

    /** The albums in the order in which they were played last. */
    private fun order(): List<String> = read(playedFile).array?.mapNotNull { it.string } ?: emptyList()

    /** The album was played just now. The order is kept as an order. */
    fun played(album: String) = synchronized(lock) {
        writeJson(playedFile, JsonArray((order().filter { it != album } + album).map { JsonPrimitive(it) }))
    }

    /**
     * Before a file is loaded that does not fit - into the limit, or into the storage of the phone, of which
     * [KEEP_FREE] stay free: what no album of the catalog names leaves first ([removeStrays]), then the album that was
     * played longest ago - never the one that plays, never the one that is being loaded. An album bigger than the limit
     * is played and leaves with the next one.
     */
    fun makeRoom(needed: Long, keep: String) {
        removeStrays(keep)
        val order = synchronized(lock) { order() }
        val here = (manifests.listFiles() ?: emptyArray()).map { it.name.removeSuffix(".json") }
        val candidates = here.filter { it != keep && it != playing() && it !in fetching() }
            .sortedBy { order.indexOf(it) }
        for (album in candidates) {
            if (used() + needed <= limit && free() - needed >= KEEP_FREE) return
            leave(album)
            log("the cache: album $album left to make room")
        }
    }

    /**
     * Folders below the cache that no album of the catalog names - its folder was renamed in the library, it was
     * removed while it played - can never be played: they leave, and so do their manifests. Not the folder of the album
     * that plays or is being loaded, as it was found. A catalog without albums names nothing: then nothing leaves.
     */
    private fun removeStrays(keep: String) {
        val catalog = data.working("target").filterValues { it["target-type"].string == "album" }
        if (catalog.isEmpty()) return
        val busy = setOf(keep) + listOfNotNull(playing()) + fetching()
        val named = catalog.values.mapNotNull { inside(it["album-folder"].string) }.toSet() + busy.mapNotNull { known[it] }
        fun sweep(folder: File) {
            for (child in folder.listFiles() ?: emptyArray()) {
                val path = child.canonicalFile
                when {
                    path in named -> Unit
                    child.isDirectory && named.any { it.path.startsWith(path.path + File.separator) } -> sweep(child)
                    else -> {
                        child.deleteRecursively()
                        log("the cache: ${child.relativeTo(albums)} belongs to no album of the catalog - it left")
                    }
                }
            }
        }
        sweep(albums)
        for (file in manifests.listFiles() ?: emptyArray()) {
            val album = file.name.removeSuffix(".json")
            if (album !in catalog && album !in busy) file.delete()
        }
    }

    /** The files of an album leave the cache. The album stays in the catalog and its position stays. */
    fun leave(album: String) {
        folder(album)?.deleteRecursively()
        manifestFile(album).delete()
    }

    /** An album that was removed in the library leaves the cache - one that plays when it plays no more
     * ([removeStrays]). */
    fun removed(value: JsonObject) {
        val album = value["target-id"].text ?: return
        if (value["target-type"].string != "album" || album == playing()) return
        inside(value["album-folder"].string)?.deleteRecursively()
        manifestFile(album).delete()
        log("album $album was removed in the library: it left the cache")
    }

    /** Unpaired: the cache is empty. */
    fun clear() {
        albums.deleteRecursively()
        manifests.deleteRecursively()
        playedFile.delete()
        albums.mkdirs()
        manifests.mkdirs()
    }

    // --- loading -----------------------------------------------------------------------------------------------------

    /** Whether the files of an album changed in the library since they were loaded here, and the library can be
     * asked for them. */
    fun outdated(album: String): Boolean {
        val described = data.album(album)?.get("checksum").string ?: return false
        val manifest = manifest(album) ?: return false
        return manifest.checksum != described && client() != null
    }

    /**
     * An album whose files changed in the library is taken over when it is put on, not before and not while it plays:
     * the files that differ leave and are loaded again. Without the library the album plays as it is.
     */
    private fun takeOver(album: String) {
        if (!outdated(album)) return
        val client = client() ?: return
        val new = try {
            Manifest.of(client.get("$V/albums/$album/manifest")) ?: return
        } catch (error: Exception) {
            log("album $album plays as it is: its manifest cannot be fetched ($error)")
            return
        }
        val manifest = manifest(album) ?: return
        val folder = folder(album) ?: return
        for (entry in manifest.files) {
            if (new.entry(entry.name)?.sha256 == entry.sha256) continue
            File(folder, entry.name).delete()
            part(File(folder, entry.name)).delete()
        }
        writeJson(manifestFile(album), new.toJson())
        log("the files of album $album changed in the library")
    }

    /**
     * Load the album from the library, beginning with the track [track]. A second call for another track pulls that
     * one forward. [renew]: its tag was put on - files that changed in the library are taken over first ([outdated]).
     * The network is asked on a thread of its own, never on the one of the player.
     */
    fun need(album: String, track: Int, renew: Boolean = false) {
        synchronized(lock) {
            wanted[album] = track
            failed.remove(album)
            if (album in fetching) return
            fetching += album
        }
        Thread({ fetch(album, renew) }, "Cache.fetch-$album").apply { isDaemon = true }.start()
    }

    private fun fetch(album: String, renew: Boolean) {
        try {
            if (renew) takeOver(album)
            fetchFiles(album)
        } catch (error: Exception) {
            log("loading album $album is interrupted: $error")
            synchronized(lock) { failed[album] = error.message ?: error.toString() }
        } finally {
            synchronized(lock) { fetching -= album }
            announce(album)
        }
    }

    private fun fetchFiles(album: String) {
        val folder = folder(album) ?: throw Unreachable("album $album is not in the catalog")
        var manifest = manifest(album)
        if (manifest == null) {
            val client = client() ?: throw Unreachable("the album is not here, and the library cannot be reached")
            manifest = Manifest.of(client.get("$V/albums/$album/manifest")) ?: throw Unreachable("no manifest of $album")
            writeJson(manifestFile(album), manifest.toJson())
        }
        val attempts = mutableMapOf<String, Int>()
        val wrong = mutableSetOf<String>()
        while (true) {
            val missing = (lacking(album) ?: emptyList()).filter { it !in wrong }
            if (missing.isEmpty()) {
                if (wrong.isNotEmpty()) throw Unreachable("${wrong.size} files have a wrong checksum again and again")
                log("album $album is complete")
                return
            }
            val name = next(album, manifest, missing)
            val entry = manifest.entry(name) ?: return
            val target = File(folder, name)
            val part = part(target)
            target.parentFile.mkdirs()
            if (!part.isFile || part.length() < entry.size) {
                makeRoom(entry.size - part.length(), album) // the part is here already
                val client = client() ?: throw Unreachable("the library cannot be reached")
                client.request("GET", "$V/albums/$album/files/${quoted(name)}", sink = part) // continues a part that is there
                if (part.length() < entry.size) throw Unreachable("the transfer of $name broke off") // continued next time
            }
            // checked, also a complete part whose new name was not given yet when the app was ended
            if (part.length() == entry.size && sha256(part) == entry.sha256) {
                if (!part.renameTo(target)) throw IOException("cannot rename $part")
            } else {
                part.delete()
                val count = (attempts[name] ?: 0) + 1
                attempts[name] = count
                if (count < ATTEMPTS) {
                    log("file $name of album $album has a wrong checksum: asked for again")
                } else {
                    log("file $name of album $album has a wrong checksum again and again")
                    data.journal("file", mapOf("album" to album, "file" to name, "error" to "wrong checksum"))
                    wrong += name
                }
            }
            announce(album)
        }
    }

    /** Where a file is loaded to: it gets its name only after its checksum was verified. */
    private fun part(target: File) = File(target.parentFile, ".${target.name}.part")

    /** The file to load now: what is not played first, then the wanted track, then on in the playlist. */
    private fun next(album: String, manifest: Manifest, missing: List<String>): String {
        manifest.extras.firstOrNull { it in missing }?.let { return it }
        val order = manifest.order
        val start = synchronized(lock) { (wanted[album] ?: 0).coerceIn(0, maxOf(order.size - 1, 0)) }
        return (order.drop(start) + order.take(start)).first { it in missing }
    }

    companion object {
        /** A file whose checksum is wrong is requested again, this often. */
        const val ATTEMPTS = 3

        /** Bytes that stay free on the storage of the phone: albums leave the cache before it is full. */
        const val KEEP_FREE = 200L shl 20

        fun quoted(name: String): String = URLEncoder.encode(name, "UTF-8").replace("+", "%20")
    }
}

/** The covers of albums, stations and entries of Spotify: all of them once, and afterwards what has changed. */
class Images(private val data: AppData, val folder: File, private val log: (String) -> Unit = {}) {
    private val checksums = mutableMapOf<String, Triple<Long, Long, String>>()

    private fun checksum(file: File): String {
        val known = checksums[file.name]
        if (known != null && known.first == file.length() && known.second == file.lastModified()) return known.third
        return sha256(file).also { checksums[file.name] = Triple(file.length(), file.lastModified(), it) }
    }

    fun file(name: String): File? = File(folder, name).takeIf { it.isFile && name == File(name).name }

    /** Fetch the images that are missing or differ here, remove what the library removed. */
    fun balance(client: Client) {
        folder.mkdirs()
        for ((name, image) in data.images()) {
            if (name != File(name).name || name.startsWith(".")) continue
            val file = File(folder, name)
            if (image.obj?.get("deleted").boolean == true) {
                if (file.delete()) log("cover $name was removed in the library")
                continue
            }
            val expected = image.obj?.get("sha256").string ?: continue
            if (file.isFile && checksum(file) == expected) continue
            val part = File(folder, ".$name.part")
            part.delete()
            client.request("GET", "$V/images/${Cache.quoted(name)}", sink = part)
            if (sha256(part) == expected) part.renameTo(file) else part.delete()
        }
    }

    fun clear() {
        folder.deleteRecursively()
        checksums.clear()
    }
}
