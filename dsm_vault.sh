# sourced by update_library.sh and release.sh: the user and the password of DSM from the vault of the playbook,
# deploy/ansible/group_vars/all/vault.yaml - the file and the password of update_box.sh - with the keys
#   vault_dsm_user: admin
#   vault_dsm_password: '...'    # in quotes: YAML reads 0123 as a number
# (ansible-vault edit deploy/ansible/group_vars/all/vault.yaml). An encrypted vault asks for its password, three
# tries, or takes ANSIBLE_VAULT_PASSWORD_FILE. Without the file, without ansible-vault or without the keys it sets
# nothing, and the scripts ask for user and password as before. DSM_USER and DSM_PASSWORD, when set, come first
DSM_VAULT=deploy/ansible/group_vars/all/vault.yaml

# sets DSM_USER and DSM_PASSWORD (not exported) from the vault, unless both are set
dsm_from_vault() {
    local text values python try
    [ -z "${DSM_USER:-}" ] || [ -z "${DSM_PASSWORD:-}" ] || return 0
    [ -f "$DSM_VAULT" ] && command -v ansible-vault > /dev/null || return 0
    if head -n 1 "$DSM_VAULT" | grep -q '^\$ANSIBLE_VAULT'; then
        echo "user and password of DSM from the vault ($DSM_VAULT)" >&2
        for try in 1 2 3; do
            text=$(ansible-vault view "$DSM_VAULT") && break
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
print(json.dumps({"user": data.get("vault_dsm_user"), "password": data.get("vault_dsm_password")}))') || return 0
    if [ -z "$(jq -r '.user // empty' <<< "$values")" ] || [ -z "$(jq -r '.password // empty' <<< "$values")" ]; then
        echo "the vault has no vault_dsm_user and vault_dsm_password" >&2
        return 0
    fi
    # a user given beforehand keeps its own password: the one of the vault belongs to the user of the vault
    if [ -n "${DSM_USER:-}" ] && [ "$DSM_USER" != "$(jq -r .user <<< "$values")" ]; then
        return 0
    fi
    DSM_USER=$(jq -r .user <<< "$values")
    DSM_PASSWORD=$(jq -r .password <<< "$values")
}
