package io.github.tgd1975.tinyjukebox.sync

import java.io.File
import kotlin.system.exitProcess

/** The folders of the app below one folder, as docs/developer/app.md, section 6 names them. */
fun syncOf(root: File, version: String, host: String = "tinyjukebox-sync", log: (String) -> Unit = {}): Sync = Sync(
    Identity(File(root, "identity")), File(root, "sync"), File(root, "albums"), File(root, "images"),
    "app-$version", host, cacheLimit = 4L shl 30, log = log,
)

private const val USAGE = """tinyjukebox-sync DIR COMMAND ...  - the sync module of the app on the PC
  pair QR [NAME]                     pair with the content of the QR code of the page of the library
  pass                               one pass of the synchronization
  catalog                            the tags and what they play
  fetch ALBUM                        load an album into DIR/albums
  position UID ALBUM TRACK SECONDS   the position of a tag moved on
  journal EVENT [KEY=VALUE ...]      a line of the journal
  unpair                             forget the library"""

fun main(arguments: Array<String>) {
    if (arguments.size < 2) {
        System.err.println(USAGE)
        exitProcess(2)
    }
    val version = System.getProperty("tinyjukebox.version") ?: System.getenv("TINYJUKEBOX_VERSION") ?: "0.0.0"
    val sync = syncOf(File(arguments[0]), version) { System.err.println(it) }
    val rest = arguments.drop(2)
    when (arguments[1]) {
        "pair" -> {
            val pairing = Pairing.parse(rest.firstOrNull() ?: "") ?: fail("this is no QR code of the library")
            sync.identity.pair(pairing, rest.getOrElse(1) { "tinyjukebox-sync" })
            println("paired with ${pairing.library} as ${sync.identity.boxId}")
            passed(sync)
        }
        "pass" -> passed(sync)
        "catalog" -> {
            passed(sync)
            val targets = sync.data.working("target")
            for ((uid, tag) in sync.data.working("tag")) {
                val target = tag["target"]
                val what = if (AppData.unlinked(target)) "not linked"
                else targets[target.obj?.get("id").text]?.let { it["title"].string ?: it["album-folder"].string ?: it["name"].string }
                println("$uid\t${target.obj?.get("type").string}\t${target.obj?.get("id").text}\t$what")
            }
        }
        "fetch" -> {
            val album = rest.firstOrNull() ?: fail(USAGE)
            passed(sync)
            val done = Object()
            sync.cache.subscribe { if (it == album) synchronized(done) { (done as Object).notifyAll() } }
            sync.cache.need(album, 0)
            synchronized(done) {
                while (album in sync.cache.fetching() || !sync.cache.complete(album) && sync.cache.failed(album) == null) {
                    (done as Object).wait(500)
                }
            }
            sync.cache.failed(album)?.let { fail("album $album: $it") }
            sync.cache.played(album)
            println("album $album is in ${sync.cache.folder(album)}")
        }
        "position" -> {
            if (rest.size < 4) fail(USAGE)
            val (uid, album, track, seconds) = rest
            sync.data.add(
                "position", uid,
                json(
                    "tag-uid" to uid, "target-id" to album, "track-id" to track.toInt(), "track-time" to seconds.toDouble(),
                    "target-type" to "album", "album-finished" to false,
                ),
            )
            passed(sync)
            println("position of $uid: track $track, $seconds s")
        }
        "journal" -> {
            val event = rest.firstOrNull() ?: fail(USAGE)
            sync.data.journal(event, rest.drop(1).associate { it.substringBefore("=") to it.substringAfter("=") })
            passed(sync)
            println("journal line $event delivered")
        }
        "unpair" -> sync.unpair().also { println("unpaired") }
        else -> fail(USAGE)
    }
}

private fun passed(sync: Sync) {
    while (sync.pass()) Unit
    if (!sync.connected) fail("no pass: ${sync.alone}")
    if (sync.fit != Fit.OK) fail(sync.fit)
}

private fun fail(message: String): Nothing {
    System.err.println(message)
    exitProcess(1)
}
