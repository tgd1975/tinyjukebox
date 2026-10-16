#!/bin/bash
# builds the app for phones (app/, docs/developer/library.md, section 4.21): dist/tinyjukebox-app-<version>.apk
#   scripts/build_app.sh [--out DIR]
#   APP_SIGNING_KEY       the keystore of the app in Base64, which only the workflow has (section 8.2); without it the
#   APP_SIGNING_PASSWORD  APK is signed with the debug key of this computer, and a phone does not take it as an update
#                         of a released one
# Needs: Java 17 and the Android SDK (ANDROID_HOME).
set -eu
cd "$(dirname "$0")/.."
OUT=dist
[ "${1:-}" != --out ] || OUT=$2
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
[ -n "${APP_SIGNING_KEY:-}" ] || echo "note: no APP_SIGNING_KEY - the app is signed with the debug key" >&2
app/gradlew -p app --no-daemon -q :app:assembleRelease
mkdir -p "$OUT"
cp app/app/build/outputs/apk/release/app-release.apk "$OUT/tinyjukebox-app-$VERSION.apk"
echo "$OUT/tinyjukebox-app-$VERSION.apk"
