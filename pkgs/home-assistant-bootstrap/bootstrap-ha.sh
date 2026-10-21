#!/usr/bin/env bash
# Complete Home Assistant first-run onboarding and provision SSO users.
set -euo pipefail

INTERNAL_URL="${INTERNAL_URL:-http://127.0.0.1:8123}"
EXTERNAL_URL="${EXTERNAL_URL:?EXTERNAL_URL is required}"
OWNER_USERNAME="${OWNER_USERNAME:?OWNER_USERNAME is required}"
OWNER_PASSWORD="${OWNER_PASSWORD:?OWNER_PASSWORD is required}"
# Space-separated Authentik usernames to mirror in HA (they log in through
# Authentik; auth_oidc links each to the HA user of the same name).
SSO_USERS="${SSO_USERS:-$OWNER_USERNAME}"
STATE_FILE="${STATE_FILE:-/var/lib/hass/.lanbat-onboarding-complete}"

log() {
  echo "home-assistant-bootstrap: $*"
}

wait_for_ha() {
  local attempt
  for attempt in $(seq 1 60); do
    if curl -fsS -o /dev/null "${INTERNAL_URL}/"; then
      return 0
    fi
    sleep 5
  done
  log "Home Assistant did not become ready"
  return 1
}

onboarding_done() {
  if [[ -f "$STATE_FILE" ]]; then
    return 0
  fi
  local status
  status="$(curl -fsS "${INTERNAL_URL}/api/onboarding" 2>/dev/null || true)"
  if [[ -z "$status" || "$status" == "[]" ]]; then
    return 0
  fi
  return 1
}

exchange_auth_code() {
  local auth_code="$1"
  curl -fsS -X POST "${INTERNAL_URL}/auth/token" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "client_id=${EXTERNAL_URL}/" \
    --data-urlencode "grant_type=authorization_code" \
    --data-urlencode "code=${auth_code}" \
    | jq -r .access_token
}

run_onboarding() {
  log "creating owner user ${OWNER_USERNAME}"
  local payload auth_code access_token
  payload="$(jq -n \
    --arg client_id "${EXTERNAL_URL}/" \
    --arg name "$OWNER_USERNAME" \
    --arg username "$OWNER_USERNAME" \
    --arg password "$OWNER_PASSWORD" \
    '{client_id: $client_id, name: $name, username: $username, password: $password, language: "en"}')"

  auth_code="$(curl -fsS -X POST "${INTERNAL_URL}/api/onboarding/users" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    | jq -r .auth_code)"

  access_token="$(exchange_auth_code "$auth_code")"

  log "finishing onboarding steps"
  curl -fsS -X POST "${INTERNAL_URL}/api/onboarding/core_config" \
    -H "Authorization: Bearer ${access_token}" \
    -H "Content-Type: application/json" \
    -d '{}' \
    >/dev/null || true

  curl -fsS -X POST "${INTERNAL_URL}/api/onboarding/integration" \
    -H "Authorization: Bearer ${access_token}" \
    -H "Content-Type: application/json" \
    -d "$(jq -n --arg client_id "${EXTERNAL_URL}/" --arg redirect_uri "${EXTERNAL_URL}/" \
      '{client_id: $client_id, redirect_uri: $redirect_uri}')" \
    >/dev/null || true

  curl -fsS -X POST "${INTERNAL_URL}/api/onboarding/analytics" \
    -H "Authorization: Bearer ${access_token}" \
    -H "Content-Type: application/json" \
    -d '{"analytics": false}' \
    >/dev/null || true
}

ensure_sso_users() {
  local user
  local config_dir="${HASS_CONFIG:-/var/lib/hass}"
  for user in $SSO_USERS; do
    if hass --script auth -c "$config_dir" list 2>/dev/null | grep -q "${user}"; then
      log "user ${user} already exists"
      continue
    fi
    log "creating SSO user ${user}"
    # Random password: login is through Authentik (auth_oidc), not this password.
    hass --script auth -c "$config_dir" add "$user" "$(openssl rand -base64 32)"
  done
}

if onboarding_done; then
  log "onboarding already complete"
else
  wait_for_ha
  run_onboarding
  touch "$STATE_FILE"
  log "onboarding complete"
fi

ensure_sso_users
