# pkgs/home-assistant-oidc-secret/default.nix
#
# ha-oidc-secret <env-file> <secrets.yaml> <user>
#
# Home Assistant's YAML cannot read environment variables, so auth_oidc's
# client secret reaches it as !secret auth_oidc_client_secret. This copies
# AUTHENTIK_HA_CLIENT_SECRET from the Authentik client-secret file into
# secrets.yaml, changing only that key: the file is HA's own and may hold
# others. The value is written as a JSON string, which YAML reads exactly.
# A missing secret fails before anything is written, so HA never starts with
# an empty one.
#
# Run as root, which alone can read the client-secret file, it reads the
# secret and nothing else: the file is written by ha-oidc-secret-write as
# <user>. HA's user owns the directory, so a root writer could be pointed at a
# file of root's through a symlink at secrets.yaml or at its temporary file.
{
  writeShellApplication,
  coreutils,
  gnugrep,
  jq,
  util-linux,
}:

let
  write = writeShellApplication {
    name = "ha-oidc-secret-write";
    # setpriv --reset-env leaves it a bare PATH: everything it runs is listed.
    runtimeInputs = [
      coreutils
      gnugrep
      jq
    ];
    text = ''
      # ha-oidc-secret-write <secrets.yaml>, the secret on stdin.
      yaml=$1
      secret=$(cat)

      if [ -L "$yaml" ]; then
        echo "ha-oidc-secret: $yaml is a symlink; refusing to write through it" >&2
        exit 1
      fi

      tmp=$(mktemp "$yaml.XXXXXX")
      trap 'rm -f "$tmp"' EXIT
      if [ -e "$yaml" ]; then
        # 1 is "no other lines"; anything else (an unreadable file) must not
        # rewrite secrets.yaml without its other keys.
        grep -v '^auth_oidc_client_secret:' "$yaml" > "$tmp" || [ $? -eq 1 ]
      fi
      printf 'auth_oidc_client_secret: %s\n' "$(jq -cn --arg s "$secret" '$s')" >> "$tmp"
      mv -f "$tmp" "$yaml"
      trap - EXIT
    '';
  };
in
writeShellApplication {
  name = "ha-oidc-secret";
  runtimeInputs = [
    coreutils
    util-linux
  ];
  text = ''
    env_file=$1
    yaml=$2
    user=$3
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

    if [ "$(id -un)" = "$user" ]; then
      printf '%s' "$secret" | ${write}/bin/ha-oidc-secret-write "$yaml"
    else
      printf '%s' "$secret" \
        | setpriv --reuid="$user" --regid="$user" --clear-groups --reset-env \
          ${write}/bin/ha-oidc-secret-write "$yaml"
    fi
  '';
}
