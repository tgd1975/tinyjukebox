#!/bin/bash
# creates the package of the library for the Synology NAS from deploy/synology (docs/developer/library.md,
# sections 3.2 and 3.6)
#   ./build_package.sh                 # dist/tinyjukebox-library-<version>-0001-<arch>.spk, version from pyproject.toml
#   ./build_package.sh 2               # build number 2: a newer package of the same version
#   ./build_package.sh 1 --slots DIR   # with the slot of this version for the boxes from DIR (default: dist)
# The package brings tinyjukebox and what it needs (target/lib), for the Python 3.12 of SynoCommunity on aarch64, and
# the slot of its own version for the boxes if it lies in DIR. It is installed by hand: Package Center of DSM > Manual
# Install. Needs: uv.
set -eu
cd "$(dirname "$0")"
SOURCE=deploy/synology
PORT=8866  # of the web page - above 1024, because a package does not run as root
PYTHON_VERSION=3.12                       # the Python of the NAS
PYTHON_PLATFORM=aarch64-manylinux_2_28    # DS418j: aarch64; DSM 7.2 has glibc 2.36

BUILD=${1:-1}
[[ $BUILD =~ ^[0-9]+$ ]] || { echo "usage: $0 [BUILD NUMBER] [--slots DIR]" >&2; exit 1; }
SLOTS=dist
[ "${2:-}" != --slots ] || SLOTS=$3
RELEASE=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
VERSION=$RELEASE-$(printf '%04d' "$((10#$BUILD))")
ARCH=$(sed -n 's/^arch="\(.*\)"/\1/p' "$SOURCE/INFO")
SPK=dist/tinyjukebox-library-$VERSION-$ARCH.spk

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -r "$SOURCE"/. "$STAGE"
find "$STAGE" -name __pycache__ -prune -exec rm -rf {} +
grep -rlI '@VERSION@\|@PORT@' "$STAGE" | xargs sed -i "s/@VERSION@/$VERSION/g; s/@PORT@/$PORT/g"
echo "$VERSION" > "$STAGE/package/VERSION"

# tinyjukebox and exactly the dependencies of uv.lock, without the hardware libraries of a box: ready-made files for the
# Python and the processor of the NAS, so that the installation there needs neither internet nor a compiler
uv build --quiet --wheel --out-dir "$STAGE/wheel"
uv export --quiet --locked --no-dev --no-emit-project --format requirements-txt --output-file "$STAGE/requirements.txt"
# (tantamount and asyncscheduler have no ready-made files; they are pure Python and are built here)
uv pip install --quiet --target "$STAGE/package/lib" --python-version "$PYTHON_VERSION" \
    --python-platform "$PYTHON_PLATFORM" --only-binary :all: --no-binary tantamount --no-binary asyncscheduler \
    -r "$STAGE/requirements.txt"
uv pip install --quiet --target "$STAGE/package/lib" --python-version "$PYTHON_VERSION" \
    --python-platform "$PYTHON_PLATFORM" --no-deps "$STAGE"/wheel/tinyjukebox-*.whl
# (what the source packages of tantamount and asyncscheduler install next to their code is not needed)
rm -rf "$STAGE/package/lib/bin" "$STAGE/package/lib/share" "$STAGE/package/lib/example" \
    "$STAGE/package/lib/tests_unit" "$STAGE/package/lib/README.rst" "$STAGE/wheel" "$STAGE/requirements.txt"
find "$STAGE/package/lib" -name __pycache__ -prune -exec rm -rf {} +

# the slot of this version for the boxes (section 6.13)
mkdir -p "$STAGE/package/slots"
for file in "$SLOTS"/tinyjukebox-box-"$RELEASE"-*; do
    [ -e "$file" ] && cp "$file" "$STAGE/package/slots/"
done
[ -n "$(ls -A "$STAGE/package/slots")" ] || echo "note: no slot of version $RELEASE in $SLOTS - the package brings none" >&2
# and the installer of a box of the same version, which the library hands out (docs/developer/deployment.md, section 5)
if grep -qs "^VERSION=\"$RELEASE\"$" "$SLOTS/install.sh"; then
    cp "$SLOTS/install.sh" "$STAGE/package/slots/tinyjukebox-install-$RELEASE.sh"
else
    echo "note: no installer of version $RELEASE in $SLOTS - the library offers no installation of it" >&2
fi
cp "$STAGE/package/ui/images/icon_64.png" "$STAGE/PACKAGE_ICON.PNG"
cp "$STAGE/package/ui/images/icon_256.png" "$STAGE/PACKAGE_ICON_256.PNG"
chmod -R u=rwX,go=rX "$STAGE"
chmod 755 "$STAGE"/scripts/*

# package.tgz becomes the folder 'target' of the package on the NAS; INFO carries its checksum
(cd "$STAGE/package" && tar --owner=root --group=root -czf ../package.tgz -- *)
chmod 644 "$STAGE/package.tgz"
sed -i "s/@CHECKSUM@/$(md5sum "$STAGE/package.tgz" | cut -d ' ' -f 1)/" "$STAGE/INFO"
mkdir -p dist
tar --owner=root --group=root -C "$STAGE" -cf "$SPK" INFO package.tgz scripts conf WIZARD_UIFILES \
    PACKAGE_ICON.PNG PACKAGE_ICON_256.PNG
echo "$SPK"
