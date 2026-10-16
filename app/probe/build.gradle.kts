// The probe (step 0 of the concept of the app): one screen and a log - how a tag behaves on a phone. Not released.
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
}

android {
    namespace = "io.github.tgd1975.tinyjukebox.probe"
    compileSdk = 36
    defaultConfig {
        applicationId = "io.github.tgd1975.tinyjukebox.probe"
        minSdk = 26
        targetSdk = 36
        versionCode = 1
        versionName = "probe"
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

kotlin {
    jvmToolchain(17)
}
