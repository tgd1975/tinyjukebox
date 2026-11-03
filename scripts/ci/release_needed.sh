#!/bin/bash
# decides whether this commit becomes a release (docs/developer/library.md, section 8.1); prints
# release=true or release=false
#   - a push that changes only documentation, tests or hardware files needs neither a higher version nor an entry in
#     the changelog, and leads to no release - otherwise every corrected typing error in a document would make all
#     boxes download and try a new version
#   - a push that changes the software needs a version in pyproject.toml that is higher than the last release, and a
#     section for exactly this version in CHANGELOG.md; otherwise this script fails and nothing is published
#   - on a branch other than master (GITHUB_REF of the workflow) the same push is a warning and release=false: the
#     branch is built and tried all the same, and the version is raised before it goes to master. On master - and on
#     the PC, where release.sh asks - it fails
#   - versions are X.Y.Z, and only tags vX.Y.Z count: sort -V would put 0.21.0rc1 after 0.21.0
set -eu
cd "$(dirname "$0")/../.."
SOFTWARE=(src cpp deploy scripts app build_package.sh pyproject.toml uv.lock)  # what runs on a box, the NAS or a phone, and what builds it
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
BRANCH=master
case ${GITHUB_REF:-} in refs/heads/master | "") ;; *) BRANCH=${GITHUB_REF#refs/heads/} ;; esac

no_release() {  # why: on master the push fails; on another branch it is built without a release
    if [ "$BRANCH" = master ]; then
        echo "$1" >&2
        exit 1
    fi
    if [ "${GITHUB_ACTIONS:-}" = true ]; then echo "::warning::$1 - no release from $BRANCH" >&2; else echo "warning: $1 - no release from $BRANCH" >&2; fi
    echo "release=false"
    exit 0
}

if ! [[ $VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "the version '$VERSION' in pyproject.toml is not X.Y.Z" >&2
    exit 1
fi
# the last release this commit descends from
LAST=$(git tag --list 'v*' --merged HEAD | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V | tail -n 1)

if [ -n "$LAST" ]; then
    if git diff --quiet "$LAST" HEAD -- "${SOFTWARE[@]}"; then
        echo "the software did not change since $LAST: no release" >&2
        echo "release=false"
        exit 0
    fi
    if [ "${LAST#v}" = "$VERSION" ] || [ "$(printf '%s\n%s\n' "${LAST#v}" "$VERSION" | sort -V | tail -n 1)" != "$VERSION" ]; then
        no_release "the software changed since $LAST, but the version $VERSION is not higher"
    fi
fi
if ! grep -q -E "^## ${VERSION//./\\.}( |\$)" CHANGELOG.md; then
    no_release "CHANGELOG.md has no section for version $VERSION"
fi
echo "version $VERSION becomes a release$([ "$BRANCH" = master ] || echo " when it is pushed to master")" >&2
echo "release=true"
