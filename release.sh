#!/bin/bash
# brings the committed work of this branch to master and into use: checks it as the workflow on GitHub does, pushes
# the branch, moves master forward to it and pushes master, waits for the workflow, and installs the package of the
# new release on the NAS
#   ./release.sh nas       # NAS: its name or address, as for update_library.sh
#   ./release.sh           # without the NAS: the library stays as it is
# A push to master that changes the software is a release - it needs a higher version in pyproject.toml and its
# section in CHANGELOG.md, and the boxes install it by themselves. A push that changes none is no release, and
# nothing is installed (docs/developer/library.md, section 8.1). With a NAS and a release it takes the user of
# DSM and the password from the vault of the playbook (dsm_vault.sh) or asks for them at the start - and, the first
# time, whether the certificate of DSM is the right one (scripts/trust.sh) - and uses them at the end; the password
# is given to update_library.sh only, and nothing of it is stored
set -eu
cd "$(dirname "$0")"
# shellcheck source=scripts/trust.sh
. scripts/trust.sh
# shellcheck source=dsm_vault.sh
. ./dsm_vault.sh

for tool in git gh uv; do
    command -v "$tool" > /dev/null || { echo "$tool is missing" >&2; exit 1; }
done
NAS=${1:-}
BRANCH=$(git rev-parse --abbrev-ref HEAD)
COMMIT=$(git rev-parse HEAD)
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
[ -z "$(git status --porcelain)" ] || echo "warning: uncommitted changes are not part of it" >&2
echo "$BRANCH: $(git log -1 --format='%h %s')"

# stops here if the software changed without a new version and its section; needs the tags of the releases
git fetch --quiet --tags origin
RELEASE=$(scripts/ci/release_needed.sh)
if [ -n "$NAS" ] && [ "$RELEASE" = release=true ]; then
    case $NAS in *://*) DSM=${NAS%/} ;; *) DSM=https://$NAS:5001 ;; esac  # as update_library.sh names it
    trust "$DSM" || exit 1
    dsm_from_vault
    [ -n "${DSM_USER:-}" ] || read -r -p "user of DSM (administrator): " DSM_USER
    if [ -z "${DSM_PASSWORD:-}" ]; then
        IFS= read -r -s -p "password of $DSM_USER on $NAS: " DSM_PASSWORD
        echo
    fi
fi

# what the workflow checks (.github/workflows/ci.yml) - a failure there would be found after the push only
uv lock --check
uv run ruff format --check .
uv run ruff check .
uv run mypy
uv run pytest --cov -q

if [ "$BRANCH" != master ]; then
    git push origin "$BRANCH"
    git fetch . "$BRANCH":master  # forward only: fails if master has commits that the branch lacks
fi
git push origin master

echo "waiting for the workflow on GitHub ..."
RUN=
for _ in $(seq 1 30); do
    RUN=$(gh run list --workflow CI --branch master --event push --commit "$COMMIT" --json databaseId \
        --jq '.[0].databaseId // empty')
    [ -z "$RUN" ] || break
    sleep 4
done
[ -n "$RUN" ] || { echo "GitHub shows no run for $COMMIT on master" >&2; exit 1; }
if ! gh run watch "$RUN" --exit-status --interval 20 > /dev/null; then
    echo "the workflow failed: gh run view $RUN --log-failed" >&2
    exit 1
fi

if [ "$RELEASE" != release=true ]; then
    echo "master is pushed and checked. No release: nothing to install"
    exit 0
fi
gh release view "v$VERSION" --json tagName > /dev/null
echo "release v$VERSION is published: the boxes install it by themselves"
[ -n "$NAS" ] || exit 0
# the password in the environment of this one program only, not of the checks and pushes before
DSM_USER=$DSM_USER DSM_PASSWORD=$DSM_PASSWORD ./update_library.sh "$NAS" "$VERSION"
