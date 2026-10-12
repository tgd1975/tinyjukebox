#!/bin/bash
# starts the library on the pc (docs/developer/library.md) - web page: https://localhost:8866/
#   ./start_library.sh            # further options are passed on to tinyjukebox-library
# config: deploy/config/library-sim.yaml, data: ~/tinyjukebox-library
set -eu
cd "$(dirname "$0")"
CONFIG=deploy/config/library-sim.yaml
DATA=~/tinyjukebox-library

command -v uv > /dev/null || { echo "uv is missing" >&2; exit 1; }
mkdir -p "$DATA/ssl" "$DATA/log"
if [ ! -f "$DATA/ssl/users.yaml" ]; then
    echo "no users yet - create an admin of the page first:" >&2
    echo "  uv run tinyjukebox-users -f $DATA/ssl/users.yaml add admin --role admin" >&2
    exit 1
fi
echo "web page: https://localhost:8866/ (self signed certificate), stop with Ctrl-C"
exec uv run tinyjukebox-library -c "$CONFIG" -v "$@"
