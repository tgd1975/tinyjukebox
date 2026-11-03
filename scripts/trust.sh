# shellcheck shell=bash
# which server a script on the PC sends a password or a token to, when the server has a certificate of its own - DSM
# on the NAS, the library (docs/developer/library.md, section 8.2). Sourced by update_library.sh, release.sh and
# scripts/check_release.sh.
#   trust URL [PIN]    sets TLS, the options of curl for the server of URL:
#                      - TINYJUKEBOX_CACERT names the certificate of an authority that signed the one of the server, or
#                      - the public key of the server must be PIN, or the one accepted for it before, or - the first
#                        time - the one it shows now, after a person compared it and accepted it (trust on first use)
# The accepted keys are kept in ~/.config/tinyjukebox/pins, one line per server: "host:port sha256//...". A server
# that shows another key later is refused (curl ends with status 90) until its line is removed.
PINS=${XDG_CONFIG_HOME:-$HOME/.config}/tinyjukebox/pins
# shellcheck disable=SC2034  # for the scripts that source this file
TLS=()

server_of() {  # URL: host:port
    local server=${1#*://}
    server=${server%%/*}
    case $server in *\]:* | [!\[]*:*) echo "$server" ;; *) echo "$server:443" ;; esac
}

terminal() { { : < /dev/tty; } 2> /dev/null; }

trust() {  # URL [PIN]
    local server host pin certificate answer sni=()
    server=$(server_of "$1")
    if [ -n "${TINYJUKEBOX_CACERT:-}" ]; then
        TLS=(--cacert "$TINYJUKEBOX_CACERT")
        return 0
    fi
    pin=${2:-$(awk -v server="$server" '$1 == server { pin = $2 } END { print pin }' "$PINS" 2> /dev/null)}
    if [ -z "$pin" ]; then
        host=${server%:*}
        # the name of the server, as curl sends it; not for an address
        [[ $host =~ ^[0-9.]+$|^\[ ]] || sni=(-servername "$host")
        certificate=$(openssl s_client -connect "$server" "${sni[@]}" < /dev/null 2> /dev/null | openssl x509 2> /dev/null) \
            || true
        [ -n "$certificate" ] || { echo "$server shows no certificate - is the address right?" >&2; return 1; }
        pin=sha256//$(openssl x509 -pubkey -noout <<< "$certificate" | openssl pkey -pubin -outform der \
            | openssl dgst -sha256 -binary | openssl enc -base64)
        terminal || { echo "$server is not known yet: run this once in a terminal to accept it, or give its pin" >&2; return 1; }
        {
            echo "$server is not known yet. It shows this certificate:"
            openssl x509 -noout -subject -enddate -fingerprint -sha256 <<< "$certificate" | sed 's/^/    /'
            echo "    public key: $pin"
            echo "Compare the fingerprint with the one the browser shows for this address - for the library also the"
            echo "public key with the pin of the line in its tab \"Boxes\"."
        } > /dev/tty
        read -r -p "Accept it for $server from now on? [y/N] " answer < /dev/tty
        [ "$answer" = y ] || { echo "not accepted: nothing was sent to $server" >&2; return 1; }
        mkdir -p "$(dirname "$PINS")"
        printf '%s %s\n' "$server" "$pin" >> "$PINS"
        chmod 600 "$PINS"
    fi
    # -k: the certificate is of the server itself, no authority signed it. curl accepts exactly this key
    # shellcheck disable=SC2034
    TLS=(-k --pinnedpubkey "$pin")
}

refused_key() {  # status of curl, URL: says why, if the server showed another key than the accepted one
    [ "$1" = 90 ] || return 0
    echo "$(server_of "$2") shows another public key than the one given, or accepted before ($PINS): nothing was" \
        "sent. If its certificate was renewed on purpose, remove its line there and run this again" >&2
}
