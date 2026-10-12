#!/bin/bash
# prints the text of a release: the section of its version in CHANGELOG.md
set -eu
cd "$(dirname "$0")/../.."
awk -v version="$1" '
    /^## / { on = ($2 == version) ; next }
    on' CHANGELOG.md | sed -e '/./,$!d'
