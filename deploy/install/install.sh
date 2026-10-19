#!/bin/bash
# The installer of a Tiny Jukebox box (docs/developer/deployment.md, section 5): sets up the system of a fresh
# Raspberry Pi OS Lite (64-bit, Debian 13 "trixie"), installs the first version into a slot and starts the box.
#
#   curl -fsSL https://github.com/tgd1975/tinyjukebox/releases/latest/download/install.sh | sudo bash
#   curl -fsSLk --pinnedpubkey 'sha256//...' https://nas:8866/install.sh | sudo bash -s -- --library https://nas:8866
#
# The same line can be run any number of times: what is right already stays unchanged. It never touches the data of
# the box (albums, tags, positions, users, its config, its key for the library) and never changes which version
# runs. It leaves alone what the Raspberry Pi Imager sets: hostname, user, WLAN, time zone, locale, SSH.
#
# Options:
#   --user NAME              the user that runs the service (default: the user who called sudo)
#   --version X.Y.Z          the version to install first (default: the version of this installer)
#   --archive FILE           install this slot archive instead of fetching one (FILE.sig and the .json next to it)
#   --library URL            link the box to this library: catalog, albums and versions come from there
#   --bootstrap-file FILE    the bootstrap password of the library (otherwise it is asked for)
#   --admin-password-file F  the password of the user admin of the web page (otherwise it is asked for)
#   --no-admin               no user for the web page (the playbook adds its own)
#   --token-file FILE        a token that may read the releases of a private repository; --no-token removes it
#   --releases URL           where a box without a library finds its versions (default: the repository of the project)
#   --wait-days N            days a release waits before such a box installs it by itself (default 3)
#   --stage1-limit SECONDS   time a new version has to pass stage 1 of its health check (default 120)
#   --bluealsa-args ARGS     options of BlueALSA
#   --disable "UNIT ..."     timers and services that are disabled (default: automatic updates, and what slows the start)
#   --no-slots               set up the system only (a box that runs the code of its repository: the playbook)
#   --no-start               do not start the service
#   --yes                    no question before the reboot
#   --no-reboot              leave a reboot that is needed to the caller: exit status 10
#   --root DIR               work on the files below DIR and run no command of the system (the tests)
#
# What it printed is in /var/log/tinyjukebox-install.log, also what the setup after a reboot did.
set -u -o pipefail

VERSION="@VERSION@"  # filled in by scripts/build_installer.sh
SETUP_LEVEL=1        # level of the setup this installer makes; a version may ask for a higher one (slot.conf)
REPOSITORY=https://api.github.com/repos/tgd1975/tinyjukebox

ROOT=
USER_NAME=${SUDO_USER:-}
WANT=
ARCHIVE=
LIBRARY=
BOOTSTRAP_FILE=
ADMIN_FILE=
ADMIN=1
TOKEN_FILE=
NO_TOKEN=0
RELEASES=$REPOSITORY
WAIT_DAYS=3
STAGE1_LIMIT=120
BLUEALSA_ARGS="-S -p a2dp-source -p hfp-ag --a2dp-volume --a2dp-force-audio-cd"
DISABLE="apt-daily.timer apt-daily-upgrade.timer man-db.timer NetworkManager-wait-online.service ModemManager.service
triggerhappy.service keyboard-setup.service console-setup.service"
SLOTS=1
START=1
YES=0
NO_REBOOT=0

BASE_PATH=/srv/tinyjukebox
LIB_PATH=/usr/local/lib/tinyjukebox
LOG_PATH=/var/log/tinyjukebox-install.log
BOOT_PATH=/boot/firmware
# what a box needs to run (docs/developer/box.md, section 4); libasound2 comes with alsa-utils
PACKAGES="ffmpeg alsa-utils liblgpio1 openssl curl jq util-linux rsync avahi-daemon bluez bluez-alsa-utils rfkill"
# Spotify: Soloist plays only into PipeWire; the box starts its own instance
PACKAGES_SPOTIFY="pipewire wireplumber libatomic1"
SOUND_SERVERS="pipewire.socket pipewire.service wireplumber.service pipewire-pulse.socket pipewire-pulse.service"
COMMANDS="tinyjukebox tinyjukebox-users tinyjukebox-import-cards tinyjukebox-migrate-01 tinyjukebox-fill-library rfid-reader"

