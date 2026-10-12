#!/bin/bash
# builds the slot archive for the boxes (aarch64, Debian trixie) on any computer with Docker: build_slot.sh runs in a
# container of that kind, emulated if the computer is not aarch64 itself (needs binfmt/QEMU, which Docker Desktop
# brings along and the workflow sets up)
#   scripts/build_slot_aarch64.sh [--out DIR] [--key FILE]
# The container gets the tools, the library bcm2835 and the sources of RPi-MFRC522 in the versions of the playbook
# (deploy/ansible/group_vars/all/main.yaml), so the slot holds the same rfid-reader as a box set up by the playbook.
#
# What takes long is kept in SLOT_CACHE (default ~/.cache/tinyjukebox/slot) and reused while it is unchanged
# (build_slot.sh --cache): the compiled libraries and rfid-reader. Emulated, a build from nothing takes about eight
# minutes on a PC; with the cache filled the container installs no compiler at all. The workflow keeps the folder
# between its runs.
set -eu
cd "$(dirname "$0")/.."
OUT=dist
KEY=
while [ $# -gt 0 ]; do
    case $1 in
        --out) OUT=$2; shift ;;
        --key) KEY=$2; shift ;;
        *) echo "unknown option $1" >&2; exit 1 ;;
    esac
    shift
done
value() { sed -n "s/^$1: *\"\{0,1\}\([^\"]*\)\"\{0,1\}\$/\1/p" deploy/ansible/group_vars/all/main.yaml; }
BCM2835=$(value rfid_bcm2835_version)
BCM2835_SHA256=$(value rfid_bcm2835_sha256)
MFRC522_REPO=$(value rfid_mfrc522_repo)
MFRC522_VERSION=$(value rfid_mfrc522_version)
RASPBERRYPI=https://archive.raspberrypi.com/debian
KEYRING=raspberrypi-archive-keyring_2025.1+rpt1_all.deb
KEYRING_SHA256=2e727149d7acb8cc7f604e66d0049161039c8aa1eaf1175e54f9e69d963d60e4
CACHE=${SLOT_CACHE:-$HOME/.cache/tinyjukebox/slot}
mkdir -p "$OUT" "$CACHE"
OUT=$(realpath "$OUT")
MOUNT_KEY=()
[ -z "$KEY" ] || MOUNT_KEY=(-v "$(realpath "$KEY"):/key:ro")

# the repository is mounted read-only and what the build needs is copied inside: the build leaves nothing behind in
# it. Everything the build writes lies in memory, except the packages of the operating system
# UV_LEGACY_TAR_BACKEND=1: uv unpacks archives as it did before 0.13.0. Its new way uses the system call openat2, which
# an emulator older than QEMU 10.0.8 gets wrong for an aarch64 program (issue 3262 of QEMU) - Docker Desktop brings
# such an emulator, and uv then cannot even unpack its own Python: "failed to inspect ... Invalid argument (os error
# 22)". When the switch can go: docs/developer/roadmap.md, section 3
docker run --rm --platform linux/arm64 -v "$PWD:/source:ro" -v "$OUT:/out" -v "$CACHE:/cache" "${MOUNT_KEY[@]}" \
    --tmpfs /tmp:exec,size=3g --tmpfs /root:exec,size=2g --tmpfs /build:exec,size=1g \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" -e COMMIT="$(git rev-parse HEAD 2> /dev/null || true)" \
    -e DEBIAN_FRONTEND=noninteractive -e READER_LIBRARIES="RPi-MFRC522 $MFRC522_VERSION bcm2835 $BCM2835" \
    -e UV_LEGACY_TAR_BACKEND=1 \
    debian:trixie bash -euc "
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends ca-certificates curl jq openssl > /dev/null
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh > /dev/null 2>&1
    tar -C /source --exclude=./.git --exclude=./.venv --exclude=./dist --exclude=./cards --exclude=./hardware \
        --exclude=./docs --exclude=./tests -cf - . | tar -C /build -xf -
    cd /build
    MISSING=\$(scripts/build_slot.sh --cache /cache --missing)
    echo \"to build: \${MISSING:-nothing, everything is in the cache}\" >&2
    case \$MISSING in *deps*)
        # pyalsaaudio and lgpio are built from their source. liblgpio-dev comes from the archive of Raspberry Pi, as
        # on a box: Debian itself does not have it. The key of the archive is its key package, pinned by checksum
        apt-get install -y -qq --no-install-recommends gcc libc6-dev libasound2-dev swig > /dev/null
        curl -fsSL -o /tmp/keyring.deb $RASPBERRYPI/pool/main/r/raspberrypi-archive-keyring/$KEYRING
        echo '$KEYRING_SHA256  /tmp/keyring.deb' | sha256sum -c --quiet -
        dpkg -i /tmp/keyring.deb > /dev/null
        echo 'deb [signed-by=/usr/share/keyrings/raspberrypi-archive-keyring.pgp] $RASPBERRYPI trixie main' \
            > /etc/apt/sources.list.d/raspberrypi.list
        apt-get update -qq
        apt-get install -y -qq --no-install-recommends liblgpio-dev > /dev/null
        ;;
    esac
    case \$MISSING in *rfid-reader*)
        apt-get install -y -qq --no-install-recommends git make g++ libc6-dev > /dev/null
        curl -fsSL -o /tmp/bcm2835.tar.gz http://www.airspayce.com/mikem/bcm2835/bcm2835-$BCM2835.tar.gz
        echo '$BCM2835_SHA256  /tmp/bcm2835.tar.gz' | sha256sum -c --quiet -
        tar -xzf /tmp/bcm2835.tar.gz -C /tmp
        (cd /tmp/bcm2835-$BCM2835 && ./configure > /dev/null && make -s > /dev/null && make -s install > /dev/null)
        git clone -q --depth 1 --branch $MFRC522_VERSION $MFRC522_REPO /tmp/RPi-MFRC522 2> /dev/null
        ;;
    esac
    scripts/build_slot.sh --cache /cache --out /out --mfrc522 /tmp/RPi-MFRC522 $([ -z "$KEY" ] || echo '--key /key')
    # (Docker Desktop hands the files to the user by itself and does not allow this)
    chown -R \$HOST_UID:\$HOST_GID /out /cache 2> /dev/null || true
"
