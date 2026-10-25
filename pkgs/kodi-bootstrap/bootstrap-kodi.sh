#!/usr/bin/env bash
# Wait for Pi storage, then seed Kodi library sources with content types
# (every boot, before Kodi starts: the sources are upserts, so a folder made
# since, or a source added here, joins the library).
#
# kodi-bootstrap music-scan (after Kodi starts): the first scan of each music
# source. Kodi's startup update only rescans folders it has scanned before, so
# a music source nothing has scanned stays empty.
#
# kodi-bootstrap sources-xml: add a configured video source missing from the
# seeded sources.xml (the Videos > Files list), leaving the rest alone.
set -euo pipefail

KODI_HOME="${KODI_HOME:?KODI_HOME must be set}"
USERDATA="${KODI_HOME}/.kodi/userdata"
STORAGE_STAMP="${STORAGE_STAMP:-${KODI_HOME}/.lanbat-kodi-storage-ready}"
KODI_PORT="${KODI_PORT:-9090}"

# The sources, from the NixOS module (lanbat.services.kodi.settings), one per
# line: name|path|content|scraper|recursive|useFolderNames for video,
# name|path for music.
mapfile -t VIDEO_SOURCES <<< "${KODI_VIDEO_SOURCES:-}"
mapfile -t MUSIC_SOURCES <<< "${KODI_MUSIC_SOURCES:-}"

log() {
  echo "kodi-bootstrap: $*"
}

wait_for_mount() {
  local path="$1"
  local attempt
  for attempt in $(seq 1 60); do
    if mountpoint -q "$path"; then
      return 0
    fi
    sleep 5
  done
  log "${path} is not mounted"
  return 1
}

