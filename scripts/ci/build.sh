#!/bin/bash
# builds what a release holds into dist/: the signed slot for the boxes, the package of the library for the NAS with
# this slot in it, the installer of a box (install.sh), the app for phones, wheel and source archive, and checksums
# (docs/developer/library.md, section 8)
#   scripts/ci/build.sh            everything, one part after the other
#   scripts/ci/build.sh slot       the folder of the slot for aarch64, built in a container (build_slot_aarch64.sh):
#                                  not signed, not packed
#   scripts/ci/build.sh sign       signs and packs it (build_slot.sh --pack), and checks both signatures as a box does
#                                  SIGNING_KEY   the private key of the releases (PEM), which only master gets
#   scripts/ci/build.sh app        the app for phones (scripts/build_app.sh)
#                                  APP_SIGNING_KEY, APP_SIGNING_PASSWORD   its keystore, which only master gets
#   scripts/ci/build.sh package    the installer, the package with slot, installer and app, wheel, source archive and
#                                  SHA256SUMS
# Without a key, a key is made for this run: signing and checking are tried on every branch, and no box and no phone
# trusts what it signs. The workflow runs the parts as steps of their own, each with the one secret it needs: no
# program of others - the packages built from their source, uv, Gradle and its plugins - runs while the key of the
# releases is there.
set -eu
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
SLOT=dist/tinyjukebox-box-$VERSION-aarch64-debian-trixie

slot() {
    rm -rf dist
    mkdir dist
    scripts/build_slot_aarch64.sh --out dist --unpacked
}

KEYS=$(mktemp -d)  # for this user only
trap 'rm -rf "$KEYS"' EXIT

sign() {
    local public
    if [ -n "${SIGNING_KEY:-}" ]; then
        printf '%s\n' "$SIGNING_KEY" > "$KEYS/release.key"
        public=deploy/release.pub  # the key of the boxes: a secret that does not fit it stops the release here
    else
        echo "note: no SIGNING_KEY - the slot is signed with a key made for this run, which no box trusts" >&2
        openssl genpkey -algorithm EC -pkeyopt ec_paramgen_curve:P-256 -out "$KEYS/release.key" 2> /dev/null
        openssl pkey -in "$KEYS/release.key" -pubout -out "$KEYS/release.pub"
        public=$KEYS/release.pub
    fi
    unset SIGNING_KEY
    scripts/build_slot.sh --pack "$SLOT" --out dist --key "$KEYS/release.key"
    rm -f "$KEYS/release.key"
    rm -rf "$SLOT"
    # as a box checks the archive, and as the launcher of rfid-reader checks the list of checksums in it
    openssl dgst -sha256 -verify "$public" -signature "$SLOT.tar.gz.sig" "$SLOT.tar.gz"
    tar -xzf "$SLOT.tar.gz" -C "$KEYS" ./SHA256SUMS ./SHA256SUMS.sig
    openssl dgst -sha256 -verify "$public" -signature "$KEYS/SHA256SUMS.sig" "$KEYS/SHA256SUMS"
}

app() {
    local password apksigner
    if [ -z "${APP_SIGNING_KEY:-}" ] && command -v keytool > /dev/null; then
        echo "note: no APP_SIGNING_KEY - the app is signed with a keystore made for this run, which no phone takes as an update" >&2
        password=$(openssl rand -hex 16)
        keytool -genkeypair -keystore "$KEYS/app.keystore" -storetype PKCS12 -alias tinyjukebox -keyalg RSA \
            -keysize 2048 -validity 2 -dname CN=tinyjukebox-run -storepass "$password" -keypass "$password" \
            > /dev/null 2>&1
        APP_SIGNING_KEY=$(base64 -w0 "$KEYS/app.keystore")
        APP_SIGNING_PASSWORD=$password
        export APP_SIGNING_KEY APP_SIGNING_PASSWORD
        rm -f "$KEYS/app.keystore"
    fi
    scripts/build_app.sh --out dist
    unset APP_SIGNING_KEY APP_SIGNING_PASSWORD
    # the signature of the app, as a phone checks it - where the Android SDK brings apksigner
    apksigner=$(find "${ANDROID_HOME:-/nonexistent}/build-tools" -name apksigner -type f 2> /dev/null | sort -V | tail -n 1)
    if [ -n "$apksigner" ]; then
        "$apksigner" verify "dist/tinyjukebox-app-$VERSION.apk"
    else
        echo "note: no apksigner (ANDROID_HOME) - the signature of the app is not checked" >&2
    fi
}

package() {
    scripts/build_installer.sh --out dist
    ./build_package.sh 1 --slots dist
    uv build --quiet --out-dir dist  # wheel and source archive: PyPI and the image (scripts/build_image.sh)
    (cd dist && sha256sum -- * > SHA256SUMS)
    ls -l dist
}

case ${1:-all} in
    slot | sign | app | package) "$1" ;;
    all) slot; sign; app; package ;;
    *) echo "usage: scripts/ci/build.sh [slot | sign | app | package]" >&2; exit 1 ;;
esac
