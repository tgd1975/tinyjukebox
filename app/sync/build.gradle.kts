// The counterpart of tinyjukebox.sync without Android: handshake, client with the pinned certificate, copy, outbox,
// journal, manifests and the cache. Tested on the PC, also against a real library (LibraryTest).
plugins {
    alias(libs.plugins.kotlin.jvm)
    application
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    implementation(libs.serialization.json)
    testImplementation(platform(libs.junit.bom))
    testImplementation(libs.junit.jupiter)
    testRuntimeOnly(libs.junit.launcher)
}

// the command-line program: pair, a pass, an album into a folder, a position, a journal line
application {
    mainClass.set("io.github.tgd1975.tinyjukebox.sync.MainKt")
}

tasks.test {
    useJUnitPlatform()
    // the root of the repository: the shared test vectors, and tinyjukebox-library for the tests against a library
    systemProperty("tinyjukebox.repository", rootDir.resolve("..").canonicalPath)
    systemProperty("tinyjukebox.version", rootProject.extra["projectVersion"] as String)
    testLogging { events("failed"); exceptionFormat = org.gradle.api.tasks.testing.logging.TestExceptionFormat.FULL }
}
