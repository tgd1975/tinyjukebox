#!/bin/bash
# creates tag and release on GitHub from what the build left in dist/
# the tag is created only here, from the version in pyproject.toml: tag and version cannot differ, and nobody tags by
# hand. The files go up in the same call, so the library never sees a release with half its files
#   GITHUB_SHA   set by the runner;  GH_TOKEN   the token of the job
set -eu
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
gh release create "v$VERSION" dist/* --target "$GITHUB_SHA" --title "v$VERSION" \
    --notes "$(scripts/ci/release_notes.sh "$VERSION")"
echo "release v$VERSION published"
