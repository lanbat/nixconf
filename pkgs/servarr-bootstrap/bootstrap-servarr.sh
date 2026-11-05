#!/usr/bin/env bash
# Configure a Servarr app (Radarr or Sonarr) after its first start: root
# folders, the qBittorrent download client (with the container/host path
# mappings that let imports find its files), and Jackett's configured trackers
# as Torznab indexers. Idempotent: an item already configured is left alone,
# so this runs at every unlock. It reads the app's API key from its config.xml
# (written on first start).
#
# Radarr and Sonarr share this one script; the few differences (port, root
# folders, the qBittorrent category field, the indexer categories) are env.
set -euo pipefail

SERVARR_URL="${SERVARR_URL:?SERVARR_URL is required}" # http://127.0.0.1:<port>
CONFIG_XML="${CONFIG_XML:?CONFIG_XML is required}"    # .../config.xml
APP="${APP:?APP is required}"                         # radarr | sonarr
ROOT_FOLDERS="${ROOT_FOLDERS:-}"                      # space-separated paths
JACKETT_CONFIG="${JACKETT_CONFIG:-}"                  # empty = no Jackett, skip indexers
JACKETT_URL="${JACKETT_URL:-}"                        # empty = no Jackett, skip indexers
QBT_HOST="${QBT_HOST:-127.0.0.1}"
QBT_PORT="${QBT_PORT:-8090}"

log() { echo "${APP}-bootstrap: $*"; }

# The qBittorrent category field differs by app.
if [[ "$APP" == "radarr" ]]; then
  CATEGORY_FIELD="movieCategory"
  CATEGORY="radarr"
elif [[ "$APP" == "sonarr" ]]; then
  CATEGORY_FIELD="tvCategory"
  CATEGORY="sonarr"
else
  log "unknown APP ${APP}"
  exit 1
fi

# The API key is generated and written to config.xml on first start.
wait_for_api_key() {
  local attempt
  for attempt in $(seq 1 60); do
    API_KEY="$(sed -n 's:.*<ApiKey>\([^<]*\)</ApiKey>.*:\1:p' "$CONFIG_XML" 2>/dev/null | head -1 || true)"
    if [[ -n "$API_KEY" ]] && api "${SERVARR_URL}/api/v3/system/status" >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  log "API key or API did not become ready"
  return 1
}

api() { curl -fsS -H "X-Api-Key: ${API_KEY}" "$@"; }

# The default quality profile exists on a fresh install (id 1); read it so a
# root folder created after the user changed profiles still lands on a valid
# one. Metadata profiles were removed from Radarr/Sonarr, so only quality.
quality_profile_id() { api "${SERVARR_URL}/api/v3/qualityprofile" | jq -r '.[0].id // 1'; }

add_root_folder() {
  local path="$1"
  if api "${SERVARR_URL}/api/v3/rootfolder" | jq -e --arg p "$path" '.[] | select(.path == $p)' >/dev/null 2>&1; then
    log "root folder ${path} already present"
    return 0
  fi
  local qp
  qp="$(quality_profile_id)"
  log "adding root folder ${path}"
  api -X POST "${SERVARR_URL}/api/v3/rootfolder" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg p "$path" --argjson qp "$qp" '
      {
        path: $p,
        defaultQualityProfileId: $qp,
        defaultMonitorOption: "all",
        defaultNewItemMonitorOption: "all",
        defaultTags: []
      }')" \
    -o /dev/null
}

add_download_client() {
  if api "${SERVARR_URL}/api/v3/downloadclient" | jq -e '.[] | select(.implementation == "QBittorrent")' >/dev/null 2>&1; then
    log "qBittorrent download client already present"
    return 0
  fi
  log "adding qBittorrent download client (${QBT_HOST}:${QBT_PORT})"
  api -X POST "${SERVARR_URL}/api/v3/downloadclient" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg host "$QBT_HOST" --argjson port "$QBT_PORT" --arg field "$CATEGORY_FIELD" --arg category "$CATEGORY" '
      {
        enable: true,
        protocol: "torrent",
        priority: 1,
        name: "qBittorrent",
        implementation: "QBittorrent",
        implementationName: "QBittorrent",
        configContract: "QBittorrentSettings",
        fields: [
          { name: "host", value: $host },
          { name: "port", value: $port },
          { name: $field, value: $category }
        ]
      }')" \
    -o /dev/null
}

