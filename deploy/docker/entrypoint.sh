#!/bin/sh
# What the image of Tiny Jukebox runs (deploy/docker/Dockerfile):
#   library     the library, everything below /data (port 8866) - the default
#   box         a box without hardware and without sound, everything below /data (port 8443)
#   COMMAND     that command, e.g. tinyjukebox-users, tinyjukebox-fill-library
# The first start: TINYJUKEBOX_ADMIN_PASSWORD (otherwise generated and printed once on the error output),
# TINYJUKEBOX_BOOTSTRAP_PASSWORD - for the library also whenever it names another one -, for a box TINYJUKEBOX_LIBRARY
# (docs/developer/configuration.md, section 1). The passwords may be files instead, which keeps them out of the
# environment of the container: TINYJUKEBOX_ADMIN_PASSWORD_FILE, TINYJUKEBOX_BOOTSTRAP_PASSWORD_FILE (the secrets of
# compose.yaml).
set -u
command=${1:-library}
[ $# -eq 0 ] || shift
case $command in
    library)
        # after a backup was put back the library starts itself again
        exec tinyjukebox-library --data /data -v "$@"
        ;;
    box)
        # after a backup was put back the box ends with status 75 to be started again, as systemd does on a box
        child=
        trap '[ -n "$child" ] && kill -TERM "$child" 2> /dev/null' TERM INT
        while :; do
            tinyjukebox --data /data --no-hardware --no-audio --no_gui -v "$@" &
            child=$!
            wait "$child"
            status=$?
            if kill -0 "$child" 2> /dev/null; then  # the wait was ended by the stop signal: wait for the box
                wait "$child"
                status=$?
            fi
            [ "$status" = 75 ] || exit "$status"
            echo "the box starts again"
        done
        ;;
    *)
        exec "$command" "$@"
        ;;
esac
