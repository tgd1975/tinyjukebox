#!/bin/bash
# fetches the files of a release from GitHub into dist/: the slot archive of the box with its description and
# signature, and the package of the library for the NAS
#   ./fetch_release.sh             # the newest release
#   ./fetch_release.sh 0.15.0      # this version
# the files are checked before they reach dist/: against SHA256SUMS of the release, the archive also against its
# signature (deploy/release.pub). gh must be logged in (gh auth login)
set -eu
cd "$(dirname "$0")"

for tool in gh openssl sha256sum; do
    command -v "$tool" > /dev/null || { echo "$tool is missing" >&2; exit 1; }
done
TAG=${1:+v${1#v}}
TAG=${TAG:-$(gh release view --json tagName --jq .tagName)}

mkdir -p dist
WORK=$(mktemp -d dist/.fetch-XXXXXX)
trap 'rm -rf "$WORK"' EXIT
gh release download "$TAG" --dir "$WORK"
(cd "$WORK" && sha256sum --quiet -c SHA256SUMS)
for signature in "$WORK"/*.sig; do
    openssl dgst -sha256 -verify deploy/release.pub -signature "$signature" "${signature%.sig}" > /dev/null \
        || { echo "the signature of $(basename "${signature%.sig}") is wrong" >&2; exit 1; }
done
rm "$WORK/SHA256SUMS"  # names only the files of this release; dist/ holds several
mv -f "$WORK"/* dist/
echo "release $TAG in dist/:"
ls -1 dist | grep -F -- "-${TAG#v}-"