# qBittorrent is a container that names the media drives /media/a and /media/b,
# while Radarr/Sonarr run on the host and see them at /srv/storage/<drive>/media.
# Map the two namespaces so imports find the files qBittorrent downloaded.
add_remote_path_mappings() {
  local host="127.0.0.1" remote_path local_path
  for pair in "/media/a:/srv/storage/a/media" "/media/b:/srv/storage/b/media"; do
    remote_path="${pair%%:*}"
    local_path="${pair#*:}"
    if api "${SERVARR_URL}/api/v3/remotepathmapping" | jq -e --arg r "$remote_path" --arg l "$local_path" '.[] | select(.remotePath == $r and .localPath == $l)' >/dev/null 2>&1; then
      log "remote path mapping ${remote_path} -> ${local_path} already present"
      continue
    fi
    log "adding remote path mapping ${remote_path} -> ${local_path}"
    api -X POST "${SERVARR_URL}/api/v3/remotepathmapping" \
      -H 'Content-Type: application/json' \
      -d "$(jq -n --arg h "$host" --arg r "$remote_path" --arg l "$local_path" '{ host: $h, remotePath: $r, localPath: $l }')" \
      -o /dev/null
  done
}

add_indexer() {
  if [[ -z "$JACKETT_CONFIG" || -z "$JACKETT_URL" ]]; then
    log "no Jackett configured; skipping indexers"
    return 0
  fi

  local key
  key="$(jq -r '.APIKey // empty' "$JACKETT_CONFIG" 2>/dev/null || true)"
  if [[ -z "$key" || "$key" == "YOUR_API_KEY_HERE" ]]; then
    log "Jackett has not generated an API key yet; skipping indexers"
    return 0
  fi

  local cats_json
  if [[ "$APP" == "radarr" ]]; then
    cats_json='[2000,2010,2020,2030,2040,2045,2050,2060]'
  else
    cats_json='[5000,5010,5020,5030,5040,5045,5050,5060,5070,5080]'
  fi

  # One Torznab indexer per Jackett tracker. Modern Radarr/Sonarr reject
  # Jackett's /all aggregate endpoint, so each tracker is added at its own
  # torznab URL. /all/results lists the configured trackers in its "Indexers"
  # array (ID, Name); an unconfigured tracker is not searched, so not listed.
  local trackers id name
  trackers="$(curl -fsS "${JACKETT_URL}/api/v2.0/indexers/all/results?apikey=${key}" 2>/dev/null || true)"
  if [[ -z "$trackers" ]]; then
    log "could not read Jackett's indexers; skipping"
    return 0
  fi

  while IFS=$'\t' read -r id name; do
    if api "${SERVARR_URL}/api/v3/indexer" | jq -e --arg n "$name" '.[] | select(.name == $n)' >/dev/null 2>&1; then
      log "indexer ${name} already present"
      continue
    fi
    log "adding Jackett indexer ${name}"
    api -X POST "${SERVARR_URL}/api/v3/indexer" \
      -H 'Content-Type: application/json' \
      -d "$(jq -n \
        --arg name "$name" \
        --arg baseUrl "${JACKETT_URL}/api/v2.0/indexers/${id}/results/torznab/" \
        --arg key "$key" \
        --argjson cats "$cats_json" '
        {
          enableRss: true,
          enableAutomaticSearch: true,
          enableInteractiveSearch: true,
          priority: 25,
          implementation: "Torznab",
          configContract: "TorznabSettings",
          name: $name,
          fields: [
            { name: "baseUrl", value: $baseUrl },
            { name: "apiKey", value: $key },
            { name: "categories", value: $cats }
          ]
        }')" \
      -o /dev/null
  done < <(jq -r '.Indexers[] | [.ID, .Name] | @tsv' <<< "$trackers")
}

wait_for_api_key

for folder in $ROOT_FOLDERS; do
  add_root_folder "$folder"
done
add_download_client
add_remote_path_mappings
add_indexer
log "done"