CHANGES=()
REBOOT=0
ADMIN_PASSWORD=
BOOTSTRAP=

# --- helpers ---------------------------------------------------------------------------------------------------------

say() { printf '%s\n' "$*"; }
step() { printf '\n== %s\n' "$*"; }
die() { printf 'tinyjukebox-install: %s\n' "$*" >&2; exit 1; }
changed() { CHANGES+=("$1"); say "   changed: $1"; }
# a command of the system - not with --root, where only files are written
system() { [ -n "$ROOT" ] || "$@"; }
as_user() { if [ -n "$ROOT" ]; then "$@"; else runuser -u "$USER_NAME" -- "$@"; fi; }

# put PATH MODE OWNER [validate]: the content of stdin becomes the file, if it differs. Status 0 if it was written
put() {
    local path=$ROOT$1 mode=$2 owner=$3 validate=${4:-} new
    new=$(mktemp)
    cat > "$new"
    if [ -f "$path" ] && cmp -s "$new" "$path"; then
        rm -f "$new"
        chmod "$mode" "$path"
        return 1
    fi
    if [ "$validate" = sudoers ] && ! "$(command -v visudo || echo /usr/sbin/visudo)" -cqf "$new" > /dev/null; then
        rm -f "$new"
        die "$1: the rule does not pass visudo"
    fi
    mkdir -p "$(dirname "$path")"
    install -m "$mode" "$new" "$path"
    rm -f "$new"
    [ -n "$ROOT" ] || chown "$owner" "$path"
    changed "$1"
}

folder() {  # path mode owner
    local path=$ROOT$1
    if [ ! -d "$path" ]; then
        mkdir -p "$path"
        changed "$1/"
    fi
    chmod "$2" "$path"
    [ -n "$ROOT" ] || chown "$3" "$path"
}

link() {  # target path
    [ "$(readlink "$ROOT$2" 2> /dev/null)" = "$1" ] && return 1
    mkdir -p "$(dirname "$ROOT$2")"
    ln -sfn "$1" "$ROOT$2"
    changed "$2"
}

payload_file() {  # name: one file of what the installer brings along
    cat "$PAYLOAD/$1"
}

