# tests/ha-oidc-secret.nix
#
# ha-oidc-secret (pkgs/home-assistant-oidc-secret) puts Home Assistant's
# OIDC client secret into secrets.yaml before HA starts. The file holds other
# keys too (the live server has mqtt_password), so the writer must change
# only its own key, stay idempotent across restarts, refuse an empty secret
# without touching the file, and quote values YAML would misread.
#
# Root only reads the secret; the file is written as the given user, because
# HA's own user owns the directory and could otherwise point secrets.yaml (or
# the temporary file) at a file of root's. The build sandbox has one user, so
# the user switch itself is checked on the server (plan Task 4); here the
# writer's own refusals are: a symlinked secrets.yaml, and one it cannot read.
{ pkgs }:

let
  writer = pkgs.callPackage ../pkgs/home-assistant-oidc-secret { };
  yq = "${pkgs.yq-go}/bin/yq";
in
pkgs.runCommand "ha-oidc-secret-check" { } ''
  set -euo pipefail
  user="$(id -un)"
  cd "$(mktemp -d)"

  # A secret with characters YAML would misread unquoted.
  cat > env <<'EOF'
  AUTHENTIK_HA_CLIENT_SECRET='a:b #c "d" e'
  AUTHENTIK_OTHER_CLIENT_SECRET=other
  EOF
  printf 'mqtt_password: keep-me\n' > secrets.yaml

  # 1. Sets the key and keeps the other one.
  ${writer}/bin/ha-oidc-secret env secrets.yaml "$user"
  [ "$(${yq} '.auth_oidc_client_secret' secrets.yaml)" = 'a:b #c "d" e' ]
  [ "$(${yq} '.mqtt_password' secrets.yaml)" = keep-me ]
  [ "$(stat -c %a secrets.yaml)" = 600 ]

  # 2. Idempotent: a second run leaves one key with the new value.
  sed -i "s/^AUTHENTIK_HA_CLIENT_SECRET=.*/AUTHENTIK_HA_CLIENT_SECRET=rotated/" env
  ${writer}/bin/ha-oidc-secret env secrets.yaml "$user"
  [ "$(grep -c '^auth_oidc_client_secret:' secrets.yaml)" = 1 ]
  [ "$(${yq} '.auth_oidc_client_secret' secrets.yaml)" = rotated ]
  [ "$(${yq} '.mqtt_password' secrets.yaml)" = keep-me ]

  # 3. A missing secret fails and leaves the file as it was.
  cp secrets.yaml before.yaml
  printf 'AUTHENTIK_OTHER_CLIENT_SECRET=other\n' > env
  if ${writer}/bin/ha-oidc-secret env secrets.yaml "$user" 2>err; then
    echo "expected failure on a missing secret" >&2; exit 1
  fi
  grep -q AUTHENTIK_HA_CLIENT_SECRET err
  cmp before.yaml secrets.yaml

  # 4. A secrets.yaml it cannot read fails, rather than being rewritten
  #    without its other keys.
  printf 'AUTHENTIK_HA_CLIENT_SECRET=x\n' > env
  chmod 000 secrets.yaml
  if ${writer}/bin/ha-oidc-secret env secrets.yaml "$user" 2>err; then
    echo "expected failure on an unreadable secrets.yaml" >&2; exit 1
  fi
  chmod 600 secrets.yaml
  cmp before.yaml secrets.yaml

  # 5. A symlinked secrets.yaml is refused, and its target left alone.
  printf 'root: only\n' > victim
  mv secrets.yaml real.yaml
  ln -s victim secrets.yaml
  if ${writer}/bin/ha-oidc-secret env secrets.yaml "$user" 2>err; then
    echo "expected failure on a symlinked secrets.yaml" >&2; exit 1
  fi
  grep -q symlink err
  [ "$(cat victim)" = "root: only" ]
  [ -L secrets.yaml ]
  rm secrets.yaml

  # 6. No secrets.yaml yet: it is created. Run with an empty environment,
  #    as setpriv --reset-env leaves the writer on the server: nothing may
  #    come from the caller's PATH.
  printf 'AUTHENTIK_HA_CLIENT_SECRET=fresh\n' > env
  ${pkgs.coreutils}/bin/env -i ${writer}/bin/ha-oidc-secret env secrets.yaml "$user"
  [ "$(${yq} '.auth_oidc_client_secret' secrets.yaml)" = fresh ]

  touch $out
''