find_db() {
  local pattern="$1"
  shopt -s nullglob
  local matches=("${USERDATA}/Database"/${pattern})
  shopt -u nullglob
  if ((${#matches[@]} == 0)); then
    return 1
  fi
  # The newest schema: Kodi leaves the old database behind when it upgrades.
  printf '%s\n' "${matches[@]}" | sort -V | tail -n 1
}

ensure_storage() {
  # Check on every boot, not once: the stamp only records that a previous boot
  # saw the drives. If a drive is slow or missing this time, wait for it here so
  # its sources are still configured before Kodi starts. A source whose
  # directory is absent is skipped, not removed, and is picked up on a later
  # boot once the drive is back.
  wait_for_mount /mnt/storage-a || true
  wait_for_mount /mnt/storage-b || true

  if [[ ! -d "$USERDATA" ]]; then
    log "Kodi userdata directory is missing"
    exit 1
  fi

  if [[ ! -f "$STORAGE_STAMP" ]]; then
    install -d -m 0755 -o media -g media "$(dirname "$STORAGE_STAMP")"
    touch "$STORAGE_STAMP"
    chown media:media "$STORAGE_STAMP"
  fi
  log "storage ready"
}

upsert_video_source() {
  local db="$1"
  local path="$2"
  local content="$3"
  local scraper="$4"
  local recursive="$5"
  local folder_names="$6"

  sqlite3 "$db" <<SQL
INSERT INTO path (
  strPath, strContent, strScraper, scanRecursive, useFolderNames, dateAdded
) SELECT
  '${path}', '${content}', '${scraper}', ${recursive}, ${folder_names}, datetime('now')
WHERE NOT EXISTS (SELECT 1 FROM path WHERE strPath = '${path}');

UPDATE path SET
  strContent = '${content}',
  strScraper = '${scraper}',
  scanRecursive = ${recursive},
  useFolderNames = ${folder_names},
  noUpdate = 0,
  exclude = 0
WHERE strPath = '${path}';
SQL
}

configure_video_library() {
  local db
  db="$(find_db 'MyVideos*.db')" || {
    log "waiting for Kodi to create its video library database"
    return 1
  }

  local spec name path content scraper recursive folder_names
  local configured=()
  for spec in "${VIDEO_SOURCES[@]}"; do
    [[ -n "$spec" ]] || continue
    IFS='|' read -r name path content scraper recursive folder_names <<< "$spec"
    configured+=("'${path}'")
    if [[ ! -d "$path" ]]; then
      log "skipping ${name} (${path} not mounted yet)"
      continue
    fi
    upsert_video_source "$db" "$path" "$content" "$scraper" "$recursive" "$folder_names"
    log "configured video source ${name} -> ${path} (${content})"
  done

  # A source no longer configured (moved to another directory, removed) stops
  # being one; Kodi's clean on update then drops what it held.
  if ((${#configured[@]} > 0)); then
    local list
    list="$(IFS=,; echo "${configured[*]}")"
    sqlite3 "$db" "UPDATE path SET strContent = '', strScraper = '' WHERE strContent != '' AND strPath LIKE '/mnt/%' AND strPath NOT IN (${list});"
  fi

  return 0
}

# Video sources are named by their key in the settings ("tv",
# "documentary-series"); the Videos > Files list shows a label ("TV Shows",
# "Documentary Series").
source_label() {
  if [[ "$1" == "tv" ]]; then
    printf 'TV Shows'
    return 0
  fi
  local word out="" words=()
  IFS=' ' read -r -a words <<< "${1//-/ }"
  for word in "${words[@]}"; do
    out+="${word^} "
  done
  printf '%s' "${out% }"
}

# Kodi's library scan reads the paths in its database, but Videos > Files reads
# sources.xml. That file is seeded once, so a source configured since (the
# documentary series, say) never appears there. Add the missing ones and leave
# everything else, including sources added in Kodi's GUI, alone.
configure_video_sources_xml() {
  local xml="${USERDATA}/sources.xml"
  [[ -f "$xml" ]] || return 0

  local spec name path label present
  for spec in "${VIDEO_SOURCES[@]}"; do
    [[ -n "$spec" ]] || continue
    IFS='|' read -r name path _ <<< "$spec"
    [[ -d "$path" ]] || continue
    label="$(source_label "$name")"
    present="$(xmlstarlet sel -t -v "/sources/video/source[path='${path}']/name" "$xml" 2>/dev/null || true)"
    if [[ -n "$present" ]]; then
      # The path is what Kodi browses; keep its display name in step with the
      # settings (an older seed may have called it "documentary-series").
      if [[ "$present" != "$label" ]]; then
        xmlstarlet ed -L -u "/sources/video/source[path='${path}']/name" -v "$label" "$xml"
        log "renamed video source ${present} -> ${label} in sources.xml"
      fi
      continue
    fi
    if xmlstarlet ed -L \
      -s /sources/video -t elem -n source -v "" \
      -s "/sources/video/source[last()]" -t elem -n name -v "$label" \
      -s "/sources/video/source[last()]" -t elem -n path -v "$path" \
      -s "/sources/video/source[last()]" -t elem -n allowsharing -v "true" \
      "$xml"; then
      log "added video source ${label} -> ${path} to sources.xml"
    else
      log "could not add ${label} to sources.xml"
      return 1
    fi
  done

  # xmlstarlet rewrites the file, so give it back to the Kodi account.
  if id media >/dev/null 2>&1; then
    chown media:media "$xml"
  fi
  chmod 0644 "$xml"
  return 0
}

upsert_music_source() {
  local db="$1"
  local name="$2"
  local path="$3"

  sqlite3 "$db" <<SQL
INSERT INTO source (strName, strMultipath)
SELECT '${name}', '${path}'
WHERE NOT EXISTS (
  SELECT 1 FROM source WHERE strMultipath = '${path}'
);
SQL
}

configure_music_library() {
  local db
  db="$(find_db 'MyMusic*.db')" || {
    log "waiting for Kodi to create its music library database"
    return 1
  }

  local spec name path
  for spec in "${MUSIC_SOURCES[@]}"; do
    [[ -n "$spec" ]] || continue
    IFS='|' read -r name path <<< "$spec"
    if [[ ! -d "$path" ]]; then
      log "skipping ${name} (${path} not mounted yet)"
      continue
    fi
    upsert_music_source "$db" "$name" "$path"
    log "configured music source ${name} -> ${path}"
  done

  return 0
}

# One JSON-RPC call on Kodi's TCP port (loopback, no password); prints the
# reply. Kodi keeps the connection open, so the read ends on a timeout.
rpc() {
  local reply
  # Refused until Kodi is up; the caller retries.
  { exec 3<>"/dev/tcp/127.0.0.1/${KODI_PORT}"; } 2>/dev/null || return 1
  printf '%s' "$1" >&3
  reply="$(timeout 2 cat <&3 || true)"
  exec 3>&-
  printf '%s' "$reply"
}

scanning_music() {
  rpc '{"jsonrpc":"2.0","id":1,"method":"XBMC.GetInfoBooleans","params":{"booleans":["Library.IsScanningMusic"]}}' |
    grep -q '"Library.IsScanningMusic":true'
}

wait_for_music_scan() {
  # A scan already running (Kodi's startup update) refuses another.
  while scanning_music; do
    sleep 10
  done
}

music_scan() {
  local attempt
  for attempt in $(seq 1 60); do
    if rpc '{"jsonrpc":"2.0","id":1,"method":"JSONRPC.Ping"}' | grep -q pong; then
      break
    fi
    sleep 5
  done

  local db spec name path scanned
  db="$(find_db 'MyMusic*.db')" || {
    log "Kodi has no music library database yet"
    return 0
  }
  for spec in "${MUSIC_SOURCES[@]}"; do
    [[ -n "$spec" ]] || continue
    IFS='|' read -r name path <<< "$spec"
    [[ -d "$path" ]] || continue
    scanned="$(sqlite3 -readonly -cmd '.timeout 5000' "$db" "SELECT COUNT(*) FROM path WHERE strPath LIKE '${path}%';")"
    if [[ "$scanned" != "0" ]]; then
      continue
    fi
    wait_for_music_scan
    log "first music scan of ${name} (${path})"
    rpc "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"AudioLibrary.Scan\",\"params\":{\"directory\":\"${path}\",\"showdialogs\":false}}" >/dev/null
    sleep 5
    wait_for_music_scan
  done
}

if [[ "${1:-}" == "music-scan" ]]; then
  music_scan
  exit 0
fi

if [[ "${1:-}" == "sources-xml" ]]; then
  configure_video_sources_xml
  exit 0
fi

ensure_storage

# Each on its own: a music source isn't held up by the video library.
status=0
configure_video_library || status=1
configure_music_library || status=1
configure_video_sources_xml || status=1
if ((status == 0)); then
  log "library sources configured"
else
  log "library setup incomplete (waiting for Kodi databases or media paths)"
fi
