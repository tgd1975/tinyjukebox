#!/bin/bash
# builds the installer of a box (docs/developer/deployment.md, section 5): deploy/install/install.sh with the files it
# brings along - the slot scripts with their service unit, the public key of the releases, /etc/asound.conf, the
# setup after the reboot and the launcher of rfid-reader - packed into it. One file that needs nothing else from the
# repository.
#   scripts/build_installer.sh [--out DIR]       -> DIR/install.sh (default dist/)
set -eu
cd "$(dirname "$0")/.."
OUT=dist
[ "${1:-}" = --out ] && OUT=$2
mkdir -p "$OUT"
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp deploy/slot/tinyjukebox-slot deploy/slot/tinyjukebox-start deploy/slot/tinyjukebox-functions \
    deploy/slot/tinyjukebox.service deploy/release.pub deploy/config/asound.conf \
    deploy/install/tinyjukebox-setup deploy/install/tinyjukebox-setup.service deploy/install/rfid-reader-launcher \
    "$STAGE/"
# the same archive for the same files: no time stamps, no owners
PAYLOAD=$(tar --sort=name --mtime=@0 --owner=0 --group=0 --numeric-owner -C "$STAGE" -cf - . | gzip -n -9 | base64 -w 76)
{
    sed -e "s/^VERSION=\"@VERSION@\"/VERSION=\"$VERSION\"/" -e '/^# @PAYLOAD@$/,$d' deploy/install/install.sh
    printf 'payload() {  # the files the installer brings along: tar.gz, base64\n    cat << '"'"'END'"'"'\n%s\nEND\n}\n\n' "$PAYLOAD"
    sed -n '/^# @PAYLOAD@$/,$p' deploy/install/install.sh | sed 1d
} > "$OUT/install.sh.new"
chmod 755 "$OUT/install.sh.new"
mv "$OUT/install.sh.new" "$OUT/install.sh"
echo "$OUT/install.sh: version $VERSION, $(wc -c < "$OUT/install.sh") bytes"
