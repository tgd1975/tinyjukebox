# sourced by update_library.sh and release.sh: the DSM of the library - its address, a user who is an administrator
# and the password - from the vault of the playbook, deploy/ansible/group_vars/all/vault.yaml (the file and the
# password of update_box.sh):
#   vault_dsm_address: 192.168.11.6    # as for update_library.sh: name or address of the NAS, or the address of DSM
#   vault_dsm_user: admin
#   vault_dsm_password: '...'          # in quotes: YAML reads 0123 as a number
# (ansible-vault edit deploy/ansible/group_vars/all/vault.yaml). An encrypted vault asks for its password, three
# tries, or takes ANSIBLE_VAULT_PASSWORD_FILE. Without the file, without ansible-vault or without the keys nothing
# is taken from it, and the scripts ask as before
DSM_VAULT=deploy/ansible/group_vars/all/vault.yaml
VAULT_DSM_ADDRESS=
VAULT_DSM_USER=
VAULT_DSM_PASSWORD=

# the address of DSM for the name or address of a NAS: https://NAS:5001, or the address itself
dsm_url() {
    case $1 in
        *://*) printf '%s\n' "${1%/}" ;;
        *) printf 'https://%s:5001\n' "$1" ;;
    esac
}

# reads the three keys into VAULT_DSM_ADDRESS, VAULT_DSM_USER and VAULT_DSM_PASSWORD (not exported)
dsm_vault_read() {
    local text values python try
    [ -f "$DSM_VAULT" ] && command -v ansible-vault > /dev/null || return 0
    if head -n 1 "$DSM_VAULT" | grep -q '^\$ANSIBLE_VAULT'; then
        echo "the DSM of the library from the vault ($DSM_VAULT)" >&2
        # decrypt, not view: view masks every place where the password of the vault is part of the text
        # ($REDACTED$, ansible-core 2.22) as soon as its output is no terminal
        for try in 1 2 3; do
            text=$(ansible-vault decrypt --output - "$DSM_VAULT") && break
            [ $try -lt 3 ] || { echo "the vault does not open" >&2; exit 1; }
        done
    else
        text=$(< "$DSM_VAULT")
    fi
    # the YAML is read by the Python of ansible, which has PyYAML; the vault reaches it on its input only
    python=$(sed -n '1s/^#! *//p' "$(command -v ansible-vault)")
    values=$(printf '%s\n' "$text" | ${python:-python3} -c '
import json, sys, yaml
try:
    data = yaml.safe_load(sys.stdin) or {}
except yaml.YAMLError as error:
    sys.exit(f"the vault is no valid YAML: {str(error).splitlines()[-1].strip()}")
print(json.dumps({key: str(data.get(f"vault_dsm_{key}") or "") for key in ("address", "user", "password")}))') ||
        return 0
    VAULT_DSM_ADDRESS=$(jq -r .address <<< "$values")
    VAULT_DSM_USER=$(jq -r .user <<< "$values")
    VAULT_DSM_PASSWORD=$(jq -r .password <<< "$values")
    [ -n "$VAULT_DSM_ADDRESS$VAULT_DSM_USER$VAULT_DSM_PASSWORD" ] ||
        echo "the vault has no vault_dsm_address, vault_dsm_user, vault_dsm_password" >&2
}

# sets DSM_USER and DSM_PASSWORD (not exported) from the vault for DSM, the address of DSM: user and password belong
# to the DSM of the vault and go to no other. A user given beforehand keeps its own password
dsm_credentials() {
    local dsm=$1
    [ -n "$VAULT_DSM_USER" ] && [ -n "$VAULT_DSM_PASSWORD" ] || return 0
    [ -z "${DSM_USER:-}" ] || [ -z "${DSM_PASSWORD:-}" ] || return 0
    if [ -n "$VAULT_DSM_ADDRESS" ] && [ "$(dsm_url "$VAULT_DSM_ADDRESS")" != "$dsm" ]; then
        echo "user and password of the vault are those of $(dsm_url "$VAULT_DSM_ADDRESS"), not of $dsm" >&2
        return 0
    fi
    [ -z "${DSM_USER:-}" ] || [ "$DSM_USER" = "$VAULT_DSM_USER" ] || return 0
    DSM_USER=$VAULT_DSM_USER
    DSM_PASSWORD=$VAULT_DSM_PASSWORD
}
