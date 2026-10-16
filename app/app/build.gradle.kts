import java.util.Base64

// The app of Tiny Jukebox (docs/developer/library.md, section 4.21): simple mode - the phone as a box - and expert
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

dependencies {
    implementation(project(":sync"))
    implementation(libs.serialization.json)
    implementation(libs.androidx.core)
    implementation(libs.androidx.activity)
    implementation(libs.androidx.webkit)
    implementation(libs.media3.exoplayer)
    implementation(libs.media3.session)
    implementation(libs.zxing.embedded)
}
