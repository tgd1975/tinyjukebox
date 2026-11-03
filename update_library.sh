#!/bin/bash
# installs the package of the library of a release on the NAS, with the requests the Package Center of DSM sends for
# "Manual Install": upload, check, upgrade (docs/developer/library.md, section 3.7)
#   ./update_library.sh nas            # the newest release
#   ./update_library.sh nas 0.15.2     # this version
#   ./update_library.sh --pin sha256//... nas    or    --cacert FILE nas
# NAS is the name or address of the NAS, or the address of DSM if it is not https://NAS:5001. Asks for a user of
# DSM who is an administrator (or takes DSM_USER), for the password (or takes DSM_PASSWORD) and, with two-factor
# login, for the code; nothing of it is stored. The package is taken from dist/ and fetched with fetch_release.sh if it is not there.
# Only for a library that is installed: the first installation has a dialog and is done in the Package Center.
# The requests are not documented by Synology; they are read from the Package Center of DSM 7.4
# DSM has a certificate of its own: password and code go only to the public key that was accepted the first time
# (shown with its fingerprint and asked for, then kept in ~/.config/tinyjukebox/pins), or to the one --pin names, or
# to a certificate that the authority of --cacert signed (scripts/trust.sh)
set -eu
cd "$(dirname "$0")"
# shellcheck source=scripts/trust.sh
. scripts/trust.sh
PACKAGE=tinyjukebox-library
INSTALLATION=SYNO.Core.Package.Installation

for tool in curl jq gh openssl; do
    command -v "$tool" > /dev/null || { echo "$tool is missing" >&2; exit 1; }
done
PIN=
while [ $# -gt 0 ]; do
    case $1 in
        --pin) PIN=$2; shift 2 ;;
        --cacert) TINYJUKEBOX_CACERT=$2; shift 2 ;;
        *) break ;;
    esac
done
[ $# -ge 1 ] || { echo "usage: ./update_library.sh [--pin sha256//... | --cacert FILE] NAS [VERSION]" >&2; exit 1; }
case $1 in
    *://*) DSM=${1%/} ;;
    *) DSM=https://$1:5001 ;;
