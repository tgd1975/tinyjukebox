// The app of Tiny Jukebox for Android (docs/developer/library.md, section 5.2): "sync" is plain Kotlin and tested on
// the PC, "app" is the Android app, "probe" a throw-away app that measures how a tag behaves on a phone.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "tinyjukebox-app"
include(":sync")
// the Android modules need the SDK; on a computer without it the sync module is built and tested alone
if (System.getenv("ANDROID_HOME") != null || file("local.properties").exists()) {
    listOf("app", "probe").filter { file(it).isDirectory }.forEach { include(":$it") }
}
