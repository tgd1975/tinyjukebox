package io.github.tgd1975.tinyjukebox.sync

import org.junit.jupiter.api.Assertions.assertArrayEquals
import org.junit.jupiter.api.Assertions.assertEquals
import org.junit.jupiter.api.Assertions.assertFalse
import org.junit.jupiter.api.Assertions.assertThrows
import org.junit.jupiter.api.Assertions.assertTrue
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.File

/** The identity of the phone: a pairing is complete or absent; a file that cannot be read means "not paired". */
class IdentityTest {
    @TempDir
    lateinit var folder: File

    private val certificate = JSON.parseToJsonElement(javaClass.getResource("/handshake.json")!!.readText()).obj!!["certificate"].string!!

    @Test
    fun `a pairing that was ended before its files were written is completed on start`() {
        writeJson(File(folder, Identity.PAIRING), json("library" to "https://nas:8866", "key" to "secret", "certificate" to certificate))
        File(folder, "library.crt").writeText(certificate) // the first of the three files, then the app was ended
        val identity = Identity(folder)
        assertTrue(identity.paired)
        assertEquals("https://nas:8866", identity.library)
        assertEquals("secret", identity.key)
        assertArrayEquals(Pem.decode(certificate), identity.certificate)
        assertFalse(File(folder, Identity.PAIRING).exists())
    }

    @Test
    fun `a file of a pairing that cannot be read is no pairing`() {
        File(folder, Identity.PAIRING).writeText("""{"library": "https://nas:8866", "ke""")
        val identity = Identity(folder)
        assertFalse(identity.paired)
        assertFalse(File(folder, Identity.PAIRING).exists())
    }

    @Test
    fun `a certificate that cannot be read means not paired`() {
        File(folder, "library").writeText("https://nas:8866\n")
        File(folder, "key").writeText("secret\n")
        File(folder, "library.crt").writeText("-----BEGIN CERTIFICATE-----\nMIIB*%\$x\n-----END CERTIFICATE-----\n")
        val identity = Identity(folder)
        assertEquals(null, identity.certificate)
        assertFalse(identity.paired)
        assertThrows(WrongCertificate::class.java) { identity.client("app-0.44.0") }
        File(folder, "library.crt").writeText("-----BEGIN CERTIFICATE-----\n-----END CERTIFICATE-----\n")
        assertFalse(identity.paired)
    }
}
