#!/bin/bash
# builds the slot archive of the box software (docs/developer/library.md, section 6.14): one file that runs
# from either slot of a box
#   scripts/build_slot.sh [--out DIR] [--key FILE] [--mfrc522 DIR] [--no-rfid-reader] [--cache DIR]
#   scripts/build_slot.sh ... --unpacked            only the folder of the slot, not signed and not packed:
#                                                   DIR/tinyjukebox-box-<version>-<platform>/ (build_slot_aarch64.sh)
#   scripts/build_slot.sh --pack FOLDER [--out DIR] [--key FILE]     signs, packs and describes such a folder
#   scripts/build_slot.sh --cache DIR --missing     prints what a build would have to compile: deps, rfid-reader
# writes into DIR (default dist/):
#   tinyjukebox-box-<version>-<platform>.tar.gz       the slot: own Python, tinyjukebox, rfid-reader with the licence
#                                                     texts of its libraries, config, checksums and - with a key - the
#                                                     signature of the checksums, SHA256SUMS.sig
#   tinyjukebox-box-<version>-<platform>.tar.gz.sig   its signature, if a private key is given (--key)
#   tinyjukebox-box-<version>-<platform>.json         its description, read before anything is downloaded
#
# The script builds for the computer it runs on. For the boxes that is aarch64 with Debian trixie: a runner or a
# container of that kind (scripts/build_slot_aarch64.sh), or a Pi. On the PC it builds a slot for trying.
# Needs: uv, jq, curl; on aarch64 a C compiler, libasound2-dev, swig and liblgpio-dev (pyalsaaudio and lgpio are built
# from their source there); for rfid-reader a C++ compiler, the library bcm2835 and the sources of RPi-MFRC522
# (--mfrc522 DIR, default cpp/RPi-MFRC522). A box needs the libraries libasound2 and liblgpio1, which the playbook
# installs.
#
# --cache DIR keeps what takes long to build and reuses it as long as it is unchanged: the self-contained Python, the
# installed dependencies (as long as uv.lock names the same ones) and rfid-reader (as long as its source and its
# libraries are the same; READER_LIBRARIES names their versions). With everything in the cache a build needs no
# compiler at all.
set -eu
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
OUT=dist
KEY=
MFRC522=cpp/RPi-MFRC522
READER=1
CACHE=
MISSING=0
UNPACKED=0
PACK=
while [ $# -gt 0 ]; do
    case $1 in
        --out) OUT=$2; shift ;;
        --key) KEY=$2; shift ;;
        --mfrc522) MFRC522=$2; shift ;;
        --no-rfid-reader) READER=0 ;;
        --cache) CACHE=$2; shift ;;
        --missing) MISSING=1 ;;
        --unpacked) UNPACKED=1 ;;
        --pack) PACK=$(realpath "$2"); shift ;;
        *) echo "unknown option $1" >&2; exit 1 ;;
    esac
    shift
done

pack() {  # folder: signs its list of checksums, packs it and describes the archive; prints the archive
    local folder=$1 file version platform base
    for file in VERSION description.json SHA256SUMS; do
        [ -f "$folder/$file" ] && [ ! -L "$folder/$file" ] || { echo "$folder is no slot: $file is missing" >&2; exit 1; }
    done
    version=$(cat "$folder/VERSION")
    platform=$(jq -r .platform "$folder/description.json")
    [ "$(jq -r .version "$folder/description.json")" = "$version" ] || { echo "$folder: VERSION and description.json differ" >&2; exit 1; }
    # what is signed is what the list names, and the list names every file: a build that went wrong stops here
    (cd "$folder" && sha256sum --quiet --strict -c SHA256SUMS) || { echo "$folder does not fit its SHA256SUMS" >&2; exit 1; }
    if [ -n "$( (cd "$folder" && find . -type f ! -name SHA256SUMS ! -name SHA256SUMS.sig | sort \
        | comm -23 - <(sed 's/^[0-9a-f]\{64\} [ *]//' SHA256SUMS | sort)) )" ]; then
        echo "$folder has files that SHA256SUMS does not name" >&2
        exit 1
    fi
    rm -f "$folder/SHA256SUMS.sig"
    # the signature of the list, which the list does not name: the box trusts rfid-reader of the slot by it and starts
    # it as root (docs/developer/box.md, section 7). An EC key (P-256): "openssl dgst -sha256 -sign" refuses Ed25519
    [ -z "$KEY" ] || openssl dgst -sha256 -sign "$KEY" -out "$folder/SHA256SUMS.sig" "$folder/SHA256SUMS"
    mkdir -p "$OUT"
    base=$OUT/tinyjukebox-box-$version-$platform
    tar --sort=name --owner=0 --group=0 --numeric-owner -C "$folder" -cf - . | gzip -6 > "$base.tar.gz"
    jq --argjson size "$(stat -c %s "$base.tar.gz")" --arg sha256 "$(sha256sum "$base.tar.gz" | cut -d ' ' -f 1)" \
        '. + {size: $size, sha256: $sha256}' "$folder/description.json" > "$base.json"
    rm -f "$base.tar.gz.sig"
    [ -z "$KEY" ] || openssl dgst -sha256 -sign "$KEY" -out "$base.tar.gz.sig" "$base.tar.gz"
    echo "$base.tar.gz"
}
if [ -n "$PACK" ]; then
    pack "$PACK"
    exit 0
