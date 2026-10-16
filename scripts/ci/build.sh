#!/bin/bash
# builds what a release holds into dist/: the signed slot for the boxes, the package of the library for the NAS with
# this slot in it, the installer of a box (install.sh), wheel and source archive, and checksums (docs/developer/library.md, section 8)
#   SIGNING_KEY   the private key of the releases (PEM), which only the workflow has; without it the slot is not signed
# The slot is built for aarch64 in a container (scripts/build_slot_aarch64.sh); the package only fetches ready-made
# files and is built as it is.
set -eu
cd "$(dirname "$0")/../.."
rm -rf dist
mkdir dist
KEY=()
if [ -n "${SIGNING_KEY:-}" ]; then
    KEYFILE=$(mktemp)
    trap 'rm -f "$KEYFILE"' EXIT
    printf '%s\n' "$SIGNING_KEY" > "$KEYFILE"
    KEY=(--key "$KEYFILE")
fi
scripts/build_slot_aarch64.sh --out dist "${KEY[@]}"
scripts/build_installer.sh --out dist
./build_package.sh 1 --slots dist
uv build --quiet --out-dir dist  # wheel and source archive: PyPI and the image (scripts/build_image.sh)
(cd dist && sha256sum -- * > SHA256SUMS)
ls -l dist
