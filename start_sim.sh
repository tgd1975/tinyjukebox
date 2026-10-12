#!/bin/bash
# starts the box on the pc: no hardware (tags, buttons and led on the web page), with web page and rest api
#   ./start_sim.sh                # with sound and interactive console
#   ./start_sim.sh --no-audio     # further options are passed on to tinyjukebox (--no_gui, -v, ...)
# config: deploy/config/tinyjukebox-sim.yaml, data: ~/tinyjukebox-sim
set -eu
cd "$(dirname "$0")"
CONFIG=deploy/config/tinyjukebox-sim.yaml

# value of a key in the config, e.g. 'albums-folder'; a path may begin with ~
config_value() {
    sed -n "s/^ *$1: *\([^ #]*\).*/\1/p" "$CONFIG" | head -n 1 | sed "s|^~|$HOME|"
}

for tool in uv ffmpeg; do
    command -v "$tool" > /dev/null || { echo "$tool is missing" >&2; exit 1; }
done
TAGS=$(config_value tags)
if [ ! -f "$TAGS" ]; then
    echo "$TAGS is missing - create the data of the box on the PC first (~/tinyjukebox-sim)" >&2
    exit 1
fi
mkdir -p "$(dirname "$(config_value log-file)")"
ALBUMS=$(config_value albums-folder)
[ -d "$ALBUMS" ] || echo "warning: albums folder $ALBUMS is missing (drive not mounted?) - no album can be played" >&2

echo "web page: https://localhost:$(config_value port)/ (self signed certificate), stop with Ctrl-C or 'exit'"
exec uv run tinyjukebox -c "$CONFIG" --no-hardware --webserver "$@"
