#!/bin/bash
# publishes the image of this version to the registry of GitHub, after the release (docs/developer/library.md,
# section 8.2). An image of this version that is there already is left as it is: the job can be run again.
#   GH_TOKEN   the token of the job (packages: write)    GITHUB_ACTOR   set by the runner
set -eu
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
IMAGE=${TINYJUKEBOX_IMAGE:-ghcr.io/tgd1975/tinyjukebox}
printf '%s\n' "$GH_TOKEN" | docker login ghcr.io --username "$GITHUB_ACTOR" --password-stdin
if docker buildx imagetools inspect "$IMAGE:$VERSION" > /dev/null 2>&1; then
    echo "image $IMAGE:$VERSION exists already"
    exit 0
fi
scripts/build_image.sh --push
echo "image $IMAGE:$VERSION published"
