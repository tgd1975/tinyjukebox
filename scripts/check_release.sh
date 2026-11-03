#!/bin/bash
# asks the library to look for a new release now (docs/developer/library.md, section 8); a version that is
# found is fetched, and the library looks more often for a while
#   scripts/check_release.sh [--pin sha256//... | --cacert FILE] https://nas:8866 [TOKEN FILE]
# the token for scripts is created by an admin on the page of the library (tab "Boxen") and kept in a file outside
# git (default: ~/.config/tinyjukebox/library-token). It allows nothing but this request
# The library has a certificate of its own: the token goes only to the public key that was accepted the first time
# (compare it with the pin of the line in the tab "Boxes"), or to the one --pin names (scripts/trust.sh)
set -eu
# shellcheck source=scripts/trust.sh
. "$(dirname "$0")/trust.sh"
PIN=
while [ $# -gt 0 ]; do
    case $1 in
        --pin) PIN=$2; shift 2 ;;
        --cacert) TINYJUKEBOX_CACERT=$2; shift 2 ;;
        *) break ;;
    esac
done
URL=${1:?usage: check_release.sh [--pin sha256//... | --cacert FILE] URL [TOKEN FILE]}
URL=${URL%/}
TOKEN_FILE=${2:-$HOME/.config/tinyjukebox/library-token}
[ -r "$TOKEN_FILE" ] || { echo "$TOKEN_FILE cannot be read" >&2; exit 1; }
trust "$URL" "$PIN" || exit 1
# the token goes to curl through a pipe: an argument could be read in the list of processes
STATUS=0
curl -fsS "${TLS[@]}" -X POST -H @<(printf 'Authorization: Bearer %s\n' "$(cat "$TOKEN_FILE")") \
    -H 'Content-Type: application/json' -d '{}' "$URL/api/library/releases/check" || STATUS=$?
refused_key $STATUS "$URL"
[ $STATUS = 0 ] || exit $STATUS
echo
