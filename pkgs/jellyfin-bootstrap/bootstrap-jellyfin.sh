#!/usr/bin/env bash
# Complete Jellyfin first-run setup: wizard, media libraries, plugins, and SSO.
set -euo pipefail

JELLYFIN_URL="${JELLYFIN_URL:-http://127.0.0.1:8096}"
EXTERNAL_URL="${EXTERNAL_URL:?EXTERNAL_URL is required}"
AUTH_DOMAIN="${AUTH_DOMAIN:?AUTH_DOMAIN is required}"
OWNER_USERNAME="${OWNER_USERNAME:?OWNER_USERNAME is required}"
OWNER_PASSWORD="${OWNER_PASSWORD:?OWNER_PASSWORD is required}"
AUTHENTIK_JELLYFIN_CLIENT_SECRET="${AUTHENTIK_JELLYFIN_CLIENT_SECRET:?AUTHENTIK_JELLYFIN_CLIENT_SECRET is required}"
# Music video metadata from IMVDb (optional; empty leaves the plugin out).
IMVDB_API_KEY="${IMVDB_API_KEY:-}"
# Open Subtitles account (optional; empty skips credential configuration).
OPENSUBTITLES_USERNAME="${OPENSUBTITLES_USERNAME:-}"
OPENSUBTITLES_PASSWORD="${OPENSUBTITLES_PASSWORD:-}"
# DLNA: 1 installs the DLNA plugin and lets Jellyfin play to DLNA speakers and
# screens (the Echo Shows); 0 turns that off again (the plugin stays installed).
# The plugin's media-server side has no switch: who can reach it is the
# firewall's to say (services/jellyfin.nix).
DLNA_ENABLE="${DLNA_ENABLE:-0}"
STATE_DIR="${STATE_DIR:-/var/lib/jellyfin}"
WIZARD_STATE_FILE="${WIZARD_STATE_FILE:-${STATE_DIR}/.lanbat-bootstrap-complete}"
CONFIG_STATE_FILE="${CONFIG_STATE_FILE:-${STATE_DIR}/.lanbat-jellyfin-config-complete}"

EMBY_CLIENT='MediaBrowser Client="lanbat-bootstrap", Device="server", DeviceId="lanbat", Version="10.11.7"'
API_KEY_APP="lanbat-bootstrap"

log() {
  echo "jellyfin-bootstrap: $*"
}

# /health answers 200 "Degraded" while the server is still starting, and the
# API answers 503 until then, so wait for "Healthy".
wait_for_jellyfin() {
  local attempt
  for attempt in $(seq 1 60); do
    if [[ "$(curl -fsS "${JELLYFIN_URL}/health" 2>/dev/null)" == "Healthy" ]]; then
      return 0
    fi
    sleep 5
  done
  log "Jellyfin did not become ready"
  return 1
}

# The public endpoint: /System/Info needs a login, so without one the wizard
# always looked incomplete, and the startup calls then failed with 401 on a
# set-up server.
wizard_complete() {
  curl -fsS "${JELLYFIN_URL}/System/Info/Public" | jq -e '.StartupWizardCompleted == true' >/dev/null
}

api_auth() {
  local token
  token="$(curl -fsS -X POST "${JELLYFIN_URL}/Users/AuthenticateByName" \
    -H "Content-Type: application/json" \
    -H "X-Emby-Authorization: ${EMBY_CLIENT}" \
    -d "$(jq -n \
      --arg u "$OWNER_USERNAME" \
      --arg p "$OWNER_PASSWORD" \
      '{Username: $u, Pw: $p}')" \
    | jq -r .AccessToken)"
  AUTH_HEADER="${EMBY_CLIENT}, Token=\"${token}\""
}

api_call() {
  curl -fsS "$@" -H "X-Emby-Authorization: ${AUTH_HEADER}"
}

