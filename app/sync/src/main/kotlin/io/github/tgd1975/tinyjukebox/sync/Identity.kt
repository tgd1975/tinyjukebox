package io.github.tgd1975.tinyjukebox.sync

import java.io.File
import java.net.URLDecoder
import java.security.SecureRandom

/** What the QR code of the page of the library holds (tab "Boxes", "Add a phone"). */
data class Pairing(val library: String, val password: String) {
    companion object {
        private const val PREFIX = "tinyjukebox:pair?"

        /** The content of the QR code, or null if it is not one of the library. */
        fun parse(content: String): Pairing? {
            if (!content.startsWith(PREFIX)) return null
            val query = content.removePrefix(PREFIX).split("&").mapNotNull { part ->
                val (key, value) = part.split("=", limit = 2).takeIf { it.size == 2 } ?: return@mapNotNull null
                key to URLDecoder.decode(value, "UTF-8")
            }.toMap()
            val library = query["library"]?.trimEnd('/') ?: return null
            val password = query["password"] ?: return null
            return if (library.startsWith("https://") && password.isNotEmpty()) Pairing(library, password) else null
        }
    }
}

/**
 * What identifies this device to the library (docs/developer/library.md, section 4.7): its id, generated on the
 * first start, the address of the library, its own key and the certificate of the library. The folder is
 * ``identity/`` of docs/developer/app.md, section 6; nothing of it is in a backup of the phone.
 */
class Identity(val folder: File) {
    private val random = SecureRandom()

    /** The internal id: generated on the first start, never changed, typed by nobody. */
    val boxId: String by lazy {
        val file = File(folder, "box-id")
        readText(file).ifEmpty {
            val id = "b" + hex(ByteArray(4).also { random.nextBytes(it) })
            writeAtomic(file, id + "\n")
            id
        }
    }

    val library: String get() = readText(File(folder, "library"))
    val key: String get() = readText(File(folder, "key"))
    val certificate: ByteArray? get() = File(folder, "library.crt").takeIf { it.isFile }?.let { Pem.decode(it.readText()) }

    /** Whether the app is paired: it has an address, a key and the certificate of the library. */
    val paired: Boolean get() = library.isNotEmpty() && key.isNotEmpty() && certificate != null

    /**
     * Pair with the library: the handshake of section 4.14 with the one-time password of [pairing]. The handshake proves
     * the certificate of the library; from then on only this one is accepted. [name] is how the library names the
     * device at first - the model of the phone.
     */
    fun pair(pairing: Pairing, name: String) {
        val nonce = Handshake.newNonce()
        val first = Client(pairing.library)
        val nonceLibrary = first.post("/api/sync/boxes", json("box" to boxId, "nonce" to nonce)).obj?.get("nonce").string
            ?: throw WrongCertificate("the library did not answer the handshake")
        val certificate = first.peerCertificate ?: throw WrongCertificate("the library presented no certificate")
        val proof = Handshake.proof(pairing.password, "box", boxId, nonce, nonceLibrary, certificate)
        val answer = try {
            Client(pairing.library, certificate).post("/api/sync/boxes", json("box" to boxId, "proof" to proof, "name" to name)).obj
        } catch (error: HttpError) {
            if (error.status == 401) throw WrongCertificate("the pairing was rejected: the QR code is used up or expired")
            throw error
        }
        val expected = Handshake.proof(pairing.password, "library", boxId, nonce, nonceLibrary, certificate)
        if (!Handshake.same(answer?.get("proof").string ?: "", expected)) {
            throw WrongCertificate("the pairing failed: the other side does not know the password")
        }
        folder.mkdirs()
        writeAtomic(File(folder, "library.crt"), Pem.encode(certificate))
        writeAtomic(File(folder, "key"), (answer?.get("key").string ?: error("no key")) + "\n")
        writeAtomic(File(folder, "library"), pairing.library + "\n")
    }

    /** Forget the library: address, key and certificate. The id stays - a device set up again is a new one only when
     * the data of the app is deleted. */
    fun unpair() {
        for (name in listOf("library", "key", "library.crt")) File(folder, name).delete()
    }

    /** A client for the interface with the key of this device and the pinned certificate. */
    fun client(version: String, timeoutMs: Int = 10_000): Client {
        val headers = mapOf(
            "Authorization" to "Box $boxId:$key",
            "X-Box-Version" to version,
            "X-Box-Interface" to INTERFACE.toString(),
        )
        return Client(library, certificate ?: throw WrongCertificate("not paired"), headers, timeoutMs)
    }
}
