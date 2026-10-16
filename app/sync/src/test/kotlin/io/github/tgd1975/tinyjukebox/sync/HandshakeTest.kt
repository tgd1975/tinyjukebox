package io.github.tgd1975.tinyjukebox.sync

import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test

/** The same test vectors as tests/unit/test_handshake.py: app and library compute the same proofs. */
class HandshakeTest {
    private val vectors = JSON.parseToJsonElement(
        javaClass.getResource("/handshake.json")!!.readText(),
    ).obj!!

    @Test
    fun `the proofs of the test vectors`() {
        val certificate = Pem.decode(vectors["certificate"].string!!)
        assertEquals(vectors["fingerprint"].string, Handshake.fingerprint(certificate))
        val cases = vectors["proofs"].array!!
        assertEquals(6, cases.size)
        for (case in cases.map { it.obj!! }) {
            val proof = Handshake.proof(
                case["password"].string!!, case["role"].string!!, case["box"].string!!,
                case["nonce_box"].string!!, case["nonce_library"].string!!, certificate,
            )
            assertEquals(case["proof"].string, proof, case.toString())
        }
    }

    @Test
    fun `a certificate goes to PEM and back`() {
        val pem = vectors["certificate"].string!!
        assertEquals(pem, Pem.encode(Pem.decode(pem)))
    }

    @Test
    fun `the QR code of the library`() {
        val pairing = Pairing.parse("tinyjukebox:pair?library=https%3A%2F%2Fnas%3A8866&password=Xq3-_a")!!
        assertEquals(Pairing("https://nas:8866", "Xq3-_a"), pairing)
        assertEquals(null, Pairing.parse("https://nas:8866/app.apk"))
        assertEquals(null, Pairing.parse("tinyjukebox:pair?library=http%3A%2F%2Fnas&password=x"))
        assertTrue(Handshake.same("ab", "ab"))
        assertFalse(Handshake.same("ab", "ac"))
    }
}