ensure_api_key() {
  local existing
  existing="$(api_call "${JELLYFIN_URL}/Auth/Keys" | jq -r --arg app "$API_KEY_APP" '
    [.Items[] | select(.AppName == $app) | .AccessToken] | last // empty
  ')"
  if [[ -n "$existing" ]]; then
    API_KEY="$existing"
    return 0
  fi
  api_call -X POST "${JELLYFIN_URL}/Auth/Keys?app=${API_KEY_APP}" -o /dev/null
  API_KEY="$(api_call "${JELLYFIN_URL}/Auth/Keys" | jq -r --arg app "$API_KEY_APP" '
    [.Items[] | select(.AppName == $app) | .AccessToken] | last
  ')"
}

run_startup_wizard() {
  log "setting locale and metadata language"
  curl -fsS -X POST "${JELLYFIN_URL}/Startup/Configuration" \
    -H 'Content-Type: application/json' \
    -d '{"UICulture":"en-US","MetadataCountryCode":"US","PreferredMetadataLanguage":"en"}' \
    >/dev/null

  log "initializing admin user record"
  curl -fsS "${JELLYFIN_URL}/Startup/User" >/dev/null

  log "creating admin user ${OWNER_USERNAME}"
  curl -fsS -X POST "${JELLYFIN_URL}/Startup/User" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n \
      --arg name "$OWNER_USERNAME" \
      --arg password "$OWNER_PASSWORD" \
      '{Name: $name, Password: $password}')" \
    >/dev/null

  log "disabling UPnP port mapping (Caddy handles exposure)"
  curl -fsS -X POST "${JELLYFIN_URL}/Startup/RemoteAccess" \
    -H 'Content-Type: application/json' \
    -d '{"EnableRemoteAccess":true,"EnableAutomaticPortMapping":false}' \
    >/dev/null

  log "completing startup wizard"
  curl -fsS -X POST "${JELLYFIN_URL}/Startup/Complete" >/dev/null
}

library_exists() {
  local name="$1"
  api_call "${JELLYFIN_URL}/Library/VirtualFolders" | jq -e --arg name "$name" '.[] | select(.Name == $name)' >/dev/null
}

library_options_json() {
  local path="$1"
  jq -n --arg path "$path" '{
    LibraryOptions: {
      PathInfos: [{Path: $path}],
      EnableRealtimeMonitor: false
    }
  }'
}

add_library() {
  local collection_type="$1"
  local name="$2"
  local path="$3"

  if [[ ! -d "$path" ]]; then
    log "skipping library ${name} (${path} not mounted yet)"
    return 0
  fi
  if library_exists "$name"; then
    log "library ${name} already exists"
    return 0
  fi

  log "adding library ${name} -> ${path}"
  api_call -X POST \
    "${JELLYFIN_URL}/Library/VirtualFolders?collectionType=${collection_type}&name=$(jq -rn --arg v "$name" '$v|@uri')&refreshLibrary=true" \
    -H 'Content-Type: application/json' \
    -d "$(library_options_json "$path")" \
    -o /dev/null
}

# All media folders on Pi storage except adult (Samba-only) and incomplete
# (in-progress downloads). roms/ is served by RomM, not Jellyfin.
#
# A TV library's root must hold one folder per show: Jellyfin takes every
# folder there for a series. A qBittorrent category that saves into a single
# folder (a "TV Shows" category saving to tv/misc, say) makes the whole library
# one series, named after whatever TMDb matches "misc" to.
# Format: collectionType|displayName|path
LIBRARY_SPECS=(
  "movies|Movies|/srv/storage/a/media/movies"
  "tvshows|TV Shows|/srv/storage/a/media/tv"
  "musicvideos|Music Videos|/srv/storage/a/media/music-videos"
  "music|Music|/srv/storage/b/media/music"
  "movies|Documentaries|/srv/storage/b/media/documentaries"
  # Documentaries in episodes, laid out as <show>/Season 01/<files>: in the
  # movie library above they would each be matched as a film.
  "tvshows|Documentary Series|/srv/storage/b/media/documentary-series"
  "books|Audiobooks|/srv/storage/b/media/audiobooks"
  "books|Books|/srv/storage/b/media/books"
  "tvshows|Gym|/srv/storage/b/media/gym"
  "mixed|Games|/srv/storage/b/media/games"
  "mixed|Misc|/srv/storage/b/media/misc"
)

