#!/usr/bin/env bash
# secrets/generate-jellyfin-watch-key.sh
#
# Mint (or reuse) the Jellyfin API key the storage Pi's media watcher uses, and
# write it into secrets/jellyfin-watch-env.age for the Pi to read via agenix.
#
# Run from the repo root after Jellyfin is deployed and bootstrapped:
#
#   bash secrets/generate-jellyfin-watch-key.sh [server-host]
#
# The script SSHs to the server, authenticates to Jellyfin as the owner
# (hass-bootstrap-env), and mints an API key named "jellyfin-watch" through the
# same /Auth/Keys flow jellyfin-bootstrap uses for yt-show and Homepage. The key
# is idempotent: re-running reuses the existing key for that app name.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/secrets"

SERVER_HOST="${1:-}"
if [[ -z "$SERVER_HOST" ]]; then
  SERVER_IP="$(bash "$(dirname "$0")/lib/read-deploy.sh" server-ip 2>/dev/null || true)"
  SERVER_HOST="admin@${SERVER_IP:-192.168.1.10}"
fi

AGENIX="agenix"
if ! command -v agenix &>/dev/null; then
  echo "ERROR: agenix not found. Run from the dev shell: nix develop"
  exit 1
fi

if [[ ! -f secrets.nix ]]; then
  echo "ERROR: secrets/secrets.nix not found."
  echo "  cp secrets/secrets.nix.example secrets/secrets.nix"
  exit 1
fi

echo "Minting the jellyfin-watch API key on ${SERVER_HOST}..."

KEY="$(
  ssh -o BatchMode=yes "$SERVER_HOST" 'bash -s' <<'EOF'
set -euo pipefail
JELLYFIN_URL="http://127.0.0.1:8096"
OWNER_USERNAME="$(sudo sed -n "s/^OWNER_USERNAME=//p" /run/agenix/hass-bootstrap-env 2>/dev/null || true)"
OWNER_PASSWORD="$(sudo sed -n "s/^OWNER_PASSWORD=//p" /run/agenix/hass-bootstrap-env 2>/dev/null || true)"
if [[ -z "$OWNER_USERNAME" || -z "$OWNER_PASSWORD" ]]; then
  echo "jellyfin-watch-key: no owner credentials in hass-bootstrap-env" >&2
  exit 1
fi
curl -fsS "${JELLYFIN_URL}/System/Info/Public" | jq -e '.StartupWizardCompleted == true' >/dev/null || {
  echo "jellyfin-watch-key: Jellyfin has not finished onboarding" >&2
  exit 1
}
EMBY_CLIENT='MediaBrowser Client="lanbat-jellyfin-watch", Device="server", DeviceId="lanbat-jellyfin-watch", Version="10.11.7"'
TOKEN="$(curl -fsS -X POST "${JELLYFIN_URL}/Users/AuthenticateByName" \
  -H "Content-Type: application/json" \
  -H "Authorization: ${EMBY_CLIENT}" \
  -d "$(jq -n --arg u "$OWNER_USERNAME" --arg p "$OWNER_PASSWORD" '{Username: $u, Pw: $p}')" \
  | jq -r .AccessToken)"
AUTH_HEADER="${EMBY_CLIENT}, Token=\"${TOKEN}\""
EXISTING="$(curl -fsS "${JELLYFIN_URL}/Auth/Keys" -H "Authorization: ${AUTH_HEADER}" \
  | jq -r --arg app "jellyfin-watch" '[.Items[] | select(.AppName == $app) | .AccessToken] | last // empty')"
if [[ -n "$EXISTING" ]]; then
  printf '%s' "$EXISTING"
  exit 0
fi
curl -fsS -X POST "${JELLYFIN_URL}/Auth/Keys?app=jellyfin-watch" \
  -H "Authorization: ${AUTH_HEADER}" -o /dev/null
curl -fsS "${JELLYFIN_URL}/Auth/Keys" -H "Authorization: ${AUTH_HEADER}" \
  | jq -r --arg app "jellyfin-watch" '[.Items[] | select(.AppName == $app) | .AccessToken] | last'
EOF
)"

if [[ -z "$KEY" ]]; then
  echo "ERROR: could not mint the jellyfin-watch API key." >&2
  exit 1
fi

printf 'JELLYFIN_API_KEY=%s\n' "$KEY" | $AGENIX -e jellyfin-watch-env.age

echo
echo "Wrote secrets/jellyfin-watch-env.age"
echo
echo "Redeploy the storage Pi so it reads the new key, then check jellyfin-watch.service on it."
