#!/usr/bin/env bash
# secrets/generate-oidc-secrets.sh
#
# Generates OIDC client secrets for Authentik blueprint provisioning.
# Run from the repo root (or from secrets/) after the initial deploy:
#
#   bash secrets/generate-oidc-secrets.sh
#
# What this script does:
#   - Creates/replaces authentik-oidc-secrets.age  (8 client secrets for the
#     blueprint; Authentik reads them via !Env at startup, and Jellyfin's,
#     Audiobookshelf's and Vaultwarden's configs read their own)
#   - Appends/replaces GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET in grafana-env.age
#   - Creates/replaces nextcloud-oidc-env.age
#   - Creates/replaces immich-oidc-env.age
#   - Creates/replaces romm-oidc-env.age
#   - Appends/replaces SSO_CLIENT_SECRET in vaultwarden-env.age
#   - Creates/updates forgejo-env.age (break-glass admin password + the same
#     client secret, which Forgejo's bootstrap reads)
#   - Prints client_id/secret for Home Assistant and Jellyfin
#     (those services need manual UI config — see notes below each)
#
# Secrets that still need manual input AFTER running this script:
#   Home Assistant — UI setup required (no NixOS config hook available):
#     Settings → Devices & Services → Add Integration → search "Authentik"
#     or HACS: https://github.com/jchonig/ha-authentik
#   Jellyfin — SSO Authentication plugin required:
#     Install from the plugin catalogue, then configure with the values printed.
#
# Requires: agenix (in PATH), openssl, secrets/secrets.nix

set -euo pipefail

cd "$(dirname "$0")"

AGENIX="agenix"
if ! command -v agenix &>/dev/null; then
  echo "ERROR: agenix not found. Install it first:"
  echo "  nix shell github:ryantm/agenix"
  exit 1
fi

if [ ! -f secrets.nix ]; then
  echo "ERROR: secrets/secrets.nix not found."
  echo "  cp secrets/secrets.nix.example secrets/secrets.nix"
  echo "  # then fill in your SSH public keys"
  exit 1
fi

rand() { openssl rand -hex 32; }

# Write full content to an agenix file (creates or overwrites existing content).
overwrite() {
  local file="$1"
  local content="$2"
  local script
  script=$(mktemp)
  cat > "$script" << 'EOF'
#!/bin/sh
printf '%s\n' "$AGENIX_CONTENT" > "$1"
EOF
  chmod +x "$script"
  AGENIX_CONTENT="$content" EDITOR="$script" $AGENIX -e "$file"
  rm -f "$script"
  echo "  OK    $file"
}

# Replace or append a KEY=value line in an existing agenix file.
upsert_line() {
  local file="$1"
  local line="$2"
  local key="${line%%=*}"
  local script
  script=$(mktemp)
  cat > "$script" << 'EOF'
#!/bin/sh
tmp=$(mktemp)
grep -v "^${AGENIX_KEY}=" "$1" > "$tmp" || true
printf '%s\n' "$AGENIX_LINE" >> "$tmp"
cat "$tmp" > "$1"
rm -f "$tmp"
EOF
  chmod +x "$script"
  AGENIX_KEY="$key" AGENIX_LINE="$line" EDITOR="$script" $AGENIX -e "$file"
  rm -f "$script"
  echo "  OK    $file (set $key)"
}

# ── Generate ─────────────────────────────────────────────────────────────────

echo "Generating OIDC client secrets..."
echo

GRAFANA_SECRET=$(rand)
NEXTCLOUD_SECRET=$(rand)
IMMICH_SECRET=$(rand)
HA_SECRET=$(rand)
JELLYFIN_SECRET=$(rand)
ROMM_SECRET=$(rand)
AUDIOBOOKSHELF_SECRET=$(rand)
VAULTWARDEN_SECRET=$(rand)
FORGEJO_SECRET=$(rand)
FORGEJO_ADMIN_PASSWORD=$(rand)

overwrite "authentik-oidc-secrets.age" \
"AUTHENTIK_GRAFANA_CLIENT_SECRET=$GRAFANA_SECRET
AUTHENTIK_NEXTCLOUD_CLIENT_SECRET=$NEXTCLOUD_SECRET
AUTHENTIK_IMMICH_CLIENT_SECRET=$IMMICH_SECRET
AUTHENTIK_HA_CLIENT_SECRET=$HA_SECRET
AUTHENTIK_JELLYFIN_CLIENT_SECRET=$JELLYFIN_SECRET
AUTHENTIK_ROMM_CLIENT_SECRET=$ROMM_SECRET
AUTHENTIK_AUDIOBOOKSHELF_CLIENT_SECRET=$AUDIOBOOKSHELF_SECRET
AUTHENTIK_VAULTWARDEN_CLIENT_SECRET=$VAULTWARDEN_SECRET
AUTHENTIK_FORGEJO_CLIENT_SECRET=$FORGEJO_SECRET"

upsert_line "grafana-env.age" \
  "GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET=$GRAFANA_SECRET"

overwrite "nextcloud-oidc-env.age" \
"NEXTCLOUD_OIDC_CLIENT_ID=nextcloud
NEXTCLOUD_OIDC_CLIENT_SECRET=$NEXTCLOUD_SECRET"

overwrite "immich-oidc-env.age" \
"IMMICH_OAUTH_CLIENT_ID=immich
IMMICH_OAUTH_CLIENT_SECRET=$IMMICH_SECRET"

overwrite "romm-oidc-env.age" \
"OIDC_CLIENT_SECRET=$ROMM_SECRET"

upsert_line "vaultwarden-env.age" \
  "SSO_CLIENT_SECRET=$VAULTWARDEN_SECRET"

upsert_line "forgejo-env.age" \
  "FORGEJO_ADMIN_PASSWORD=$FORGEJO_ADMIN_PASSWORD"
upsert_line "forgejo-env.age" \
  "FORGEJO_OAUTH_CLIENT_SECRET=$FORGEJO_SECRET"

# ── Summary ───────────────────────────────────────────────────────────────────

echo
echo "Done. Grafana, Nextcloud, Immich, RomM, Audiobookshelf, Vaultwarden and Forgejo are fully wired."
echo
echo "Manual UI setup required for the following two services."
echo "Secrets are also stored in authentik-oidc-secrets.age for later retrieval."
echo
echo "  Home Assistant:"
echo "    client_id:     home-assistant"
echo "    client_secret: $HA_SECRET"
echo "    discovery URL: https://auth.<domain>/application/o/home-assistant/.well-known/openid-configuration"
echo
echo "  Jellyfin (SSO Authentication plugin):"
echo "    client_id:     jellyfin"
echo "    client_secret: $JELLYFIN_SECRET"
echo "    auth URL:      https://auth.<domain>/application/o/authorize/"
echo "    token URL:     https://auth.<domain>/application/o/token/"
echo "    userinfo URL:  https://auth.<domain>/application/o/userinfo/"
echo
echo "Commit the updated .age files:"
echo "  git add secrets/*.age && git commit -m 'add OIDC client secrets'"