libraries_complete() {
  local spec _collection name path
  for spec in "${LIBRARY_SPECS[@]}"; do
    IFS='|' read -r _collection name path <<< "$spec"
    [[ -d "$path" ]] || continue
    library_exists "$name" || return 1
  done
}

setup_libraries() {
  local spec collection name path
  for spec in "${LIBRARY_SPECS[@]}"; do
    IFS='|' read -r collection name path <<< "$spec"
    add_library "$collection" "$name" "$path"
  done
}

refresh_all_libraries() {
  log "triggering full library scan"
  api_call -X POST "${JELLYFIN_URL}/Library/Refresh" -o /dev/null || \
    log "library refresh request failed (may already be scanning)"
}

# Real-time library monitoring uses inotify, which does not work on NFS mounts.
# Pi storage is written by qBittorrent on the server; Jellyfin never sees those
# events. Disable realtime monitoring and rely on scheduled + bootstrap scans.
configure_nfs_libraries() {
  local folders updated=false
  folders="$(api_call "${JELLYFIN_URL}/Library/VirtualFolders")"

  while IFS= read -r library; do
    [[ -n "$library" ]] || continue
    local id name options
    id="$(jq -r '.ItemId // empty' <<< "$library")"
    name="$(jq -r '.Name // empty' <<< "$library")"
    [[ -n "$id" && -n "$name" ]] || continue

    if [[ "$(jq -r '.LibraryOptions.EnableRealtimeMonitor // true' <<< "$library")" == "false" ]]; then
      continue
    fi

    options="$(jq '.LibraryOptions | .EnableRealtimeMonitor = false' <<< "$library")"
    log "disabling realtime monitor on ${name} (NFS mount)"
    api_call -X POST "${JELLYFIN_URL}/Library/VirtualFolders/LibraryOptions" \
      -H 'Content-Type: application/json' \
      -d "$(jq -n --arg id "$id" --argjson opts "$options" '{Id: $id, LibraryOptions: $opts}')" \
      -o /dev/null
    updated=true
  done < <(jq -c '.[] | select(.LibraryOptions != null)' <<< "$folders")

  if [[ "$updated" == true ]]; then
    log "realtime monitoring disabled on NFS libraries"
  fi
}

# Default Jellyfin scan interval is 12 hours; shorten it because NFS has no
# realtime monitoring. 2 hours = 72_000_000_000 .NET ticks.
configure_scheduled_scan() {
  local task_id interval_ticks=72000000000
  task_id="$(api_call "${JELLYFIN_URL}/ScheduledTasks" | jq -r '
    .[] | select(.Key == "RefreshLibrary") | .Id
  ')"
  if [[ -z "$task_id" ]]; then
    log "RefreshLibrary scheduled task not found"
    return 0
  fi

  local current_interval
  current_interval="$(api_call "${JELLYFIN_URL}/ScheduledTasks" | jq -r --arg id "$task_id" '
    .[] | select(.Id == $id) | .Triggers[0].IntervalTicks // empty
  ')"
  if [[ "$current_interval" == "$interval_ticks" ]]; then
    return 0
  fi

  log "configuring library scan every 2 hours (NFS has no realtime monitoring)"
  api_call -X POST "${JELLYFIN_URL}/ScheduledTasks/${task_id}/Triggers" \
    -H 'Content-Type: application/json' \
    -d "[{\"Type\":\"IntervalTrigger\",\"IntervalTicks\":${interval_ticks}}]" \
    -o /dev/null
}

uri_encode() {
  jq -rn --arg v "$1" '$v|@uri'
}

