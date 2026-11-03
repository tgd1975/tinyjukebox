#!/bin/bash
# brings master of this clone onto the box and sets it up: runs the ansible playbook (docs/developer/deployment.md)
#   ./update_box.sh                    # everything
#   ./update_box.sh --tags tinyjukebox # one role (base, tinyjukebox, audio, bluetooth, rfid)
#   ./update_box.sh --check --diff     # further options are passed on to ansible-playbook
# only what is committed on master reaches the box
set -eu
cd "$(dirname "$0")"
VAULT=deploy/ansible/group_vars/all/vault.yaml

for tool in ansible-playbook git; do
    command -v "$tool" > /dev/null || { echo "$tool is missing" >&2; exit 1; }
done
# the box gets the code and the config of the pushed commit; the installer is built from the files as they are here
[ -z "$(git status --porcelain)" ] \
    || echo "warning: uncommitted changes of code and config do not reach the box - those of the installer do" >&2
BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$BRANCH" = master ] || echo "warning: on branch $BRANCH - the box gets master" >&2
echo "master: $(git log -1 --format='%h %s' master)"

# the vault file is optional (without it the web page gets no users); encrypted, it needs the password
VAULT_ARGS=()
if [ -f "$VAULT" ] && head -n 1 "$VAULT" | grep -q '^\$ANSIBLE_VAULT' && [ -z "${ANSIBLE_VAULT_PASSWORD_FILE:-}" ]; then
    VAULT_ARGS=(--ask-vault-pass)
fi

# the playbook works as root on the box (become: true), and sudo of pi asks for its password
BECOME_ARGS=()
if [ -z "${ANSIBLE_BECOME_PASSWORD_FILE:-}" ]; then
    BECOME_ARGS=(--ask-become-pass)
fi

cd deploy/ansible
exec ansible-playbook site.yaml "${VAULT_ARGS[@]}" "${BECOME_ARGS[@]}" "$@"