fi

ARCH=$(uname -m)
# as tinyjukebox-functions names the platform of a box
PLATFORM=$ARCH-$(. /etc/os-release && echo "$ID-$VERSION_CODENAME")

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
# what the cache holds is named after what it was built from
uv export --quiet --locked --no-dev --extra box --no-emit-project --no-header --format requirements-txt \
    --output-file "$STAGE/requirements.txt"
DEPS=
CACHED_READER=
if [ -n "$CACHE" ]; then
    mkdir -p "$CACHE"
    export PYTHON_STANDALONE_DIR=$CACHE/python
    DEPS=$CACHE/deps-$(cat "$STAGE/requirements.txt" scripts/fetch_python.sh | sha256sum | cut -c 1-16)-$PLATFORM.tar
    CACHED_READER=$CACHE/rfid-reader-$( (cat cpp/rfid-reader/src/main.cpp cpp/rfid-reader/Makefile; echo "${READER_LIBRARIES:-}") | sha256sum | cut -c 1-16)-$PLATFORM
fi
if [ $MISSING = 1 ]; then
    [ -n "$DEPS" ] && [ -f "$DEPS" ] || echo deps
    [ $READER = 0 ] || { [ -n "$CACHED_READER" ] && [ -f "$CACHED_READER" ]; } || echo rfid-reader
    exit 0
fi
[ "$PLATFORM" = aarch64-debian-trixie ] || echo "note: a slot for $PLATFORM - the boxes are aarch64-debian-trixie" >&2

# the self-contained Python, reduced to what a box needs; prepared once and reused
PREPARED=$(scripts/fetch_python.sh "$ARCH")

SLOT=$STAGE/slot
mkdir -p "$SLOT/bin" "$SLOT/config"
cp -a "$PREPARED" "$SLOT/python"
PACKAGES=$(echo "$SLOT"/python/lib/python3.*/site-packages)
PYTHON_VERSION=$(basename "$(dirname "$PACKAGES")")  # python3.13

# tinyjukebox and exactly the dependencies of uv.lock, with the hardware libraries of a box (extra 'box'). The Python of
# the slot has no header files any more, so packages that are built from their source are built with a complete
# Python of the same version, which uv provides
uv build --quiet --wheel --out-dir "$STAGE/wheel"
if [ -n "$DEPS" ] && [ -f "$DEPS" ]; then
    tar -xf "$DEPS" -C "$PACKAGES" --no-same-owner  # the same dependencies as last time: nothing to fetch, nothing to compile
else
    uv pip install --quiet --python "${PYTHON_VERSION#python}" --target "$PACKAGES" -r "$STAGE/requirements.txt"
    # (console scripts, and what the source packages of tantamount and asyncscheduler install next to their code)
    rm -rf "$PACKAGES/bin" "$PACKAGES/share" "$PACKAGES/example" "$PACKAGES/tests_unit" "$PACKAGES/README.rst"
    find "$PACKAGES" -name __pycache__ -prune -exec rm -rf {} +
    if [ -n "$DEPS" ]; then
        rm -f "$CACHE"/deps-*-"$PLATFORM".tar  # the ones of an older uv.lock
        tar -cf "$DEPS.new" -C "$PACKAGES" . && mv "$DEPS.new" "$DEPS"
    fi
fi
uv pip install --quiet --python "${PYTHON_VERSION#python}" --target "$PACKAGES" --no-deps \
    "$STAGE"/wheel/tinyjukebox-*.whl
rm -rf "$PACKAGES/bin"
find "$SLOT/python" -name __pycache__ -prune -exec rm -rf {} +