plugin_installed() {
  local name="$1"
  # Use a glob test instead of compgen — compgen is not available in the
  # NixOS systemd oneshot environment and caused false negatives that
  # reinstalled plugins and restarted Jellyfin in a loop.
  local plugin_dir
  for plugin_dir in "${STATE_DIR}/plugins/${name}_"*; do
    if [[ -d "$plugin_dir" ]]; then
      return 0
    fi
  done
  api_call "${JELLYFIN_URL}/Packages/Installed/$(uri_encode "$name")" >/dev/null 2>&1
}

install_plugin() {
  local name="$1"
  local guid="${2:-}"
  local query=""
  if [[ -n "$guid" ]]; then
    query="?assemblyGuid=${guid}"
  fi
  if plugin_installed "$name"; then
    log "plugin ${name} already installed"
    return 1
  fi
  log "installing plugin ${name}"
  # Callers test the status for "newly installed", which would take a failed
  # request for "already installed", so a failure ends the run here.
  if ! api_call -X POST "${JELLYFIN_URL}/Packages/Installed/$(uri_encode "$name")${query}" -o /dev/null; then
    log "installing plugin ${name} failed"
    exit 1
  fi
  return 0
}

setup_plugin_repositories() {
  local version sso_repo sso_name
  version="$(curl -fsS "${JELLYFIN_URL}/System/Info/Public" | jq -r .Version)"
  if [[ "$version" == 12.* ]]; then
    sso_repo="https://raw.githubusercontent.com/Flowfin/jellyfin-plugin-sso/manifest-beta/manifest.json"
    sso_name="Flowfin SSO"
  else
    sso_repo="https://raw.githubusercontent.com/9p4/jellyfin-plugin-sso/manifest-release/manifest.json"
    sso_name="SSO Auth"
  fi

  api_call -X POST "${JELLYFIN_URL}/Repositories" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg sso_repo "$sso_repo" --arg sso_name "$sso_name" '[
      {
        Name: "Jellyfin Stable",
        Url: "https://repo.jellyfin.org/files/plugin/manifest.json",
        Enabled: true
      },
      {
        Name: $sso_name,
        Url: $sso_repo,
        Enabled: true
      }
    ]')" \
    -o /dev/null
}

# Plugin GUIDs as the repository manifest lists them. A loaded plugin's id
# under /Plugins can differ (IMVDb's does), so it is looked up by name.
BOOKSHELF_GUID="9c4e63f1-031b-4f25-988b-4f7d78a8b53e"
IMVDB_GUID="e29621a5-fa9e-4330-982e-ef6e54c0cad2"
DLNA_GUID="33eba9cd-7da1-4720-967f-dd7dae7b74a1"

# Libraries whose items need their metadata fetched again once the plugins
# are loaded: a newly installed provider only reaches items added after it.
REFRESH_LIBRARIES=()

setup_plugins() {
  local restarted=false
  setup_plugin_repositories

  install_plugin "Open Subtitles" "4b9ed42f518548b598036ff2989014c4" && restarted=true
  install_plugin "Trakt" "4fe3201ed6ae4f2e8917e12bda571281" && restarted=true

  # Jellyfin itself fetches metadata only for movies, TV and music. Bookshelf
  # adds Google Books and Comic Vine for books, and IMVDb adds music videos.
  if install_plugin "Bookshelf" "$BOOKSHELF_GUID"; then
    restarted=true
    REFRESH_LIBRARIES+=("Books")
  fi
  if [[ -n "$IMVDB_API_KEY" ]] && install_plugin "IMVDb" "$IMVDB_GUID"; then
    restarted=true
  fi
  # Jellyfin 10.9 moved DLNA out of the server into this plugin. The newest
  # build the running Jellyfin accepts is the one installed.
  if [[ "$DLNA_ENABLE" == 1 ]] && install_plugin "DLNA" "$DLNA_GUID"; then
    restarted=true
  fi

  local version
  version="$(curl -fsS "${JELLYFIN_URL}/System/Info/Public" | jq -r .Version)"
  if [[ "$version" == 12.* ]]; then
    install_plugin "Community SSO for Jellyfin" "505ce9d1-d916-42fa-86ca-673ef241d7df" && restarted=true
  else
    install_plugin "SSO Authentication" "505ce9d1-d916-42fa-86ca-673ef241d7df" && restarted=true
  fi

  if [[ "$restarted" == true ]]; then
    log "restarting Jellyfin to load new plugins"
    systemctl restart jellyfin.service
    wait_for_jellyfin
    local attempt
    for attempt in $(seq 1 12); do
      if api_auth 2>/dev/null; then
        return 0
      fi
      sleep 5
    done
    log "Jellyfin API did not accept authentication after plugin restart"
    return 1
  fi
}

