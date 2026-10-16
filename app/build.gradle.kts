plugins {
    alias(libs.plugins.kotlin.jvm) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.android.application) apply false
}

// One version for everything (section 7 of the concept): the version of pyproject.toml. versionCode follows from it,
// 0.40.1 -> 40001, so that Android takes every release as an update.
val projectVersion: String = rootDir.resolve("../pyproject.toml").readLines()
    .first { it.startsWith("version = ") }.substringAfter('"').substringBefore('"')
val projectVersionCode: Int = projectVersion.split(".").map { it.toInt() }
    .let { (major, minor, patch) -> major * 1_000_000 + minor * 1_000 + patch }
extra["projectVersion"] = projectVersion
extra["projectVersionCode"] = projectVersionCode
