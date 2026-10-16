#!/bin/bash
# tries the installer of a box (deploy/install/install.sh) and the start script under systemd on a stand-in for a box:
# a container with Debian trixie for aarch64 and systemd as its first process (emulated if the computer is not
# aarch64). No real box is touched.
#   deploy/install/test/run.sh SLOT-ARCHIVE [--keep]
# SLOT-ARCHIVE: tinyjukebox-box-<version>-aarch64-debian-trixie.tar.gz with its .sig and .json next to it, signed with
# the key whose public part is deploy/release.pub (scripts/build_slot_aarch64.sh --key
# ~/.config/tinyjukebox/release.key). RELEASE_KEY (default ~/.config/tinyjukebox/release.key) signs the two deliberately
# defective versions of the test.
#
# What it checks:
#   1. the installer runs through on a fresh system - packages, sudoers rules, firmware, sound, Bluetooth, slots - and
#      asks for the reboot; the box starts the version of the archive under systemd; stage 1 of the health check
#      passes; a restart of the service is a matter of seconds
#   2. a second run of the installer changes nothing
#   3. a version that crashes on start is left at once, a version that hangs at the time limit of stage 1; the
#      version before runs again, and the service stays active
# Takes a while emulated: the installer installs the packages of a box (ffmpeg, BlueZ, PipeWire). The stand-in has no
# sound card, no reader and no library: the application logs errors of ALSA, which belong to the stand-in. --keep leaves
# the container (tinyjukebox-box-test) for a look inside.
set -eu
ARCHIVE=$(realpath "${1:?usage: run.sh SLOT-ARCHIVE [--keep]}")
cd "$(dirname "$0")"
KEEP=${2:-}
KEY=${RELEASE_KEY:-$HOME/.config/tinyjukebox/release.key}
NAME=tinyjukebox-box-test
REPO=$(realpath ../../..)
LIMIT=40  # time limit of stage 1 in the test: the real version needs about 25 s under emulation

box() { docker exec "$NAME" "$@"; }
slot() { box sudo -u pi tinyjukebox-slot "$@"; }
say() { printf '\n== %s\n' "$*"; }
fail() { printf 'FAILED: %s\n' "$*" >&2; slot status >&2 || true; exit 1; }
healthy() {  # the service was (re)started: wait until the version that runs now passed stage 1
    sleep 5  # the start script removes the note of the start before
    wait_for 150 box test -e /srv/tinyjukebox/state/stage1
}
wait_for() {  # seconds command...
    local limit=$1 start=$SECONDS
    shift
    until "$@" > /dev/null 2>&1; do
        [ $((SECONDS - start)) -lt "$limit" ] || return 1
        sleep 1
    done
}
put() { docker exec -i "$NAME" sh -c "cat > '$1'"; }  # docker cp cannot write into memory
install() {  # the installer of the clone, with the answers as files; status 10: it asks for a reboot
    local status=0
    box /tmp/install.sh --yes --no-reboot --no-start --admin-password-file /tmp/admin --stage1-limit $LIMIT "$@" \
        > "$OUTPUT" 2>&1 || status=$?
    tail -n 3 "$OUTPUT"
    return $status
}
OUTPUT=$(mktemp)

say "the stand-in"
docker build -q --platform linux/arm64 -t $NAME - < Dockerfile > /dev/null
docker rm -f $NAME > /dev/null 2>&1 || true
[ "$KEEP" = --keep ] || trap 'docker rm -f $NAME > /dev/null 2>&1' EXIT
# everything the test writes lies in memory
docker run -d --name $NAME --platform linux/arm64 --privileged --cgroupns=host -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
    --tmpfs /run --tmpfs /run/lock --tmpfs /tmp:exec --tmpfs /srv:exec,size=1g --tmpfs /var/log $NAME > /dev/null
wait_for 60 box systemctl is-active multi-user.target || fail "systemd does not come up in the stand-in"

INSTALLER=$(mktemp -d)
"$REPO/scripts/build_installer.sh" --out "$INSTALLER" > /dev/null
put /tmp/install.sh < "$INSTALLER/install.sh"
rm -rf "$INSTALLER"
box chmod 755 /tmp/install.sh
echo "password-of-the-test" | put /tmp/admin
for file in "$ARCHIVE" "$ARCHIVE.sig" "${ARCHIVE%.tar.gz}.json"; do put "/tmp/$(basename "$file")" < "$file"; done
# the config of the stand-in, which the installer never writes - no drivers, the web page on a port that needs no
# capability; and the output of the start script in a file, because the stand-in has no journal
box mkdir -p /srv/tinyjukebox/config /etc/systemd/system/tinyjukebox.service.d
put /srv/tinyjukebox/config/tinyjukebox.yaml << 'END'
logger:
    log-level: INFO
    log-file: /srv/tinyjukebox/log/tinyjukebox.log