# The DLNA plugin's one switch that matters here, EnablePlayTo: Jellyfin finds
# DLNA speakers and screens and plays to them. (Its settings are this, how often
# it announces itself and looks for devices, and which user DLNA clients see;
# read from the plugin's own configuration page, DLNA 10.0.0.0.) A configuration
# without the key is left alone. $play is a JSON boolean.
DLNA_JQ='
  (if has("EnablePlayTo") then .EnablePlayTo = $play else . end)'

# Sets it from DLNA_ENABLE once the plugin is loaded.
configure_dlna() {
  local id current desired play=false
  id="$(api_call "${JELLYFIN_URL}/Plugins" | jq -r '[.[] | select(.Name == "DLNA") | .Id][0] // empty')"
  [[ -n "$id" ]] || return 0
  [[ "$DLNA_ENABLE" == 1 ]] && play=true
  current="$(api_call "${JELLYFIN_URL}/Plugins/${id}/Configuration")"
  if ! jq -e 'has("EnablePlayTo")' <<<"$current" >/dev/null; then
    log "the DLNA plugin's configuration has no EnablePlayTo; leaving it as it is"
    return 0
  fi
  desired="$(jq --argjson play "$play" "$DLNA_JQ" <<<"$current")"
  if [[ "$(jq -S . <<<"$desired")" == "$(jq -S . <<<"$current")" ]]; then
    return 0
  fi
  log "DLNA: play to devices ${play}"
  api_call -X POST "${JELLYFIN_URL}/Plugins/${id}/Configuration" \
    -H 'Content-Type: application/json' \
    -d "$desired" \
    -o /dev/null
}

# IMVDb answers nothing without its API key. Setting or changing the key
# fetches the Music Videos library's metadata again.
configure_imvdb() {
  [[ -n "$IMVDB_API_KEY" ]] || return 0
  local id current
  id="$(api_call "${JELLYFIN_URL}/Plugins" | jq -r '[.[] | select(.Name == "IMVDb") | .Id][0] // empty')"
  if [[ -z "$id" ]]; then
    log "IMVDb plugin not loaded yet"
    return 1
  fi
  current="$(api_call "${JELLYFIN_URL}/Plugins/${id}/Configuration")"
  if [[ "$(jq -r '.ApiKey // empty' <<<"$current")" == "$IMVDB_API_KEY" ]]; then
    return 0
  fi
  log "setting the IMVDb API key"
  api_call -X POST "${JELLYFIN_URL}/Plugins/${id}/Configuration" \
    -H 'Content-Type: application/json' \
    -d "$(jq --arg key "$IMVDB_API_KEY" '.ApiKey = $key' <<<"$current")" \
    -o /dev/null
  REFRESH_LIBRARIES+=("Music Videos")
}

