#!/bin/bash
# decides whether this commit becomes a release (docs/developer/library.md, section 8.1); prints
# release=true or release=false
#   - a push that changes only documentation, tests or hardware files needs neither a higher version nor an entry in
#     the changelog, and leads to no release - otherwise every corrected typing error in a document would make all
#     boxes download and try a new version
#   - a push that changes the software needs a version in pyproject.toml that is higher than the last release, and a
#     section for exactly this version in CHANGELOG.md; otherwise this script fails and nothing is published
set -eu
cd "$(dirname "$0")/../.."
SOFTWARE=(src cpp deploy scripts app build_package.sh pyproject.toml uv.lock)  # what runs on a box, the NAS or a phone, and what builds it
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
# the last release this commit descends from
LAST=$(git tag --list 'v*' --merged HEAD | sort -V | tail -n 1)

if [ -n "$LAST" ]; then
    if git diff --quiet "$LAST" HEAD -- "${SOFTWARE[@]}"; then
        echo "the software did not change since $LAST: no release" >&2
        echo "release=false"
        exit 0
    fi
    if [ "${LAST#v}" = "$VERSION" ] || [ "$(printf '%s\n%s\n' "${LAST#v}" "$VERSION" | sort -V | tail -n 1)" != "$VERSION" ]; then
        echo "the software changed since $LAST, but the version $VERSION is not higher" >&2
        exit 1
    fi
fi
if ! grep -q -E "^## ${VERSION//./\\.}( |\$)" CHANGELOG.md; then
    echo "CHANGELOG.md has no section for version $VERSION" >&2
    exit 1
fi
echo "version $VERSION becomes a release" >&2
echo "release=true"