tinyjukebox:
    albums-folder: /srv/tinyjukebox/albums
    playlist-filename: playlist.m3u
    volume: {max-volume: 82, default-volume: 50, volume-step: 5}
    albumplayer: {rewind-max-time: 10, update-schedule: 5, pause-between-tracks: 0}
    json:
        tags: /srv/tinyjukebox/json/tags.json
        targets: /srv/tinyjukebox/json/targets.json
        activities: /srv/tinyjukebox/json/activity.json
    rest-api:
        port: 8443
        certificate: /srv/tinyjukebox/ssl/tinyjukebox.crt
        private-key: /srv/tinyjukebox/ssl/tinyjukebox.key
        users-file: /srv/tinyjukebox/ssl/users.yaml
END
printf '[Service]\nStandardOutput=append:/srv/tinyjukebox/log/start.log\nStandardError=inherit\n' \
    | put /etc/systemd/system/tinyjukebox.service.d/output.conf

say "1. the installer, the first start"
status=0
install --archive "/tmp/$(basename "$ARCHIVE")" || status=$?
[ $status = 10 ] || fail "the installer ended with $status, not with 10 (reboot for config.txt)"
box chown -R pi:pi /srv/tinyjukebox/config
for rule in rfid restart trigger avahi; do box visudo -cqf /etc/sudoers.d/tinyjukebox-$rule || fail "sudoers rule $rule"; done
box grep -q '^dtoverlay=hifiberry-dac' /boot/firmware/config.txt || fail "config.txt has no block"
box sudo -u pi tinyjukebox-users -f /srv/tinyjukebox/ssl/users.yaml list | grep -q '^admin (admin)' || fail "no user admin"
box systemctl restart tinyjukebox
healthy || fail "stage 1 of the health check did not pass"
[ "$(box systemctl is-active tinyjukebox)" = active ] || fail "the service is not active"
[ "$(box readlink /srv/tinyjukebox/current)" = slots/a ] || fail "slot a is not the active one"
slot status | grep -q '^\* a .* probation' || fail "the first version is not on probation"
slot status | grep -q '^  fallback ' || fail "the first version is not the fallback version"
[ "$(box readlink -f /usr/local/bin/rfid-reader)" = /srv/tinyjukebox/slots/a/bin/rfid-reader ] || fail "rfid-reader is not the one of the slot"
start=$SECONDS
box systemctl restart tinyjukebox
[ $((SECONDS - start)) -lt 20 ] || fail "a restart of the service took $((SECONDS - start)) s"
echo "the version of the slot runs; a restart took $((SECONDS - start)) s"
healthy || fail "stage 1 did not pass after the restart"
# what the application does after an album played because of a tag - the stand-in has neither reader nor sound
slot _passed 2

say "2. the installer again"
install || fail "the second run of the installer failed"
grep -q "Nothing changed" "$OUTPUT" || fail "the second run changed something"
! slot status | grep -q '^  next ' || fail "the second run installed something"
[ "$(box systemctl is-active tinyjukebox)" = active ] || fail "the service is not active after the second run"

say "3. defective versions"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK" "$OUTPUT"; [ "$KEEP" = --keep ] || docker rm -f $NAME > /dev/null 2>&1' EXIT
for fault in crash hang; do
    version=99.0.$([ $fault = crash ] && echo 1 || echo 2)
    archive=$(./defective_slot.sh "$ARCHIVE" $version $fault "$KEY" "$WORK")
    for file in "$archive" "$archive.sig" "${archive%.tar.gz}.json"; do
        docker exec -i $NAME sh -c "cat > /tmp/$(basename "$file")" < "$file"  # docker cp cannot write into memory
    done
    slot update "/tmp/$(basename "$archive")" --by test | tail -n 1
    start=$SECONDS
    box systemctl restart tinyjukebox
    wait_for $((LIMIT + 60)) box grep -q "b:$version.*health check failed" /srv/tinyjukebox/state/history.log \
        || fail "the box did not go back from the version that would $fault"
    echo "$fault: the box went back after $((SECONDS - start)) s"
    healthy || fail "the version before did not come up again"
    [ "$(box readlink /srv/tinyjukebox/current)" = slots/a ] || fail "slot a is not the active one"
    [ "$(box systemctl is-active tinyjukebox)" = active ] || fail "the service is not active"
    slot status | grep -q "$version *manual" || fail "version $version is not manual"
    box grep -q -x "$version" /srv/tinyjukebox/state/discard || fail "the data of version $version is not marked as not trusted"
done
say "passed"
slot status