# Set or update the Open Subtitles plugin credentials so Jellyfin can download
# subtitles automatically (the plugin is always installed; this step is skipped
# when OPENSUBTITLES_USERNAME is not set).
configure_opensubtitles() {
  [[ -n "$OPENSUBTITLES_USERNAME" ]] || return 0
  local id current
  id="$(api_call "${JELLYFIN_URL}/Plugins" | jq -r '[.[] | select(.Name == "Open Subtitles") | .Id][0] // empty')"
  if [[ -z "$id" ]]; then
    log "Open Subtitles plugin not loaded yet"
    return 1
  fi
  current="$(api_call "${JELLYFIN_URL}/Plugins/${id}/Configuration")"
  if [[ "$(jq -r '.Username // empty' <<<"$current")" == "$OPENSUBTITLES_USERNAME" &&
        "$(jq -r '.Password // empty' <<<"$current")" == "$OPENSUBTITLES_PASSWORD" ]]; then
    return 0
  fi
  log "setting Open Subtitles credentials for ${OPENSUBTITLES_USERNAME}"
  api_call -X POST "${JELLYFIN_URL}/Plugins/${id}/Configuration" \
    -H 'Content-Type: application/json' \
    -d "$(jq \
      --arg u "$OPENSUBTITLES_USERNAME" \
      --arg p "$OPENSUBTITLES_PASSWORD" \
      '.Username = $u | .Password = $p' \
      <<<"$current")" \
    -o /dev/null
}

# The IMVDb plugin also registers an artist provider that is on by default in
# every library with music artists. In the Music library it asks IMVDb about
# each of thousands of artists on every scan, which has no use there (it only
# knows music videos) and fills the log with errors whenever IMVDb refuses a
# request. List the Music library's artist providers explicitly, without IMVDb.
exclude_imvdb_from_music_artists() {
  [[ -n "$IMVDB_API_KEY" ]] || return 0
  local lib id avail opts
  lib="$(api_call "${JELLYFIN_URL}/Library/VirtualFolders" | jq -c '.[] | select(.Name == "Music")')"
  [[ -n "$lib" ]] || return 0
  id="$(jq -r .ItemId <<<"$lib")"
  avail="$(api_call "${JELLYFIN_URL}/Libraries/AvailableOptions?libraryContentType=music&isNewLibrary=false" \
    | jq -c '.TypeOptions[] | select(.Type == "MusicArtist")')"
  [[ -n "$avail" ]] || return 0
  opts="$(jq -c --argjson avail "$avail" '
    ($avail.MetadataFetchers | map(select(.DefaultEnabled and .Name != "IMVDb") | .Name)) as $fetchers
    | .LibraryOptions
    | .TypeOptions = ((.TypeOptions // []) | map(select(.Type != "MusicArtist")) + [{
        Type: "MusicArtist",
        MetadataFetchers: $fetchers,
        MetadataFetcherOrder: $fetchers,
        ImageFetchers: [],
        ImageFetcherOrder: [],
        ImageOptions: []
      }])' <<<"$lib")"
  if [[ "$(jq -c '.TypeOptions' <<<"$opts")" == "$(jq -c '.LibraryOptions.TypeOptions' <<<"$lib")" ]]; then
    return 0
  fi
  log "excluding IMVDb from the Music library's artist providers"
  api_call -X POST "${JELLYFIN_URL}/Library/VirtualFolders/LibraryOptions" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n --arg id "$id" --argjson opts "$opts" '{Id: $id, LibraryOptions: $opts}')" \
    -o /dev/null
}

# Fetch missing metadata and images for a whole library, as its "Refresh
# metadata" menu does, keeping what items already have.
refresh_library_metadata() {
  local name="$1" id
  id="$(api_call "${JELLYFIN_URL}/Library/VirtualFolders" | jq -r --arg name "$name" '
    .[] | select(.Name == $name) | .ItemId
  ')"
  [[ -n "$id" ]] || return 0
  log "fetching ${name} metadata from the new providers"
  api_call -X POST \
    "${JELLYFIN_URL}/Items/${id}/Refresh?metadataRefreshMode=FullRefresh&imageRefreshMode=FullRefresh&replaceAllMetadata=false&replaceAllImages=false" \
    -o /dev/null
}