echo "$VERSION" > "$SLOT/VERSION"
cp deploy/config/tinyjukebox.yaml "$SLOT/config/tinyjukebox.yaml"  # the config of this version
cat > "$SLOT/bin/tinyjukebox" << 'END'
#!/bin/sh
# starts the box with paths relative to the slot. The config of a box lies outside the slots; without one the
# config of this version applies
slot=$(dirname "$(dirname "$(readlink -f "$0")")")
config=${TINYJUKEBOX_BASE:-/srv/tinyjukebox}/config/tinyjukebox.yaml
[ -f "$config" ] || config=$slot/config/tinyjukebox.yaml
exec "$slot/python/bin/python3" -B -m tinyjukebox.tinyjukebox -c "$config" --no_gui "$@"
END
tool() {  # name module: a command of tinyjukebox that runs with the Python of the slot
    printf '#!/bin/sh\nslot=$(dirname "$(dirname "$(readlink -f "$0")")")\nexec "$slot/python/bin/python3" -B -c "import sys; from %s import main; sys.exit(main())" "$@"\n' "$2" > "$SLOT/bin/$1"
}
tool tinyjukebox-users tinyjukebox.web.users
tool tinyjukebox-import-cards tinyjukebox.tools.import_cards
tool tinyjukebox-migrate-01 tinyjukebox.tools.migrate_box01
tool tinyjukebox-fill-library tinyjukebox.tools.fill_library

# rfid-reader, the program that reads the tags (docs/developer/rfid-reader.md)
if [ $READER = 1 ] && [ -n "$CACHED_READER" ] && [ -f "$CACHED_READER" ]; then
    cp "$CACHED_READER" "$SLOT/bin/rfid-reader"  # its source and its libraries are the ones of last time
elif [ $READER = 1 ] && [ -f "$MFRC522/src/MFRC522.h" ]; then
    make -s -C cpp/rfid-reader clean
    make -s -C cpp/rfid-reader MFRC522_DIR="$(realpath "$MFRC522")"
    cp cpp/rfid-reader/rfid-reader "$SLOT/bin/rfid-reader"
    make -s -C cpp/rfid-reader clean
    if [ -n "$CACHED_READER" ]; then
        rm -f "$CACHE"/rfid-reader-*-"$PLATFORM"
        cp "$SLOT/bin/rfid-reader" "$CACHED_READER"
    fi
elif [ "$ARCH" = aarch64 ] && [ $READER = 1 ]; then
    echo "the sources of RPi-MFRC522 are missing ($MFRC522): no rfid-reader. Give --mfrc522 DIR, or --no-rfid-reader" >&2
    exit 1
else
    printf '#!/bin/sh\necho "this slot was built without rfid-reader" >&2\nexit 1\n' > "$SLOT/bin/rfid-reader"
    READER=0
fi
chmod 755 "$SLOT"/bin/*
# rfid-reader contains the libraries bcm2835 (GPL-3.0) and RPi-MFRC522 (Unlicense): their licence texts go with it
[ $READER = 0 ] || cp -r cpp/rfid-reader/licenses "$SLOT/bin/rfid-reader-licenses"

# the slot must run as it is: this also names the interface it speaks
INTERFACE=$("$SLOT/python/bin/python3" -B -c 'from tinyjukebox.sync import INTERFACE; print(INTERFACE)')
case $("$SLOT/bin/tinyjukebox" --version) in
    *" $VERSION") ;;
    *) echo "the slot does not report version $VERSION" >&2; exit 1 ;;
esac
COMMIT=${COMMIT:-$(git rev-parse HEAD 2> /dev/null || true)}  # given from outside where the build has no repository
jq -n --arg version "$VERSION" --arg commit "$COMMIT" --arg built "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    --arg platform "$PLATFORM" --argjson interface "$INTERFACE" \
    '{version: $version, commit: $commit, built: $built, platform: $platform, interface: $interface,
      system: {setup: 1, "slot-script": 1}}' > "$SLOT/description.json"
# checksum of every file: verified after unpacking, and the basis for "modified"
(cd "$SLOT" && find . -type f ! -name SHA256SUMS ! -name SHA256SUMS.sig -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)

if [ $UNPACKED = 1 ]; then
    mkdir -p "$OUT"
    rm -rf "${OUT:?}/tinyjukebox-box-$VERSION-$PLATFORM"
    mv "$SLOT" "$OUT/tinyjukebox-box-$VERSION-$PLATFORM"
    echo "$OUT/tinyjukebox-box-$VERSION-$PLATFORM"
else
    pack "$SLOT"
fi