ask_secret() {  # prompt minimum: read twice from the terminal, not from the pipe the script came through
    local first second
    [ -r /dev/tty ] || die "no terminal to ask: give the answer as a file (see --help)"
    while :; do
        read -r -s -p "$1: " first < /dev/tty; printf '\n' > /dev/tty
        if [ ${#first} -lt "$2" ]; then
            printf 'at least %s characters\n' "$2" > /dev/tty
            continue
        fi
        read -r -s -p "again: " second < /dev/tty; printf '\n' > /dev/tty
        [ "$first" = "$second" ] && break
        printf 'the two do not match\n' > /dev/tty
    done
    printf '%s' "$first"
}

read_secret() {  # file minimum what
    local value
    value=$(head -n 1 "$1") || die "$1 cannot be read"
    [ ${#value} -ge "$2" ] || die "$3 in $1 needs at least $2 characters"
    printf '%s' "$value"
}

# --- the steps (docs/developer/deployment.md, section 5.1) --------------------------------------------------------------

check() {
    step "Check"
    [ -n "$ROOT" ] || [ "$(id -u)" = 0 ] || die "run it with sudo"
    local id version arch
    id=$(. "$ROOT/etc/os-release" 2> /dev/null && echo "$ID") || die "no /etc/os-release"
    version=$(. "$ROOT/etc/os-release" && echo "$VERSION_ID")
    arch=${ARCH:-$(uname -m)}
    [ "$id" = debian ] && [ "$version" = 13 ] && [ "$arch" = aarch64 ] \
        || die "expected Raspberry Pi OS Lite (64-bit), Debian 13 on aarch64 - found $id $version on $arch. Nothing was changed"
    [ -f "$ROOT$BOOT_PATH/config.txt" ] || die "no $BOOT_PATH/config.txt: this is not Raspberry Pi OS. Nothing was changed"
    [ -n "$USER_NAME" ] && [ "$USER_NAME" != root ] || die "name the user that runs the service: --user NAME"
    [ -n "$ROOT" ] || id "$USER_NAME" > /dev/null 2>&1 || die "there is no user $USER_NAME"
    local free
    free=$(df --output=avail -B1M "$ROOT/" | tail -n 1 | tr -d ' ')
    [ "$free" -gt 1024 ] || die "less than 1 GB free on the card"
    say "   $id $version on $arch, user $USER_NAME, $free MB free"
}

packages() {
    step "Packages"
    [ -z "$ROOT" ] || return 0
    local missing=() missing_spotify=() package
    for package in $PACKAGES; do
        dpkg-query -W -f='${Status}' "$package" 2> /dev/null | grep -q 'ok installed' || missing+=("$package")
    done
    for package in $PACKAGES_SPOTIFY; do
        dpkg-query -W -f='${Status}' "$package" 2> /dev/null | grep -q 'ok installed' || missing_spotify+=("$package")
    done
    [ ${#missing[@]} -gt 0 ] || [ ${#missing_spotify[@]} -gt 0 ] || { say "   all there"; return 0; }
    # never an upgrade: only what is missing; a fresh card may still be busy with its own apt run for a while
    local apt=(apt-get -q -y -o DPkg::Lock::Timeout=600)
    DEBIAN_FRONTEND=noninteractive "${apt[@]}" update || die "apt-get update failed"
    if [ ${#missing[@]} -gt 0 ]; then
        DEBIAN_FRONTEND=noninteractive "${apt[@]}" install "${missing[@]}" || die "the packages cannot be installed"
    fi
    if [ ${#missing_spotify[@]} -gt 0 ]; then
        DEBIAN_FRONTEND=noninteractive "${apt[@]}" install --no-install-recommends "${missing_spotify[@]}" \
            || die "the packages for Spotify cannot be installed"
    fi
    changed "packages: ${missing[*]} ${missing_spotify[*]}"
}

groups() {
    step "Groups of the user $USER_NAME"
    [ -z "$ROOT" ] || return 0
    local group
    for group in gpio audio bluetooth; do
        getent group "$group" > /dev/null || continue
        id -nG "$USER_NAME" | tr ' ' '\n' | grep -qx "$group" && continue
        usermod -a -G "$group" "$USER_NAME"
        changed "group $group"
    done
}

root_rights() {
    step "What the box does as root"
    # the service runs as the user and needs sudo for exactly these commands - also on a system whose sudo asks for a
    # password (docs/developer/box.md, section 7)
    printf '%s ALL=(root) NOPASSWD: /usr/local/bin/rfid-reader\n' "$USER_NAME" \
        | put /etc/sudoers.d/tinyjukebox-rfid 0440 root:root sudoers
    printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl restart tinyjukebox\n' "$USER_NAME" \
        | put /etc/sudoers.d/tinyjukebox-restart 0440 root:root sudoers
    local b=$BOOT_PATH
    printf '%s ALL=(root) NOPASSWD: /usr/bin/rm -f %s %s %s %s\n%s ALL=(root) NOPASSWD: /usr/bin/tee %s\n' \
        "$USER_NAME" "$b/tinyjukebox-revert" "$b/tinyjukebox-revert.txt" "$b/tinyjukebox-fallback" \
        "$b/tinyjukebox-fallback.txt" "$USER_NAME" "$b/tinyjukebox-ergebnis.txt" \
        | put /etc/sudoers.d/tinyjukebox-trigger 0440 root:root sudoers
    printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl %s avahi-daemon.socket avahi-daemon.service\n' \
        "$USER_NAME" stop "$USER_NAME" start | put /etc/sudoers.d/tinyjukebox-avahi 0440 root:root sudoers
}

robustness() {
    step "Robustness"
    # the box is switched off by cutting the power: write little, start fast (docs/developer/box.md, section 11)
    printf '[Journal]\nStorage=volatile\n' | put /etc/systemd/journald.conf.d/tinyjukebox.conf 0644 root:root \
        && system systemctl restart systemd-journald
    # what is not written with fsync (the log file) is on the card after at most about 6 s instead of 35 s
    printf 'vm.dirty_expire_centisecs = 500\nvm.dirty_writeback_centisecs = 100\n' \
        | put /etc/sysctl.d/90-tinyjukebox.conf 0644 root:root \
        && system sysctl -q -p /etc/sysctl.d/90-tinyjukebox.conf
    local fstab=$ROOT/etc/fstab new
    if [ -f "$fstab" ]; then
        new=$(awk '!/^[[:space:]]*#/ && $2 == "/" && $4 !~ /(^|,)noatime(,|$)/ { $4 = $4 ",noatime" } { print }' "$fstab")
        if [ "$new" != "$(cat "$fstab")" ]; then
            printf '%s\n' "$new" > "$fstab"
            changed "/etc/fstab: noatime"
            system mount -o remount /
        fi
    fi
    local unit
    for unit in $DISABLE; do
        [ -z "$ROOT" ] || continue
        systemctl list-unit-files --no-legend "$unit" 2> /dev/null | grep -q . || continue
        if systemctl is-enabled --quiet "$unit" 2> /dev/null || systemctl is-active --quiet "$unit"; then
            systemctl disable --now "$unit" > /dev/null 2>&1
            changed "$unit disabled"
        fi
    done
}

firmware() {
    step "Firmware settings"
    local config=$ROOT$BOOT_PATH/config.txt new
    # onboard audio and the vc4 driver off (HDMI would add sound cards of its own); the block for the hardware at
    # the end - also in place of the block that the playbook wrote before the installer existed
    new=$(awk '
        /^# BEGIN Tiny Jukebox/ { skip = 1; next }
        /^# END Tiny Jukebox/ { skip = 0; next }
        skip { next }
        /^dtparam=audio=on/ || /^dtoverlay=vc4-kms-v3d/ { print "#" $0; next }
        { print }' "$config")
    new+=$'\n'"# BEGIN Tiny Jukebox
[all]
# rfid reader
dtparam=spi=on
# onboard audio off, Audio Amp SHIM
dtparam=audio=off
dtoverlay=hifiberry-dac
gpu_mem=16
disable_splash=1
boot_delay=0
# END Tiny Jukebox"
    [ "$new" = "$(cat "$config")" ] && return 0
    # a reboot only if more than the comments changed
    [ "$(grep -v '^#' <<< "$new")" = "$(grep -v '^#' "$config")" ] || REBOOT=1
    printf '%s\n' "$new" > "$config"
    changed "$BOOT_PATH/config.txt"
}

sound() {
    step "Sound"
    payload_file asound.conf | put /etc/asound.conf 0644 root:root
    # the sound servers of the packages for Spotify would take the sound card and Bluetooth for every user who logs in
    local unit
    for unit in $SOUND_SERVERS; do
        link /dev/null "/etc/systemd/user/$unit" || true
    done
    # the volume control Softmaster exists only after the device was opened once - after a reboot, when the sound
    # card is there: a unit that runs before the service does it, and nothing from then on
    payload_file tinyjukebox-setup | put "$LIB_PATH/tinyjukebox-setup" 0755 root:root || true
    if payload_file tinyjukebox-setup.service | put /etc/systemd/system/tinyjukebox-setup.service 0644 root:root; then
        system systemctl daemon-reload
    fi
    system systemctl enable --quiet tinyjukebox-setup.service
    [ $REBOOT = 1 ] || system "$LIB_PATH/tinyjukebox-setup"
}

bluetooth() {
    step "Bluetooth"
    if printf '[Service]\nExecStart=\nExecStart=/usr/bin/bluealsa %s\n' "$BLUEALSA_ARGS" \
        | put /etc/systemd/system/bluealsa.service.d/override.conf 0644 root:root; then
        system systemctl daemon-reload
        system systemctl restart bluealsa
    fi
    [ -z "$ROOT" ] || return 0
    systemctl enable --now --quiet bluetooth bluealsa
    # a blocked adapter cannot be switched on by BlueZ; systemd-rfkill keeps the state over a reboot
    if rfkill --noheadings --output SOFT list bluetooth | grep -qw blocked; then
        rfkill unblock bluetooth
        changed "bluetooth unblocked"
    fi
    # it plays sound from a phone - the box is the sender
    if systemctl is-enabled --quiet bluealsa-aplay 2> /dev/null; then
        systemctl disable --now --quiet bluealsa-aplay
        changed "bluealsa-aplay disabled"
    fi
}

folders() {
    step "Folders"
    local owner=$USER_NAME:$USER_NAME name
    folder "$BASE_PATH" 0755 "$owner"
    for name in albums json log config; do folder "$BASE_PATH/$name" 0755 "$owner"; done
    folder "$BASE_PATH/ssl" 0700 "$owner"  # private key, password hashes
    for name in tags targets activity; do
        [ -e "$ROOT$BASE_PATH/json/$name.json" ] && continue
        printf '{}\n' | put "$BASE_PATH/json/$name.json" 0644 "$owner"
    done
    [ $SLOTS = 1 ] || return 0
    for name in slots state sync; do folder "$BASE_PATH/$name" 0755 "$owner"; done
    folder "$BASE_PATH/identity" 0700 "$owner"  # the key of this box for the library
}

slots() {
    step "Slot scripts and service"
    # outside the slots: they change only through the installer, so a version with a defective updater cannot lock the
    # box in (docs/developer/library.md, section 6.11)
    folder "$LIB_PATH" 0755 root:root
    payload_file tinyjukebox-functions | put "$LIB_PATH/tinyjukebox-functions" 0644 root:root || true
    payload_file tinyjukebox-slot | put "$LIB_PATH/tinyjukebox-slot" 0755 root:root || true
    payload_file tinyjukebox-start | put "$LIB_PATH/tinyjukebox-start" 0755 root:root || true
    link "$LIB_PATH/tinyjukebox-slot" /usr/local/bin/tinyjukebox-slot || true
    link "$LIB_PATH/tinyjukebox-start" /usr/local/bin/tinyjukebox-start || true
    local command
    for command in $COMMANDS; do link "$BASE_PATH/current/bin/$command" "/usr/local/bin/$command" || true; done
    cat << END | put "$BASE_PATH/slot.conf" 0644 "$USER_NAME:$USER_NAME" || true
# values of this box for tinyjukebox-slot and the start script - written by the installer
STAGE1_LIMIT=$STAGE1_LIMIT   # seconds a new version may take to pass stage 1 of its health check
SETUP_LEVEL=$SETUP_LEVEL     # level of the setup that the installer made: a version may ask for a higher one
RESTART_COMMAND="sudo systemctl restart tinyjukebox"
# a box without a library finds its versions here (docs/developer/library.md, section 6.16); empty: nowhere
RELEASES="$RELEASES"
WAIT_DAYS=$WAIT_DAYS        # days a release waits before such a box installs it by itself
END
    # a slot is trusted because of its signature, not because of where it came from
    payload_file release.pub | put "$BASE_PATH/release.pub" 0644 root:root || true
    if payload_file tinyjukebox.service | sed "s/^User=.*/User=$USER_NAME/" \
        | put /etc/systemd/system/tinyjukebox.service 0644 root:root; then
        system systemctl daemon-reload
    fi
    system systemctl enable --quiet tinyjukebox.service
}

source_of_versions() {
    step "Where the versions come from"
    local owner=$USER_NAME:$USER_NAME
    if [ -n "$LIBRARY" ]; then
        printf '%s\n' "$LIBRARY" | put "$BASE_PATH/identity/library" 0644 "$owner" || true
        # only a box without a key gets the password: after its handshake the box deletes it
        if [ ! -e "$ROOT$BASE_PATH/identity/key" ] && [ -n "$BOOTSTRAP" ]; then
            printf '%s\n' "$BOOTSTRAP" | put "$BASE_PATH/identity/bootstrap" 0600 "$owner" || true
        fi
        say "   the library $LIBRARY"
    elif [ -s "$ROOT$BASE_PATH/identity/library" ]; then
        say "   the library $(cat "$ROOT$BASE_PATH/identity/library") - the box stays linked to it"
    else
        say "   the releases at $RELEASES"
    fi
    if [ -n "$TOKEN_FILE" ]; then
        head -n 1 "$TOKEN_FILE" | put "$BASE_PATH/identity/releases-token" 0600 "$owner" || true
    elif [ $NO_TOKEN = 1 ] && [ -e "$ROOT$BASE_PATH/identity/releases-token" ]; then
        rm -f "$ROOT$BASE_PATH/identity/releases-token"
        changed "$BASE_PATH/identity/releases-token removed"
    fi
}

first_version() {
    step "The first version"
    if [ -x "$ROOT$BASE_PATH/current/bin/tinyjukebox" ] || [ -x "$ROOT$BASE_PATH/slots/next/bin/tinyjukebox" ]; then
        say "   the box has a version: it stays (versions come from the library or the releases, as always)"
        [ -n "$ARCHIVE" ] && install_archive "$ARCHIVE"
        return 0
    fi
    local version=${WANT:-$VERSION}
    if [ -n "$ARCHIVE" ]; then
        install_archive "$ARCHIVE"
    elif [ -n "$(library_of_box)" ]; then
        from_library "$(library_of_box)"
    else
        [ "$version" != "@VERSION@" ] || die "this installer was not built (scripts/build_installer.sh): give --archive or --version"
        [ -n "$RELEASES" ] || die "no address of releases and no library: give --archive"
        slot_command update "$version" || die "version $version cannot be installed from $RELEASES"
    fi
}

# users.yaml: "users:" with one indented line per user (tinyjukebox.web.auth.UsersFile)
has_users() { grep -qs '^  [^ ]' "$ROOT$BASE_PATH/ssl/users.yaml"; }

library_of_box() { cat "$ROOT$BASE_PATH/identity/library" 2> /dev/null; }

slot_command() {
    local output
    output=$(as_user "$LIB_PATH/tinyjukebox-slot" ${ROOT:+--root "$ROOT"} "$@" --yes --by installer 2>&1)
    local status=$?
    printf '%s\n' "$output" | sed 's/^/   /'
    [ $status = 0 ] || return 1
    grep -q 'is installed' <<< "$output" && changed "version installed"
    return 0
}

install_archive() {  # FILE with FILE.sig and its description next to it
    local file=$1 folder name
    [ -f "$file" ] && [ -f "$file.sig" ] || die "$file and $file.sig are needed"
    folder=$(mktemp -d)
    name=$(basename "$file")
    cp "$file" "$file.sig" "$folder/"
    [ -f "${file%.tar.gz}.json" ] && cp "${file%.tar.gz}.json" "$folder/"
    chmod -R a+rX "$folder"
    slot_command update "$folder/$name" || { rm -rf "$folder"; die "$name cannot be installed"; }
    rm -rf "$folder"
}

from_library() {  # URL: the slot that the library offers for an installation - without credentials (section 3.9)
    local url=$1 folder description version platform base
    folder=$(mktemp -d)
    description=$folder/description.json
    # the slot is trusted because of its signature; the line already pinned the certificate of the library
    curl -fsSk "$url/api/sync/install" -o "$description" || die "the library $url offers no version for an installation"
    version=$(jq -r .version "$description")
    platform=$(jq -r .platform "$description")
    base=$folder/tinyjukebox-box-$version-$platform
    mv "$description" "$base.json"
    say "   version $version from the library"
    curl -fsSk "$url/api/sync/install/slot" -o "$base.tar.gz" || die "the slot cannot be fetched from $url"
    curl -fsSk "$url/api/sync/install/signature" -o "$base.tar.gz.sig" || die "the signature cannot be fetched from $url"
    chmod -R a+rX "$folder"
    slot_command update "$base.tar.gz" || { rm -rf "$folder"; die "version $version cannot be installed"; }
    rm -rf "$folder"
}

fallback() {
    # a box that was set up before the slot script made the first version its fallback version gets the confirmed
    # version it runs (docs/developer/library.md, section 6.10)
    [ -z "$ROOT" ] || return 0
    local status
    status=$(as_user "$LIB_PATH/tinyjukebox-slot" status --json 2> /dev/null) || return 0
    jq -e '(.slots | has("fallback") | not) and ((.slots[.active] // {}).state == "confirmed")' <<< "$status" \
        > /dev/null 2>&1 || return 0
    as_user "$LIB_PATH/tinyjukebox-slot" promote --by installer > /dev/null && changed "fallback version"
}

users() {
    step "User of the web page"
    local tool slot
    if has_users; then
        say "   the page has users already"
        return 0
    fi
    [ -n "$ADMIN_PASSWORD" ] || { say "   no user (--no-admin)"; return 0; }
    for slot in current slots/next slots/fallback; do
        tool=$ROOT$BASE_PATH/$slot/bin/tinyjukebox-users
        [ -x "$tool" ] && break
    done
    [ -x "$tool" ] || die "no version on the box to create the user with"
    printf '%s' "$ADMIN_PASSWORD" | as_user "$tool" -f "$BASE_PATH/ssl/users.yaml" --password-stdin add admin --role admin \
        > /dev/null || die "the user admin cannot be created"
    changed "user admin of the web page"
}

start() {
    local address
    address="https://$(hostname 2> /dev/null || echo tinyjukebox).local/"
    if [ $REBOOT = 1 ]; then
        step "Reboot"
        if [ $NO_REBOOT = 1 ]; then
            say "   the box needs a reboot for the firmware settings"
            return 10
        fi
        say "   the firmware settings need a reboot. Afterwards the page answers at $address"
        if [ $YES = 0 ] && [ -r /dev/tty ]; then
            read -r -p "restart the box now? [Y/n] " answer < /dev/tty
            case $answer in n* | N*) say "   restart it later: sudo reboot"; return 0 ;; esac
        fi
        system systemctl reboot
        return 0
    fi
    [ $START = 1 ] && [ $SLOTS = 1 ] && [ -z "$ROOT" ] || return 0
    step "Start"
    if [ ${#CHANGES[@]} -gt 0 ] || ! systemctl is-active --quiet tinyjukebox; then
        system systemctl restart tinyjukebox
    fi
    local waited=0
    until curl -ksf -o /dev/null https://localhost/api/identity; do
        [ $waited -lt 180 ] || die "the page does not answer - see journalctl -u tinyjukebox and $BASE_PATH/log/tinyjukebox.log"
        sleep 3
        waited=$((waited + 3))
    done
    say "   the box runs. Its page: $address"
}

# --- the run ---------------------------------------------------------------------------------------------------------

options() {
    while [ $# -gt 0 ]; do
        case $1 in
            --user) USER_NAME=$2; shift ;;
            --version) WANT=$2; shift ;;
            --archive) ARCHIVE=$(realpath "$2"); shift ;;
            --library) LIBRARY=${2%/}; shift ;;
            --bootstrap-file) BOOTSTRAP_FILE=$2; shift ;;
            --admin-password-file) ADMIN_FILE=$2; shift ;;
            --no-admin) ADMIN=0 ;;
            --token-file) TOKEN_FILE=$2; shift ;;
            --no-token) NO_TOKEN=1 ;;
            --releases) RELEASES=$2; shift ;;
            --wait-days) WAIT_DAYS=$2; shift ;;
            --stage1-limit) STAGE1_LIMIT=$2; shift ;;
            --bluealsa-args) BLUEALSA_ARGS=$2; shift ;;
            --disable) DISABLE=$2; shift ;;
            --no-slots) SLOTS=0 ;;
            --no-start) START=0 ;;
            --yes) YES=1 ;;
            --no-reboot) NO_REBOOT=1 ;;
            --root) ROOT=$(realpath "$2"); shift ;;
            -h | --help) sed -n '2,34s/^# \{0,1\}//p' "$0" 2> /dev/null || say "see docs/developer/deployment.md"; exit 0 ;;
            *) die "unknown option $1 (--help)" ;;
        esac
        shift
    done
}

