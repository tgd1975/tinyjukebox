#!/bin/bash
# builds the image of Tiny Jukebox (deploy/docker/) from what the build left in dist/: the wheel of this version, the
# slot for the boxes with the installer, and the versions of the dependencies from uv.lock - the files of PyPI, not
# the source tree (docs/developer/library.md, section 3.9)
#   scripts/build_image.sh                   both platforms, kept in the cache of buildx only (the build of every push)
#   scripts/build_image.sh --load            for this computer only, into the images of docker (to try it)
#   scripts/build_image.sh --push            both platforms, to the registry, with the tags <version> and latest
#   TINYJUKEBOX_IMAGE   another name than ghcr.io/tgd1975/tinyjukebox
set -eu
cd "$(dirname "$0")/.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
IMAGE=${TINYJUKEBOX_IMAGE:-ghcr.io/tgd1975/tinyjukebox}
PLATFORMS=linux/amd64,linux/arm64
OUTPUT=()
case ${1:-} in
    --load) PLATFORMS=linux/$(docker version --format '{{.Server.Arch}}'); OUTPUT=(--load) ;;
    --push) OUTPUT=(--push) ;;
    "") ;;
    *) echo "usage: $0 [--load | --push]" >&2; exit 1 ;;
esac
WHEEL=dist/tinyjukebox-$VERSION-py3-none-any.whl
[ -f "$WHEEL" ] || { echo "$WHEEL is missing: uv build --out-dir dist" >&2; exit 1; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp deploy/docker/Dockerfile deploy/docker/entrypoint.sh "$WHEEL" "$STAGE/"
uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt --output-file "$STAGE/requirements.txt" > /dev/null
mkdir "$STAGE/slots"
for file in dist/tinyjukebox-box-"$VERSION"-*; do
    [ -e "$file" ] && cp "$file" "$STAGE/slots/"
done
if grep -qs "^VERSION=\"$VERSION\"$" dist/install.sh; then
    cp dist/install.sh "$STAGE/slots/tinyjukebox-install-$VERSION.sh"
fi
[ -n "$(ls -A "$STAGE/slots")" ] || echo "note: no slot of version $VERSION in dist - the image brings none" >&2
docker buildx build --platform "$PLATFORMS" --tag "$IMAGE:$VERSION" --tag "$IMAGE:latest" "${OUTPUT[@]}" "$STAGE"
