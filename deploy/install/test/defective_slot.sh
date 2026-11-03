#!/bin/bash
# makes a deliberately defective version out of a slot archive, for trying what a box does with it (run.sh)
#   defective_slot.sh ARCHIVE VERSION crash|hang KEY OUT   - prints the new archive; its .sig and .json lie next to it
set -eu
ARCHIVE=$1 VERSION=$2 FAULT=$3 KEY=$4 OUT=$5
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
tar -xzf "$ARCHIVE" -C "$STAGE"
echo "$VERSION" > "$STAGE/VERSION"
jq --arg version "$VERSION" '.version = $version' "$STAGE/description.json" > "$STAGE/description.new"
mv "$STAGE/description.new" "$STAGE/description.json"
case $FAULT in
    crash) printf '#!/bin/sh\necho "this version crashes on start" >&2\nexit 3\n' > "$STAGE/bin/tinyjukebox" ;;
    hang) printf '#!/bin/sh\nexec sleep infinity\n' > "$STAGE/bin/tinyjukebox" ;;
    *) echo "crash or hang" >&2; exit 1 ;;
esac
(cd "$STAGE" && find . -type f ! -name SHA256SUMS ! -name SHA256SUMS.sig -print0 | sort -z | xargs -0 sha256sum > SHA256SUMS)
# the list is signed as in a release (scripts/build_slot.sh): rfid-reader of the slot stays trusted
openssl dgst -sha256 -sign "$KEY" -out "$STAGE/SHA256SUMS.sig" "$STAGE/SHA256SUMS"
NAME=$(basename "$ARCHIVE" | sed "s/tinyjukebox-box-[^-]*-/tinyjukebox-box-$VERSION-/")
mkdir -p "$OUT"
tar --sort=name --owner=0 --group=0 --numeric-owner -C "$STAGE" -cf - . | gzip -1 > "$OUT/$NAME"
jq --argjson size "$(stat -c %s "$OUT/$NAME")" --arg sha256 "$(sha256sum "$OUT/$NAME" | cut -d ' ' -f 1)" \
    '. + {size: $size, sha256: $sha256}' "$STAGE/description.json" > "$OUT/${NAME%.tar.gz}.json"
openssl dgst -sha256 -sign "$KEY" -out "$OUT/$NAME.sig" "$OUT/$NAME"
echo "$OUT/$NAME"
