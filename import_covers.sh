#!/bin/bash
# sets the titles and the covers of the albums and the covers of the radio stations of the box from a folder
# (docs/developer/operations.md, section 5)
#   ./import_covers.sh FOLDER
set -eu
uv run tinyjukebox-import-covers https://tinyjukebox.local "${1:?usage: import_covers.sh FOLDER}" --user admin
