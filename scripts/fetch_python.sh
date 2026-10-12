#!/bin/bash
# provides the self-contained Python that every slot brings along (python-build-standalone) and prints its folder
#   scripts/fetch_python.sh [x86_64|aarch64]
# fetched once, verified, unpacked and reduced to what a box needs; kept in $PYTHON_STANDALONE_DIR (default:
# ~/.cache/tinyjukebox/python)
set -eu
RELEASE=20261003
PYTHON=3.13.16
ARCH=${1:-$(uname -m)}
case $ARCH in
    x86_64) SHA256=4595c5589fff7bf0cb158d9a88a797e0d791fa33830770fcb7bf3f4b104feeae ;;
    aarch64) SHA256=6e9641400f8debd9b7924b27b5ff0662c372852382e291a76c450c7b67414cf6 ;;
    *) echo "no Python for the architecture '$ARCH'" >&2; exit 1 ;;
esac
NAME=cpython-$PYTHON+$RELEASE-$ARCH-unknown-linux-gnu-install_only_stripped
DIR=${PYTHON_STANDALONE_DIR:-$HOME/.cache/tinyjukebox/python}
FOLDER=$DIR/$NAME

if [ ! -d "$FOLDER" ]; then
    mkdir -p "$DIR"
    curl -fsSL -o "$DIR/$NAME.tar.gz" "https://github.com/astral-sh/python-build-standalone/releases/download/$RELEASE/$NAME.tar.gz"
    echo "$SHA256  $DIR/$NAME.tar.gz" | sha256sum -c --quiet - >&2
    rm -rf "$FOLDER.new"
    mkdir "$FOLDER.new"
    tar -xzf "$DIR/$NAME.tar.gz" -C "$FOLDER.new" --strip-components=1 --no-same-owner
    rm "$DIR/$NAME.tar.gz"
    (
        # the interpreter is one static program: the shared library, Tcl/Tk, pip and the tools around it are not needed
        cd "$FOLDER.new"
        rm -rf include share lib/pkgconfig lib/libpython* lib/tcl* lib/tk* lib/itcl* lib/thread* lib/libtcl* lib/libtk*
        find bin -mindepth 1 ! -name 'python3*' -delete
        rm -f bin/python3*-config
        cd lib/python3.*
        rm -rf test idlelib tkinter turtledemo ensurepip lib2to3 pydoc_data config-* site-packages/* \
            lib-dynload/_tkinter* lib-dynload/_dbm*
    )
    mv "$FOLDER.new" "$FOLDER"
fi
echo "$FOLDER"