questions() {  # all questions first: the rest runs without a person
    [ $SLOTS = 1 ] || return 0
    if [ $ADMIN = 1 ] && ! has_users; then
        if [ -n "$ADMIN_FILE" ]; then
            ADMIN_PASSWORD=$(read_secret "$ADMIN_FILE" 8 "the password of admin") || exit 1
        else
            say "The web page of the box gets the user admin."
            ADMIN_PASSWORD=$(ask_secret "password of admin (at least 8 characters)" 8) || exit 1
        fi
    fi
    if [ -n "$LIBRARY" ] && [ ! -e "$ROOT$BASE_PATH/identity/key" ]; then
        if [ -n "$BOOTSTRAP_FILE" ]; then
            BOOTSTRAP=$(read_secret "$BOOTSTRAP_FILE" 16 "the bootstrap password") || exit 1
        else
            say "The box registers with the library $LIBRARY by its bootstrap password (tab \"Boxes\" of the library)."
            BOOTSTRAP=$(ask_secret "bootstrap password of the library" 16) || exit 1
        fi
    fi
}

main() {
    options "$@"
    PAYLOAD=$(mktemp -d)
    trap 'rm -rf "$PAYLOAD"' EXIT
    declare -F payload > /dev/null || die "this installer was not built: scripts/build_installer.sh"
    local name
    payload | base64 -d | tar -xzf - -C "$PAYLOAD" || die "the installer is damaged - download it again"
    for name in tinyjukebox-slot tinyjukebox-start tinyjukebox-functions tinyjukebox.service release.pub asound.conf \
        tinyjukebox-setup tinyjukebox-setup.service; do
        [ -s "$PAYLOAD/$name" ] || die "the installer is damaged ($name is missing) - download it again"
    done
    if [ -z "$ROOT" ]; then
        exec > >(tee -a "$LOG_PATH") 2>&1
        printf '\n=== %s: Tiny Jukebox installer %s\n' "$(date -Is)" "$VERSION"
    fi
    check
    questions
    packages
    groups
    root_rights
    robustness
    firmware
    sound
    bluetooth
    folders
    if [ $SLOTS = 1 ]; then
        slots
        source_of_versions
        first_version
        fallback
        users
    fi
    if [ ${#CHANGES[@]} = 0 ]; then step "Nothing changed"; else step "${#CHANGES[@]} changes"; fi
    start
}

# @PAYLOAD@
[ "${TINYJUKEBOX_INSTALL_SOURCED:-}" = 1 ] || main "$@"
