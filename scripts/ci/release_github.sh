#!/bin/bash
# creates tag and release on GitHub from what the build left in dist/
# the tag is created only here, from the version in pyproject.toml: tag and version cannot differ, and nobody tags by
# hand. The files go up in the same call, so the library never sees a release with half its files
#   GITHUB_SHA   set by the runner;  GH_TOKEN   the token of the job
set -eu
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
# a job that is run again after a later step failed: the release is there already
if gh release view "v$VERSION" > /dev/null 2>&1; then
    echo "release v$VERSION exists already"
    exit 0
fi
gh release create "v$VERSION" dist/* --target "$GITHUB_SHA" --title "v$VERSION" \
    --notes "$(scripts/ci/release_notes.sh "$VERSION")"
echo "release v$VERSION published"