refresh_new_provider_libraries() {
  local name
  for name in "${REFRESH_LIBRARIES[@]}"; do
    refresh_library_metadata "$name"
  done
}

sso_configured() {
  ensure_api_key
  curl -fsS "${JELLYFIN_URL}/sso/OID/Get?api_key=${API_KEY}" \
    | jq -e '.authentik.Enabled == true' >/dev/null
}

configure_networking() {
  local current desired
  current="$(api_call "${JELLYFIN_URL}/System/Configuration/network")"
  desired="$(jq \
    --arg url "$EXTERNAL_URL" \
    '.AutoDiscovery = true
     | .KnownProxies = ((.KnownProxies // []) + ["127.0.0.1"] | unique)
     | .PublishedServerUriBySubnet = ["all=" + $url]' \
    <<<"$current")"
  if [[ "$desired" == "$current" ]]; then
    return 0
  fi
  log "configuring networking (auto-discovery, known proxies, published URL)"
  api_call -X POST "${JELLYFIN_URL}/System/Configuration/network" \
    -H 'Content-Type: application/json' \
    -d "$desired" \
    -o /dev/null
}

configuration_complete() {
  api_auth
  libraries_complete \
    && plugin_installed "Open Subtitles" \
    && sso_configured
}

setup_sso() {
  if sso_configured; then
    log "Authentik SSO provider already configured"
    return 0
  fi

  ensure_api_key
  log "configuring Authentik OIDC provider"
  curl -fsS -X POST "${JELLYFIN_URL}/sso/OID/Add/authentik?api_key=${API_KEY}" \
    -H 'Content-Type: application/json' \
    -d "$(jq -n \
      --arg endpoint "https://auth.${AUTH_DOMAIN}/application/o/jellyfin/" \
      --arg clientId "jellyfin" \
      --arg secret "$AUTHENTIK_JELLYFIN_CLIENT_SECRET" \
      '{
        oidEndpoint: $endpoint,
        oidClientId: $clientId,
        oidSecret: $secret,
        enabled: true,
        enableAuthorization: true,
        enableAllFolders: true,
        oidScopes: ["openid", "profile", "email"]
      }')" \
    -o /dev/null
}

# The login page's "Sign in with Authentik" button. Branding is written through
# /System/Configuration/branding (admin); /Branding/Configuration is read-only.
setup_branding() {
  local disclaimer current desired
  disclaimer="$(cat <<EOF
<form action="${EXTERNAL_URL}/sso/OID/start/authentik">
  <button class="raised block emby-button button-submit" type="submit">
    Sign in with Authentik
  </button>
</form>
EOF
)"
  current="$(api_call "${JELLYFIN_URL}/System/Configuration/branding")"
  desired="$(jq \
    --arg disclaimer "$disclaimer" \
    '.LoginDisclaimer = $disclaimer
     | .CustomCss = "a.raised.emby-button { padding: 0.9em 1em; color: inherit !important; } .disclaimerContainer { display: block; }"' \
    <<<"$current")"
  if [[ "$desired" == "$current" ]]; then
    return 0
  fi
  log "configuring login page (Sign in with Authentik)"
  api_call -X POST "${JELLYFIN_URL}/System/Configuration/branding" \
    -H 'Content-Type: application/json' \
    -d "$desired" \
    -o /dev/null
}

harden_adult_permissions() {
  local adult="/srv/storage/b/media/adult"
  if [[ ! -d "$adult" ]]; then
    return 0
  fi
  log "restricting ${adult} to the private group"
  chgrp private "$adult" 2>/dev/null || true
  chmod 2770 "$adult" 2>/dev/null || true
}

