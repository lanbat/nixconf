# pkgs/home-assistant-oidc-secret/default.nix
#
# ha-oidc-secret <env-file> <secrets.yaml> <owner:group>
#
# Home Assistant's YAML cannot read environment variables, so auth_oidc's
# client secret reaches it as !secret auth_oidc_client_secret. This copies
# AUTHENTIK_HA_CLIENT_SECRET from the Authentik client-secret file into
# secrets.yaml, changing only that key: the file is HA's own and may hold
# others. The value is written as a JSON string, which YAML reads exactly.
# A missing secret fails before anything is written, so HA never starts with
# an empty one.
{ writeShellApplication, jq }:

writeShellApplication {
  name = "ha-oidc-secret";
  runtimeInputs = [ jq ];
  text = ''
    env_file=$1
    yaml=$2
    owner=$3
    # "." searches PATH for a bare name; always give it a path.
    case $env_file in
    */*) ;;
    *) env_file=./$env_file ;;
    esac

    # The file is KEY=value lines, as the other readers source it.
    secret=$(
      set -a
      # shellcheck disable=SC1090
      . "$env_file"
      printf '%s' "''${AUTHENTIK_HA_CLIENT_SECRET:-}"
    )
    if [ -z "$secret" ]; then
      echo "ha-oidc-secret: AUTHENTIK_HA_CLIENT_SECRET is missing from $env_file" >&2
      exit 1
    fi

    tmp=$(mktemp "$yaml.XXXXXX")
    trap 'rm -f "$tmp"' EXIT
    chmod 0600 "$tmp"
    if [ -e "$yaml" ]; then
      grep -v '^auth_oidc_client_secret:' "$yaml" > "$tmp" || true
    fi
    printf 'auth_oidc_client_secret: %s\n' "$(jq -cn --arg s "$secret" '$s')" >> "$tmp"
    chown "$owner" "$tmp"
    mv -f "$tmp" "$yaml"
    trap - EXIT
  '';
}
