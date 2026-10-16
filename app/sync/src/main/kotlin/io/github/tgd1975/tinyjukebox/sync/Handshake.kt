package io.github.tgd1975.tinyjukebox.sync

import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The initial handshake with the library (docs/developer/library.md, section 4.14), as tinyjukebox/sync/handshake.py
 * does it. The app proves the one-time password of the QR code in place of the bootstrap password of a box.
 */
object Handshake {
    private val random = SecureRandom()

    fun newNonce(): String = hex(ByteArray(16).also { random.nextBytes(it) })

    /** SHA-256 of a certificate (DER). */
    fun fingerprint(certificate: ByteArray): String = sha256(certificate)

    /** Checksum that the side [role] ("box" or "library") can only compute with the password. */
    fun proof(password: String, role: String, box: String, nonceBox: String, nonceLibrary: String, certificate: ByteArray): String {
        val message = listOf(role, box, nonceBox, nonceLibrary, fingerprint(certificate)).joinToString("\n")
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(password.toByteArray(Charsets.UTF_8), "HmacSHA256"))
        return hex(mac.doFinal(message.toByteArray(Charsets.UTF_8)))
    }

    fun same(left: String, right: String): Boolean =
        MessageDigest.isEqual(left.toByteArray(Charsets.UTF_8), right.toByteArray(Charsets.UTF_8))
}