esac
HOST=${DSM#*://}
HOST=${HOST%%[:/]*}
# all questions first: the certificate of DSM, user, password. The password stays in this script: no program it
# starts gets it in its environment
trust "$DSM" "$PIN" || exit 1
ACCOUNT=${DSM_USER:-}
[ -n "$ACCOUNT" ] || read -r -p "user of DSM (administrator): " ACCOUNT
PASSWORD=${DSM_PASSWORD:-}
unset DSM_PASSWORD
if [ -z "$PASSWORD" ]; then
    read -r -s -p "password of $ACCOUNT on $DSM: " PASSWORD
    echo
fi
VERSION=${2:-$(gh release view --json tagName --jq .tagName)}
VERSION=${VERSION#v}

FILE=$(ls dist/$PACKAGE-"$VERSION"-*.spk 2> /dev/null | head -n 1)
if [ -z "$FILE" ]; then
    ./fetch_release.sh "$VERSION"
    FILE=$(ls dist/$PACKAGE-"$VERSION"-*.spk | head -n 1)
fi

# one request to the web API of DSM: api NAME VERSION METHOD [arguments of curl with the parameters]
# prints what DSM answered as data
SID=
TOKEN=
api() {
    local name=$1 version=$2 method=$3 answer
    local url="$DSM/webapi/entry.cgi?_sid=$SID" which=(-d "api=$name" -d "version=$version" -d "method=$method")
    shift 3
    # a form with a file names the request in the address, as the Package Center sends it
    if [ "${1:-}" = -F ]; then
        url+="&api=$name&version=$version&method=$method"
        which=()
    fi
    answer=$(curl -sS "${TLS[@]}" --max-time 3600 -H "X-SYNO-TOKEN: $TOKEN" "$url" "${which[@]}" "$@")
    if [ "$(jq -r '.success' <<< "$answer" 2> /dev/null)" != true ]; then
        echo "DSM refuses $name $method: $(jq -c '.error // .' <<< "$answer" 2> /dev/null || echo "$answer")" >&2
        return 1
    fi
    jq -c '.data // {}' <<< "$answer"
}

# password and code go to curl through file descriptors: an argument could be read in the list of processes
login() {
    local status=0
    curl -sS "${TLS[@]}" --max-time 60 "$DSM/webapi/entry.cgi" --data-urlencode api=SYNO.API.Auth \
        --data-urlencode version=6 --data-urlencode method=login --data-urlencode "account=$ACCOUNT" \
        --data-urlencode passwd@/dev/fd/3 --data-urlencode format=sid --data-urlencode enable_syno_token=yes \
        ${1:+--data-urlencode otp_code@/dev/fd/4} 3< <(printf %s "$PASSWORD") 4< <(printf %s "${1:-}") || status=$?
    refused_key $status "$DSM"
    return $status
}

ANSWER=$(login) || exit 1
if [ "$(jq -r '.error.code // empty' <<< "$ANSWER")" = 403 ]; then
    read -r -p "code of the two-factor login: " CODE
    ANSWER=$(login "$CODE") || exit 1
fi
unset PASSWORD
if [ "$(jq -r '.success' <<< "$ANSWER")" != true ]; then
    echo "DSM refuses the login: $(jq -c '.error // .' <<< "$ANSWER")" >&2
    exit 1
fi
SID=$(jq -r '.data.sid | @uri' <<< "$ANSWER")
TOKEN=$(jq -r '.data.synotoken // empty' <<< "$ANSWER")

# what was uploaded is removed again if it was not installed, as the Package Center does when its dialog is closed
TASK=
finish() {
    [ -z "$TASK" ] || api $INSTALLATION 1 clean --data-urlencode "task_id=\"$TASK\"" > /dev/null 2>&1 || true
    api SYNO.API.Auth 6 logout > /dev/null 2>&1 || true
}
trap finish EXIT

echo "uploading $(basename "$FILE") ..."
ADDITIONAL='["description","maintainer","distributor","startable","dsm_apps","status","install_reboot"'
ADDITIONAL+=',"install_type","install_on_cold_storage","break_pkgs","replace_pkgs"]'
UPLOAD=$(api $INSTALLATION 1 upload -F "additional=$ADDITIONAL" -F "file=@$FILE;type=application/octet-stream")
# DSM answers some values beside "additional" and some in it
value() { jq -r --arg key "$1" 'if has($key) then .[$key] else .additional[$key] end // empty' <<< "$UPLOAD"; }
TASK=$(value task_id)
if [ "$(value id)" != $PACKAGE ]; then
    echo "$FILE is the package \"$(value id)\", not $PACKAGE" >&2
    exit 1
fi
if [ "$(value status)" = non_installed ]; then
    echo "the library is not installed on this NAS: the first installation is done in the Package Center" >&2
    echo "(docs/developer/library.md, section 3.7)" >&2
    exit 1
fi
# a package that Synology did not sign: the Package Center asks, and installs without the check after a yes.
# 4501 is what it does not install at all
if [ "$(value codesign_error)" = 4501 ]; then
    echo "DSM does not accept the signature of the package (4501)" >&2
    exit 1
fi

echo "installing $PACKAGE $(value version) ..."
VOLUME=$(api SYNO.Core.Package.Setting.Volume 1 get | jq -r '.default_vol // empty')
COMPOUND=$(jq -c --arg installation $INSTALLATION --arg volume "$VOLUME" '
    def value($key): if has($key) then .[$key] else .additional[$key] end;
    def list($key): value($key) | if . == "" then null else . end;
    [{api: $installation, method: "check", version: 2, id: value("id"), install_type: value("install_type"),
      install_on_cold_storage: value("install_on_cold_storage"), breakpkgs: list("break_pkgs"), blCheckDep: true,
      replacepkgs: list("replace_pkgs")},
     ({api: $installation, method: "upgrade", version: 1, volume_path: $volume, extra_values: "{}", type: 0,
       check_codesign: (has("codesign_error") | not), force: false, installrunpackage: true}
      + if (value("task_id") // "") != "" then {task_id: value("task_id")} else {path: .filename} end),
     {api: "SYNO.Core.Package", method: "get", version: 1, id: value("id"), additional: ["status", "dsm_apps"]}]
    ' <<< "$UPLOAD")
RESULT=$(api SYNO.Entry.Request 1 request --data-urlencode stop_when_error=true \
    --data-urlencode 'mode="sequential"' --data-urlencode "compound=$COMPOUND")
if [ "$(jq -r '.has_fail' <<< "$RESULT")" != false ]; then
    echo "DSM did not install the package:" >&2
    jq -c '.result[]? | select(.success != true) | {api, method, error}' <<< "$RESULT" >&2
    exit 1
fi
TASK=

# DSM starts the package after the upgrade; the page of the library answers a little later
for _ in $(seq 1 30); do
    STATE=$(api SYNO.Core.Package 1 get --data-urlencode "id=\"$PACKAGE\"" --data-urlencode 'additional=["status"]')
    [ "$(jq -r '.additional.status // empty' <<< "$STATE")" != running ] || break
    sleep 2
done
echo "$PACKAGE $(jq -r '.version // "?"' <<< "$STATE") on $HOST: $(jq -r '.additional.status // "?"' <<< "$STATE")"
PORT=$(sed -n 's/^PORT=\([0-9]*\).*/\1/p' build_package.sh)
for _ in $(seq 1 30); do
    # whether the page answers at all - nothing is sent to it
    if curl -s -k --max-time 3 -o /dev/null "https://$HOST:$PORT/"; then
        echo "the page answers: https://$HOST:$PORT/"
        exit 0
    fi
    sleep 2
done
echo "the page https://$HOST:$PORT/ does not answer - see var/server.log on the NAS" >&2
exit 1
