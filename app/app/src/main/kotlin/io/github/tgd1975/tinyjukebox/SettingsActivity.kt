package io.github.tgd1975.tinyjukebox

import android.app.AlertDialog
import android.content.Intent
import android.graphics.Typeface
import android.os.Bundle
import android.text.InputType
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import io.github.tgd1975.tinyjukebox.sync.string

/**
 * The settings of the app, behind the login of expert mode: the name of the device as the library has it, the cache
 * with its limit and content, the versions, the licences, and "Unpair".
 */
class SettingsActivity : ComponentActivity() {
    private val app by lazy { TinyJukeboxApp.of(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!app.expert) return finish() // only from expert mode, while the page is logged in
        show()
    }

    private fun show() {
        val sync = app.sync
        val column = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(48, 48, 48, 48)
        }
        fun heading(text: String) = column.addView(TextView(this).apply {
            this.text = text
            textSize = 20f
            typeface = Typeface.DEFAULT_BOLD
            setPadding(0, 32, 0, 8)
        })
        fun line(text: String) = column.addView(TextView(this).apply { this.text = text; textSize = 15f; setPadding(0, 4, 0, 4) })

        heading(getString(R.string.settings_title))
        line(getString(R.string.settings_name, sync.data.setting("name").string ?: sync.identity.boxId))
        line(getString(R.string.settings_library, sync.identity.library))
        line(if (sync.connected) getString(R.string.settings_connected) else getString(R.string.settings_alone, sync.alone))
        app.fitText()?.let { line(it) }

        heading(getString(R.string.settings_versions))
        line(getString(R.string.settings_app_version, BuildConfig.VERSION_NAME))
        line(getString(R.string.settings_library_version, sync.libraryVersion.ifEmpty { getString(R.string.settings_unknown) }))
        sync.offeredApp.takeIf { it.isNotEmpty() }?.let { line(getString(R.string.settings_library_offers, it)) }

        heading(getString(R.string.settings_cache))
        line(getString(R.string.settings_cache_help))
        val limit = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_NUMBER or InputType.TYPE_NUMBER_FLAG_DECIMAL
            hint = getString(R.string.settings_cache_limit)
            setText("%.1f".format(java.util.Locale.ROOT, app.cacheLimit() / (1L shl 30).toFloat()))
        }
        column.addView(LinearLayout(this).apply {
            addView(TextView(this@SettingsActivity).apply { text = getString(R.string.settings_cache_limit) })
            addView(limit, LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f))
            addView(Button(this@SettingsActivity).apply {
                text = getString(R.string.settings_save)
                setOnClickListener {
                    limit.text.toString().replace(',', '.').toFloatOrNull()?.takeIf { it > 0 }?.let {
                        app.setCacheLimit(it)
                        Toast.makeText(this@SettingsActivity, R.string.settings_saved, Toast.LENGTH_SHORT).show()
                        show()
                    }
                }
            })
        })
        line(getString(R.string.settings_cache_used, size(sync.cache.used()), size(app.cacheLimit())))
        val targets = sync.data.working("target")
        val contents = sync.cache.contents()
        if (contents.isEmpty()) line(getString(R.string.settings_cache_empty))
        for ((album, bytes, complete) in contents) {
            val title = app.jukebox.title(targets[album]) ?: album
            line("$title – ${size(bytes)}" + if (complete) "" else ", " + getString(R.string.settings_incomplete))
        }

        heading(getString(R.string.settings_spotify))
        val client = sync.data.setting("spotify-client").string
        line(
            when {
                !SpotifyPlayer.installed(this) -> getString(R.string.settings_spotify_app_missing)
                client.isNullOrBlank() -> getString(R.string.settings_spotify_none)
                else -> getString(R.string.settings_spotify_client, client)
            },
        )
        column.addView(TextView(this).apply {
            text = getString(R.string.settings_spotify_help, packageName, signingSha1(), SpotifyPlayer.REDIRECT)
            textSize = 13f
            setTextIsSelectable(true)
            setPadding(0, 4, 0, 4)
        })

        heading(getString(R.string.settings_licences))
        column.addView(Button(this).apply {
            text = getString(R.string.settings_licences)
            setOnClickListener { licences() }
        })

        column.addView(Button(this).apply {
            text = getString(R.string.settings_unpair)
            setOnClickListener { unpair() }
        })
        setContentView(ScrollView(this).apply { addView(column) })
    }

    /** The SHA-1 of the certificate this app is signed with: Spotify asks for it at the registration. */
    @Suppress("DEPRECATION")
    private fun signingSha1(): String {
        val signatures = if (android.os.Build.VERSION.SDK_INT >= 28) {
            packageManager.getPackageInfo(packageName, android.content.pm.PackageManager.GET_SIGNING_CERTIFICATES)
                .signingInfo?.apkContentsSigners
        } else {
            packageManager.getPackageInfo(packageName, android.content.pm.PackageManager.GET_SIGNATURES).signatures
        }
        val first = signatures?.firstOrNull() ?: return "?"
        return java.security.MessageDigest.getInstance("SHA-1").digest(first.toByteArray())
            .joinToString(":") { "%02X".format(it) }
    }

    /** In the units of the limit: 4 GB are 4 x 2^30 bytes, as the limit is set. */
    private fun size(bytes: Long): String = when {
        bytes >= 1L shl 30 -> "%.1f GB".format(bytes / (1L shl 30).toDouble())
        bytes >= 1L shl 20 -> "%.0f MB".format(bytes / (1L shl 20).toDouble())
        else -> "%.0f kB".format(bytes / 1024.0)
    }

    /** The libraries in the APK with their licences (README.md names them as well). */
    private fun licences() {
        val text = assets.open("licenses.txt").bufferedReader().readText() + "\n\n" +
            assets.open("LICENSE-2.0.txt").bufferedReader().readText()
        setContentView(ScrollView(this).apply {
            addView(TextView(this@SettingsActivity).apply {
                this.text = text
                textSize = 12f
                setTextIsSelectable(true)
                setPadding(32, 32, 32, 32)
            })
        })
    }

    /** "Unpair": key, certificate, copy, positions and cache go. The app is empty and asks for a QR code. */
    private fun unpair() {
        AlertDialog.Builder(this).setMessage(R.string.settings_unpair_confirm)
            .setPositiveButton(R.string.yes) { _, _ ->
                app.jukebox.tagOff()
                Thread {
                    app.sync.unpair()
                    runOnUiThread {
                        app.expert = false
                        startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
                        finish()
                    }
                }.start()
            }
            .setNegativeButton(R.string.no, null).show()
    }
}