repair_media_permissions() {
  local path
  local media_paths=(
    /srv/storage/a/media
    /srv/storage/a/media/movies
    /srv/storage/a/media/tv
    /srv/storage/a/media/music-videos
    /srv/storage/b/media
    /srv/storage/b/media/music
    /srv/storage/b/media/documentaries
    /srv/storage/b/media/documentary-series
    /srv/storage/b/media/roms
    /srv/storage/b/media/audiobooks
    /srv/storage/b/media/books
    /srv/storage/b/media/gym
    /srv/storage/b/media/games
    /srv/storage/b/media/misc
    /srv/storage/b/media/incomplete
  )

  for path in "${media_paths[@]}"; do
    [[ -d "$path" ]] || continue
    if [[ "$(stat -c '%U:%G' "$path")" != "qbt:media" ]] || [[ "$(stat -c '%a' "$path")" != "2775" ]]; then
      log "repairing ${path} ownership and mode (expected qbt:media 2775)"
      chown qbt:media "$path" 2>/dev/null || true
      chmod 2775 "$path" 2>/dev/null || true
    fi

    # Jellyfin runs as the media group. qBittorrent should inherit that via
    # setgid directories, but older downloads may block traversal or reads.
    local fixed_dirs fixed_files wrong_group
    fixed_dirs="$(find "$path" -type d \( ! -perm -2000 -o ! -perm -g+x \) -print 2>/dev/null | wc -l)"
    fixed_files="$(find "$path" -type f ! -perm -g+r -print 2>/dev/null | wc -l)"
    wrong_group="$(find "$path" \( -type f -o -type d \) -user qbt ! -group media -print 2>/dev/null | wc -l)"
    if [[ "$fixed_dirs" -gt 0 || "$fixed_files" -gt 0 || "$wrong_group" -gt 0 ]]; then
      log "repairing ${path} permissions (${fixed_dirs} dirs, ${fixed_files} files unreadable by media group, ${wrong_group} wrong group)"
      find "$path" -type d -user qbt ! -group media -exec chown qbt:media {} + 2>/dev/null || true
      find "$path" -type f -user qbt ! -group media -exec chgrp media {} + 2>/dev/null || true
      find "$path" -type d \( ! -perm -2000 -o ! -perm -g+x \) -exec chmod 2775 {} + 2>/dev/null || true
      find "$path" -type f ! -perm -g+r -exec chmod g+r {} + 2>/dev/null || true
    fi
  done
}

run_configuration() {
  repair_media_permissions
  harden_adult_permissions
  api_auth
  configure_networking
  setup_libraries
  configure_nfs_libraries
  configure_scheduled_scan
  setup_plugins
  configure_imvdb
  configure_dlna
  exclude_imvdb_from_music_artists
  configure_opensubtitles
  setup_sso
  setup_branding
  api_auth
  refresh_all_libraries
  refresh_new_provider_libraries
}

wait_for_jellyfin

if [[ -f "$CONFIG_STATE_FILE" ]] && wizard_complete && configuration_complete 2>/dev/null; then
  repair_media_permissions
  harden_adult_permissions
  api_auth
  configure_networking
  setup_libraries
  configure_nfs_libraries
  configure_scheduled_scan
  # Plugins added to this script after the server was first set up.
  setup_plugins
  configure_imvdb
  configure_dlna
  exclude_imvdb_from_music_artists
  configure_opensubtitles
  setup_branding
  refresh_all_libraries
  refresh_new_provider_libraries
  log "configuration already complete"
  exit 0
fi

if wizard_complete || [[ -f "$WIZARD_STATE_FILE" ]]; then
  if ! wizard_complete; then
    log "waiting for Jellyfin to finish starting"
    wait_for_jellyfin
  fi
  install -d -m 0750 "$STATE_DIR"
  touch "$WIZARD_STATE_FILE"
else
  run_startup_wizard
  install -d -m 0750 "$STATE_DIR"
  touch "$WIZARD_STATE_FILE"
  log "startup wizard complete"
fi

run_configuration

if configuration_complete; then
  install -d -m 0750 "$STATE_DIR"
  touch "$CONFIG_STATE_FILE"
  log "done"
else
  log "configuration incomplete (waiting for NFS mounts or plugin load)"
  exit 1
fi
