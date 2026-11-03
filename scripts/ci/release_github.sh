#!/bin/bash
# creates tag and release on GitHub from what the build left in dist/
# the tag is created only here, from the version in pyproject.toml: tag and version cannot differ, and nobody tags by
# hand. The files go up in the same call, so the library never sees a release with half its files
#   GITHUB_SHA   set by the runner;  GH_TOKEN   the token of the job
# It never ends without a published release that holds every file of dist/:
#   - published already, with all files: a job that is run again after a later step failed - nothing to do
#   - published, a file missing: the file is uploaded
#   - a draft - gh release create makes one, uploads the files and then publishes it; a job cancelled in between
#     leaves it, and gh release view finds it as well: published if it is complete and of this commit, otherwise
#     deleted and created again. A draft has no tag yet
set -eu
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
TAG=v$VERSION

files() { (cd dist && for file in *; do printf '%s %s\n' "$file" "$(stat -c %s "$file")"; done) | sort; }
uploaded() {  # the files of the release that are uploaded completely: "name size", one per line
    gh release view "$TAG" --json assets --jq '.assets[] | select(.state == "uploaded") | "\(.name) \(.size)"' | sort
}
create() {
    gh release create "$TAG" dist/* --target "$GITHUB_SHA" --title "$TAG" \
        --notes "$(scripts/ci/release_notes.sh "$VERSION")"
}

# "true" or "false", and the commit of the release; nothing if there is no release of this tag
STATE=$(gh release view "$TAG" --json isDraft,targetCommitish --jq '"\(.isDraft) \(.targetCommitish)"' 2> /dev/null || true)
if [ -z "$STATE" ]; then
    create
elif [ "${STATE%% *}" = false ]; then
    MISSING=$(comm -23 <(files) <(uploaded) | cut -d ' ' -f 1)
    if [ -z "$MISSING" ]; then
        echo "release $TAG exists already"
    else
        echo "release $TAG is published without $(echo "$MISSING" | tr '\n' ' ')- they are uploaded"
        mapfile -t NAMES <<< "$MISSING"
        (cd dist && gh release upload "$TAG" --clobber "${NAMES[@]}")
    fi
elif [ "${STATE#* }" = "$GITHUB_SHA" ] && [ "$(files)" = "$(uploaded)" ]; then
    echo "a draft of $TAG with all its files: published"
    gh release edit "$TAG" --draft=false --latest
else
    echo "a draft of $TAG that is incomplete, or of another commit: deleted and created again"
    gh release delete "$TAG" --yes
    create
fi
[ "$(gh release view "$TAG" --json isDraft --jq .isDraft)" = false ] || { echo "release $TAG is not published" >&2; exit 1; }
[ -z "$(comm -23 <(files) <(uploaded))" ] || { echo "release $TAG lacks files of dist/" >&2; exit 1; }
echo "release $TAG published"
