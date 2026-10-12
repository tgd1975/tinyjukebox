#!/bin/bash
# asks the library to look for a new release now (docs/developer/library.md, section 8); a version that is
# found is fetched, and the library looks more often for a while
#   scripts/check_release.sh https://nas:8866 [TOKEN FILE]
# the token for scripts is created by an admin on the page of the library (tab "Boxen") and kept in a file outside
# git (default: ~/.config/tinyjukebox/library-token). It allows nothing but this request
set -eu
URL=${1:?usage: check_release.sh URL [TOKEN FILE]}
TOKEN=$(cat "${2:-$HOME/.config/tinyjukebox/library-token}")
# -k: the library has a self-signed certificate
curl -fsS -k -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{}' \
    "$URL/api/library/releases/check"
echo
