#!/bin/bash
# tries the image of this version (docs/developer/library.md, section 10): builds it for this computer from dist/,
# starts a library and a box that is linked to it, as compose.yaml does, and waits until the box appears in the
# library. Runs in the workflow on every push; on the PC it needs Docker and leaves nothing behind.
set -eu
cd "$(dirname "$0")/../.."
VERSION=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -n 1)
IMAGE=tinyjukebox-test
NAME=tinyjukebox-test
ADMIN=password-of-the-test
BOOTSTRAP=bootstrap-password-of-the-test
TINYJUKEBOX_IMAGE=$IMAGE scripts/build_image.sh --load

cleanup() {
    docker rm -f "$NAME-library" "$NAME-box" > /dev/null 2>&1 || true
    docker volume rm "$NAME-library" "$NAME-box" > /dev/null 2>&1 || true
    docker network rm "$NAME" > /dev/null 2>&1 || true
}
fail() {
    echo "FAILED: $*" >&2
    docker logs "$NAME-library" 2>&1 | tail -n 20 >&2 || true
    docker logs "$NAME-box" 2>&1 | tail -n 20 >&2 || true
    exit 1
}
wait_for() {  # seconds command...
    local limit=$1 start=$SECONDS
    shift
    until "$@" > /dev/null 2>&1; do
        [ $((SECONDS - start)) -lt "$limit" ] || return 1
        sleep 2
    done
}
cleanup
trap cleanup EXIT
docker network create "$NAME" > /dev/null
docker run -d --name "$NAME-library" --network "$NAME" -p 127.0.0.1:18866:8866 -v "$NAME-library:/data" \
    -e TINYJUKEBOX_ADMIN_PASSWORD=$ADMIN -e TINYJUKEBOX_BOOTSTRAP_PASSWORD=$BOOTSTRAP "$IMAGE:$VERSION" > /dev/null
wait_for 120 curl -ksf https://127.0.0.1:18866/api/identity || fail "the library does not answer"
docker run -d --name "$NAME-box" --network "$NAME" -p 127.0.0.1:18443:8443 -v "$NAME-box:/data" \
    -e TINYJUKEBOX_ADMIN_PASSWORD=$ADMIN -e TINYJUKEBOX_LIBRARY="https://$NAME-library:8866" \
    -e TINYJUKEBOX_BOOTSTRAP_PASSWORD=$BOOTSTRAP "$IMAGE:$VERSION" box > /dev/null
wait_for 120 curl -ksf https://127.0.0.1:18443/api/identity || fail "the box does not answer"
TOKEN=$(curl -ksf https://127.0.0.1:18866/api/login -H 'Content-Type: application/json' \
    -d "{\"username\": \"admin\", \"password\": \"$ADMIN\"}" | jq -r .token)
registered() {
    curl -ksf https://127.0.0.1:18866/api/library/overview -H "Authorization: Bearer $TOKEN" | jq -e '.boxes | length > 0'
}
wait_for 120 registered || fail "the box did not register with the library"
[ "$(curl -ksf https://127.0.0.1:18866/install.sh | head -c 11)" = '#!/bin/bash' ] || echo "note: the library offers no installer (no slot in dist)"
echo "the image of version $VERSION runs as library and as box; the box registered with the library"
