package io.github.tgd1975.tinyjukebox

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.net.http.SslCertificate
import android.net.http.SslError
import android.nfc.NfcAdapter
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.view.Gravity
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JsPromptResult
import android.webkit.JsResult
import android.webkit.SslErrorHandler
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import io.github.tgd1975.tinyjukebox.sync.Client
import java.io.File

/**
 * Expert mode (docs/developer/app.md, section 2): the page of the library in a WebView. Nothing of the page is built a
 * second time. What the app adds around it: the certificate of the library and no other, "Scan tag" with the NFC of
 * the phone (the object TinyJukeboxApp, offered to the page of the library only), the file chooser, downloads, the
 * questions of the page, the back button, and links to elsewhere in the browser of the phone.
 *
 * The login of the page is the door out of simple mode; a logout - also after 30 minutes without a touch - leads back.
 */
class ExpertActivity : ComponentActivity(), NfcAdapter.ReaderCallback {
    private val app by lazy { TinyJukeboxApp.of(this) }
    private val main = Handler(Looper.getMainLooper())
    private lateinit var web: WebView
    private lateinit var banner: TextView
    private lateinit var settings: Button
    private lateinit var origin: Uri
    private var pinned: ByteArray? = null
    private var files: ValueCallback<Array<Uri>>? = null
    private var page: JavaScriptReplyProxy? = null
    private var scanning = false

