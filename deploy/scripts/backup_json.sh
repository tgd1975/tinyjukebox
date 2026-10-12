#!/bin/bash
# backup copy of the json files - ExecStartPre of tinyjukebox.service, i.e. before every start (also every restart)
# skipped if nothing changed since the last copy: a box that is restarted again and again must not fill the sd card
set -eu
shopt -s nullglob
JSON=${TINYJUKEBOX_JSON:-/srv/tinyjukebox/json}
files=("$JSON"/*.json "$JSON"/*.json_)
[ ${#files[@]} -gt 0 ] || exit 0
backups=("$JSON"/backup/*/)
if [ ${#backups[@]} -gt 0 ]; then
    last=${backups[-1]%/}  # the folder names are time stamps - the newest is the last one
    unchanged=1
    for file in "${files[@]}"; do
        cmp -s "$file" "$last/$(basename "$file")" || { unchanged=0; break; }
    done
    if [ $unchanged = 1 ]; then
        echo "json files unchanged since the backup in $last"
        exit 0
    fi
fi
DIR=$JSON/backup/$(date +%Y%m%d_%H%M%S)
echo "backup copy of json files to $DIR"
mkdir -p "$DIR"
cp "${files[@]}" "$DIR"
