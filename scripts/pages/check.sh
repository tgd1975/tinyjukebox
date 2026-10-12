#!/bin/bash
# looks at the web pages of library and box in a real browser: starts a library and a box without hardware on
# scratch data, drives them with headless Chromium and reports what does not work (scripts/pages/check.py)
#   scripts/pages/check.sh [FOLDER FOR SCREENSHOTS]
# Needs: uv, chromium (or CHROMIUM=/path/to/chrome), openssl. Everything it writes lies in a temporary folder; nothing
# of ~/tinyjukebox-sim or ~/tinyjukebox-library is touched. Ports: 18866 (library), 18443 (box), 19222 (browser).
set -eu
cd "$(dirname "$0")/../.."
SHOTS=${1:-}
CHROMIUM=${CHROMIUM:-$(command -v chromium || command -v chromium-browser || command -v google-chrome)}
WORK=$(mktemp -d)
# (the Chromium of snap may only write in its own folder)
SNAP_FOLDER=$HOME/snap/chromium/common
PROFILE=$(mktemp -d ${SNAP_FOLDER:+$([ -d "$SNAP_FOLDER" ] && echo "-p $SNAP_FOLDER")} tinyjukebox-pages-XXXXXX)
PIDS=()
trap 'kill "${PIDS[@]}" 2> /dev/null; sleep 1; rm -rf "$WORK" "$PROFILE"' EXIT

mkdir -p "$WORK/lib/ssl" "$WORK/box/ssl"
PYTHONPATH=. uv run python -c "
import logging, sys
from pathlib import Path
from tests.support import ONE_SECOND_MP3, create_library
create_library(Path(sys.argv[1]), logging.getLogger('pages'), track=ONE_SECOND_MP3)" "$WORK/box" > /dev/null 2>&1
sed "s|~/tinyjukebox-library|$WORK/lib|g; s|port: 8866|port: 18866|" deploy/config/library-sim.yaml \
    > "$WORK/library.yaml"
cat > "$WORK/box.yaml" << END
logger: {log-level: INFO, log-file: $WORK/box/tinyjukebox.log}
tinyjukebox:
    albums-folder: $WORK/box/albums
    playlist-filename: playlist.m3u
    volume: {max-volume: 100, default-volume: 40, volume-step: 5}
    albumplayer: {rewind-max-time: 10, update-schedule: 1, pause-between-tracks: 0}
    json: {tags: $WORK/box/json/tags.json, targets: $WORK/box/json/targets.json, activities: $WORK/box/json/activity.json}
    sync: {retry-seconds: 2}
    rest-api: {port: 18443, certificate: $WORK/box/ssl/box.crt, private-key: $WORK/box/ssl/box.key, users-file: $WORK/box/ssl/users.yaml}
END
for side in lib box; do
    printf 'admin-password\n' | uv run tinyjukebox-users -f "$WORK/$side/ssl/users.yaml" --password-stdin add admin \
        --role admin > /dev/null
done

start_library() { uv run tinyjukebox-library -c "$WORK/library.yaml" > "$WORK/library.out" 2>&1 & PIDS+=($!); }
start_box() { uv run tinyjukebox -c "$WORK/box.yaml" --no-hardware --no-audio --no_gui > "$WORK/box.out" 2>&1 & BOX=$!; PIDS+=($BOX); }
up() { for _ in $(seq 1 40); do curl -sk --max-time 2 -o /dev/null "$1" && return 0; sleep 0.5; done; echo "$1 does not answer" >&2; exit 1; }
start_library
start_box
# both pages are tabs of one window: the one behind must be drawn and keep its timers like the one in front
"$CHROMIUM" --headless=new --remote-debugging-port=19222 --ignore-certificate-errors --no-first-run \
    --disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-background-timer-throttling \
    --window-size=1280,1000 --user-data-dir="$PROFILE" about:blank > "$WORK/chromium.out" 2>&1 &
PIDS+=($!)
up https://localhost:18866/; up https://localhost:18443/; up http://localhost:19222/json/version

CHECK=(uv run --with playwright python scripts/pages/check.py)
"${CHECK[@]}" connect "$SHOTS"
# the box works with the library from its next start
pkill -TERM -P $BOX 2> /dev/null || true
kill -TERM $BOX 2> /dev/null || true
for _ in $(seq 1 20); do curl -sk --max-time 1 -o /dev/null https://localhost:18443/ || break; sleep 0.5; done
start_box
up https://localhost:18443/
sleep 3
"${CHECK[@]}" connected "$SHOTS"
