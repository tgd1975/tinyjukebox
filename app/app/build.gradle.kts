import java.net.URI
import java.security.MessageDigest
import java.util.Base64

// The app of Tiny Jukebox (docs/developer/app.md): simple mode - the phone as a box - and expert
// mode - the page of the library. The synchronization is the module sync.
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "io.github.tgd1975.tinyjukebox"
    compileSdk = 36
    defaultConfig {
        applicationId = "io.github.tgd1975.tinyjukebox"
        minSdk = 26
        targetSdk = 36
        versionName = rootProject.extra["projectVersion"] as String
        versionCode = rootProject.extra["projectVersionCode"] as Int
    }
    // Android refuses an update that is signed with another key: the releases are signed with APP_SIGNING_KEY of the
    // repository, a keystore in Base64 (docs/developer/library.md, section 8.2). Without it the build is signed with
    // the debug key of this computer.
    val keystore = System.getenv("APP_SIGNING_KEY")?.takeIf { it.isNotBlank() }?.let { key ->
        layout.buildDirectory.file("release.keystore").get().asFile.apply {
            parentFile.mkdirs()
            writeBytes(Base64.getMimeDecoder().decode(key))
        }
    }
    signingConfigs {
        if (keystore != null) {
            create("release") {
                storeFile = keystore
                storePassword = System.getenv("APP_SIGNING_PASSWORD") ?: ""
                keyAlias = System.getenv("APP_SIGNING_ALIAS") ?: "tinyjukebox"
                keyPassword = System.getenv("APP_SIGNING_PASSWORD") ?: ""
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.findByName("release") ?: signingConfigs.getByName("debug")
        }
    }
    buildFeatures {
        buildConfig = true
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    packaging {
        resources.excludes += setOf("META-INF/*.kotlin_module", "META-INF/versions/**", "DebugProbesKt.bin")
    }
}

kotlin {
    jvmToolchain(17)
}

// Spotify's App Remote SDK (Apache-2.0) is no package of Maven Central: Gradle fetches it from the release of Spotify's
// repository on GitHub and checks its checksum. Nothing of it lies in this repository.
val spotifySdkUrl =
    "https://github.com/spotify/android-sdk/releases/download/v0.8.0-appremote_v2.1.0-auth/spotify-app-remote-release-0.8.0.aar"
val spotifySdkSha256 = "b5a6dd880eaf01f63a871cba9ef7af77c341f8a94ffc8fdf2e9021f9a9d4c198"
val spotifySdk = layout.buildDirectory.file("spotify/spotify-app-remote-release-0.8.0.aar")
val fetchSpotifySdk by tasks.registering {
    val target = spotifySdk.get().asFile
    outputs.file(target)
    doLast {
        val part = File(target.path + ".part")
        part.parentFile.mkdirs()
        URI(spotifySdkUrl).toURL().openStream().use { input -> part.outputStream().use { input.copyTo(it) } }
        val sha256 = MessageDigest.getInstance("SHA-256").digest(part.readBytes())
            .joinToString("") { "%02x".format(it) }
        check(sha256 == spotifySdkSha256) { "the SDK of Spotify has another checksum: $sha256" }
        part.renameTo(target)
    }
}

dependencies {
    implementation(project(":sync"))
    implementation(libs.serialization.json)
    implementation(libs.androidx.core)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.webkit)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.session)
    implementation(libs.zxing.embedded)
    implementation(files(spotifySdk).builtBy(fetchSpotifySdk))
    implementation(libs.gson)
}