    private val choose = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        val data = result.data
        val chosen = when {
            result.resultCode != RESULT_OK || data == null -> null
            data.clipData != null -> Array(data.clipData!!.itemCount) { data.clipData!!.getItemAt(it).uri }
            data.data != null -> arrayOf(data.data!!)
            else -> null
        }
        files?.onReceiveValue(chosen)
        files = null
    }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val identity = app.sync.identity
        if (!identity.paired) return finish()
        origin = Uri.parse(identity.library)
        pinned = identity.certificate
        app.expert = false
        // the login of the page is asked every time: a session of before is not taken over
        CookieManager.getInstance().removeAllCookies(null)

        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.settings.allowFileAccess = false
        web.settings.allowContentAccess = true
        web.webViewClient = Pages()
        web.webChromeClient = Questions()
        web.setDownloadListener { url, _, disposition, type, _ -> download(url, disposition, type) }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(web, "TinyJukeboxApp", setOf(originOf(origin))) { _, message, _, _, reply ->
                page = reply
                received(message.data ?: "")
            }
        }

        banner = TextView(this).apply {
            setBackgroundColor(0xfff6c419.toInt())
            setTextColor(0xff000000.toInt())
            setPadding(24, 16, 24, 16)
        }
        settings = Button(this).apply {
            text = getString(R.string.app_settings)
            isEnabled = false
            setOnClickListener { startActivity(Intent(this@ExpertActivity, SettingsActivity::class.java)) }
        }
        val simple = Button(this).apply { text = getString(R.string.simple_mode); setOnClickListener { leave() } }
        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(LinearLayout(this@ExpertActivity).apply {
                gravity = Gravity.CENTER_VERTICAL
                addView(simple)
                addView(settings)
            })
            addView(banner)
            addView(web, LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f))
        })
        showFit()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            // "back" closes a dialog of the page, or leaves expert mode
            override fun handleOnBackPressed() {
                web.evaluateJavascript(
                    "(() => { const open = document.querySelector('dialog[open]'); if (open) { open.close(); return true; } return false; })()",
                ) { closed -> if (closed != "true") leave() }
            }
        })
        web.loadUrl(origin.toString())
    }

    override fun onResume() {
        super.onResume()
        showFit()
        if (scanning) scan(true)
    }

    override fun onPause() {
        NfcAdapter.getDefaultAdapter(this)?.disableReaderMode(this)
        super.onPause()
    }

    override fun onDestroy() {
        app.expert = false
        if (::web.isInitialized) web.destroy()
        super.onDestroy()
    }

    /** Back to simple mode: the page logs out, its cookie is forgotten. */
    private fun leave() {
        app.expert = false
        web.evaluateJavascript("fetch('/api/logout', {method: 'POST', credentials: 'same-origin', headers: {'X-TinyJukebox-Request': '1'}})", null)
        main.postDelayed({
            CookieManager.getInstance().removeAllCookies(null)
            finish()
        }, 300)
    }

    private fun showFit() {
        if (!::banner.isInitialized) return
        val text = app.fitText()
        banner.text = text?.let { "$it  ${origin}/app.apk" } ?: ""
        banner.visibility = if (text == null) android.view.View.GONE else android.view.View.VISIBLE
        banner.setOnClickListener { open(Uri.parse("$origin/app.apk")) }
    }

    // --- what the page says (app.js, where TinyJukeboxApp is there) --------------------------------------------------

    private fun received(message: String) {
        when {
            message.startsWith("session:") -> {
                app.expert = true
                settings.isEnabled = true
            }
            message == "logout" -> {
                // the page logged out - by hand or after 30 minutes without a touch: the phone is the child's again
                app.expert = false
                CookieManager.getInstance().removeAllCookies(null)
                finish()
            }
            message == "scan-start" -> scan(true)
            message == "scan-stop" -> scan(false)
        }
    }

    /** "Scan tag" with the NFC of the phone: the page gets the UID, as from NDEFReader. */
    private fun scan(on: Boolean) {
        scanning = on
        val adapter = NfcAdapter.getDefaultAdapter(this) ?: return
        if (on) {
            adapter.enableReaderMode(
                this, this,
                NfcAdapter.FLAG_READER_NFC_A or NfcAdapter.FLAG_READER_NFC_B or NfcAdapter.FLAG_READER_NFC_F or
                    NfcAdapter.FLAG_READER_NFC_V or NfcAdapter.FLAG_READER_SKIP_NDEF_CHECK,
                null,
            )
        } else {
            adapter.disableReaderMode(this)
        }
    }

    override fun onTagDiscovered(tag: android.nfc.Tag) {
        val uid = tag.id.joinToString(":") { "%02X".format(it) }
        main.post { if (scanning) page?.postMessage(uid) }
    }

    // --- the page and nothing else ---------------------------------------------------------------------------------------

    private fun originOf(uri: Uri) = "${uri.scheme}://${uri.host}${if (uri.port > 0) ":${uri.port}" else ""}"

    private fun ours(uri: Uri) = uri.scheme == origin.scheme && uri.host == origin.host && uri.port == origin.port

    private fun open(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (error: ActivityNotFoundException) {
            Toast.makeText(this, uri.toString(), Toast.LENGTH_LONG).show()
        }
    }

    private fun der(certificate: SslCertificate): ByteArray? =
        if (Build.VERSION.SDK_INT >= 29) certificate.x509Certificate?.encoded
        else SslCertificate.saveState(certificate).getByteArray("x509-certificate")

    private inner class Pages : WebViewClient() {
        /** The self-signed certificate of the library: exactly the one that pairing gave, no other. */
        override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
            val presented = der(error.certificate)
            if (presented != null && pinned != null && presented.contentEquals(pinned) && ours(Uri.parse(error.url))) {
                handler.proceed()
            } else {
                handler.cancel()
                banner.text = getString(R.string.certificate_wrong)
                banner.visibility = android.view.View.VISIBLE
            }
        }

        /** A link to another address (Spotify, GitHub) opens in the browser of the phone. */
        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            if (ours(request.url)) return false
            open(request.url)
            return true
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (request.isForMainFrame) {
                banner.text = getString(R.string.not_reachable_page, error.description)
                banner.visibility = android.view.View.VISIBLE
            }
        }
    }

    private inner class Questions : WebChromeClient() {
        override fun onJsAlert(view: WebView, url: String, message: String, result: JsResult): Boolean {
            AlertDialog.Builder(this@ExpertActivity).setMessage(message)
                .setPositiveButton(R.string.ok) { _, _ -> result.confirm() }
                .setOnCancelListener { result.confirm() }.show()
            return true
        }

        override fun onJsConfirm(view: WebView, url: String, message: String, result: JsResult): Boolean {
            AlertDialog.Builder(this@ExpertActivity).setMessage(message)
                .setPositiveButton(R.string.ok) { _, _ -> result.confirm() }
                .setNegativeButton(R.string.cancel) { _, _ -> result.cancel() }
                .setOnCancelListener { result.cancel() }.show()
            return true
        }

        override fun onJsPrompt(view: WebView, url: String, message: String, value: String?, result: JsPromptResult): Boolean {
            val input = EditText(this@ExpertActivity).apply { setText(value ?: "") }
            AlertDialog.Builder(this@ExpertActivity).setMessage(message).setView(input)
                .setPositiveButton(R.string.ok) { _, _ -> result.confirm(input.text.toString()) }
                .setNegativeButton(R.string.cancel) { _, _ -> result.cancel() }
                .setOnCancelListener { result.cancel() }.show()
            return true
        }

        /** Uploads of albums and covers, a backup to put back: the file chooser of Android. */
        override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
            files?.onReceiveValue(null)
            files = callback
            val intent = params.createIntent().apply {
                if (params.mode == FileChooserParams.MODE_OPEN_MULTIPLE) putExtra(Intent.EXTRA_ALLOW_MULTIPLE, true)
            }
            return try {
                choose.launch(intent)
                true
            } catch (error: ActivityNotFoundException) {
                files = null
                false
            }
        }
    }

    // --- downloads -------------------------------------------------------------------------------------------------------

    /** A backup: fetched by the app itself, with the session of the page and the certificate, saved to "Downloads". */
    private fun download(url: String, disposition: String?, type: String?) {
        val uri = Uri.parse(url)
        if (!ours(uri)) return open(uri)
        if (Build.VERSION.SDK_INT < 29 &&
            checkSelfPermission(Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED
        ) {
            requestPermissions(arrayOf(Manifest.permission.WRITE_EXTERNAL_STORAGE), 2)
            return
        }
        val name = URLUtil.guessFileName(url, disposition, type)
        val cookie = CookieManager.getInstance().getCookie(url) ?: ""
        Toast.makeText(this, getString(R.string.downloading, name), Toast.LENGTH_SHORT).show()
        Thread {
            val part = File(cacheDir, "download.part")
            val error = runCatching {
                part.delete()
                val client = Client(originOf(origin), pinned, mapOf("Cookie" to cookie), timeoutMs = 120_000)
                client.request("GET", (uri.encodedPath ?: "/") + (uri.encodedQuery?.let { "?$it" } ?: ""), sink = part)
                save(part, name, type ?: "application/octet-stream")
            }.exceptionOrNull()
            part.delete()
            main.post {
                val text = if (error == null) getString(R.string.download_saved, name) else getString(R.string.download_failed, error.message)
                Toast.makeText(this, text, Toast.LENGTH_LONG).show()
            }
        }.start()
    }

    private fun save(file: File, name: String, type: String) {
        if (Build.VERSION.SDK_INT >= 29) {
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.MIME_TYPE, type)
                put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            }
            val target = contentResolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values) ?: error("no place in Downloads")
            contentResolver.openOutputStream(target)!!.use { output -> file.inputStream().use { it.copyTo(output) } }
        } else {
            @Suppress("DEPRECATION")
            val folder = Environment.getExternalStoragePublicDirectory(Environment.DIRECTORY_DOWNLOADS)
            folder.mkdirs()
            file.copyTo(File(folder, name), overwrite = true)
        }
    }
}
