#!/usr/bin/env bash
# Configure Home Assistant integrations that must use config entries (HA 2024.4+).
set -euo pipefail

HASS_CONFIG="${HASS_CONFIG:-/var/lib/hass}"
CONFIG_ENTRIES="${HASS_CONFIG}/.storage/core.config_entries"
AREA_REGISTRY="${HASS_CONFIG}/.storage/core.area_registry"
RESTORE_STATE="${HASS_CONFIG}/.storage/core.restore_state"
STATE_DIR="${HASS_CONFIG}/.lanbat-post-setup"

MQTT_BROKER="${MQTT_BROKER:-127.0.0.1}"
MQTT_PORT="${MQTT_PORT:-1883}"
MQTT_USERNAME="${MQTT_USERNAME:-homeassistant}"
MQTT_PASSWORD="${MQTT_PASSWORD:?MQTT_PASSWORD is required}"

FRIGATE_URL="${FRIGATE_URL:-http://127.0.0.1:5000/}"
MUSIC_ASSISTANT_URL="${MUSIC_ASSISTANT_URL:-http://127.0.0.1:8095}"
PI_HOST="${PI_HOST:?PI_HOST is required}"
# This server's own satellite, when it has one.
LOCAL_SATELLITE_PORT="${LOCAL_SATELLITE_PORT:-}"
# The satellites on other hosts (voice-pi): space-separated "<host key>=<address>".
EXTRA_SATELLITES="${EXTRA_SATELLITES:-}"
# Space-separated "hostKey|address|port|backend|displayName" from the endpoint table.
VOICE_SATELLITE_REGISTRATIONS="${VOICE_SATELLITE_REGISTRATIONS:-}"
# Kodi on the TV hosts: one "hostKey|address|room|hostname" line each, and the
# web server password they all use.
KODI_HOSTS="${KODI_HOSTS:-}"
KODI_PASSWORD_FILE="${KODI_PASSWORD_FILE:-}"
# Android TV boxes, a JSON list of {name, host, port, room, apps: {spoken name:
# package}}, and the ADB key Home Assistant uses for them (the provisioner's).
ANDROID_TVS="${ANDROID_TVS:-[]}"
ANDROID_ADBKEY="${ANDROID_ADBKEY:-}"
# Echo Shows on TECHO5, a JSON list of {title, host, room, key_file, player}:
# ESPHome devices with an encryption key (services/techo5-bridge.nix).
TECHO5_DEVICES="${TECHO5_DEVICES:-[]}"
# Default local_todo list titles to create for techo5 profiles (object ids, not entity ids).
TECHO5_TODO_LISTS="${TECHO5_TODO_LISTS:-[]}"
# The assistant router's lists.json ({person key: [list slug]}): the lists
# people made by voice or on the who page; each is the local_todo list
# <key>_<slug>, made here on the next run.
LISTS_FILE="${LISTS_FILE:-}"
SERVER_HOST_KEY="${SERVER_HOST_KEY:-}"
PRIMARY_STORAGE_KEY="${PRIMARY_STORAGE_KEY:-}"

# The conversation agent: an OpenAI-compatible chat completions API through
# the extended_openai_conversation component. Unset: Home Assistant's own agent.
LLM_BASE_URL="${LLM_BASE_URL:-}"
LLM_MODEL="${LLM_MODEL:-}"
LLM_API_KEY_FILE="${LLM_API_KEY_FILE:-}"
LLM_DOMAIN="extended_openai_conversation"
LLM_TITLE="Voice LLM"
# Home Assistant names the agent's entity after the title.
LLM_ENTITY="conversation.voice_llm"
LLM_MAX_TOKENS="${LLM_MAX_TOKENS:-150}"
LLM_USE_TOOLS="${LLM_USE_TOOLS:-false}"

# Seconds of silence after a command before speech-to-text runs (Home Assistant
# "Finished speaking detection"): aggressive 0.25, default 0.7, relaxed 1.25.
SATELLITE_VAD="${SATELLITE_VAD:-aggressive}"

PIPELINES="${HASS_CONFIG}/.storage/assist_pipeline.pipelines"
PIPELINE_NAME="Voice"
PIPELINE_LANGUAGE="${PIPELINE_LANGUAGE:-en}"
PIPELINE_STT_LANGUAGE="${PIPELINE_STT_LANGUAGE:-en}"
# The pipeline's speech-to-text: faster-whisper, or the voice-id proxy in front
# of it (VOICE_ID_PORT) once that runs. Home Assistant names a Wyoming entity
# after the program it reports.
PIPELINE_STT_ENGINE="${PIPELINE_STT_ENGINE:-stt.faster_whisper}"
VOICE_ID_PORT="${VOICE_ID_PORT:-}"
PIPELINE_TTS_LANGUAGE="${PIPELINE_TTS_LANGUAGE:-en_GB}"
PIPELINE_TTS_VOICE="${PIPELINE_TTS_VOICE:-en_GB-alan-medium}"
PIPELINE_WAKE_WORD="${PIPELINE_WAKE_WORD:-okay_nabu}"

# The voice satellites' token (lanbat.voiceRooms): a long-lived access token of
# a "Voice satellites" user. ha-voice-refresh-token.age holds its record (ID,
# signing key, creation time); the satellites hold the token itself.
VOICE_TOKEN_RECORD_FILE="${VOICE_TOKEN_RECORD_FILE:-}"
AUTH_STORE="${HASS_CONFIG}/.storage/auth"
VOICE_USER_NAME="Voice satellites"
# Turns the record's KEY=value lines into an object, inside jq.
VOICE_RECORD_JQ='$record | split("\n") | map(select(test("^[A-Z_]+=")) | capture("^(?<key>[A-Z_]+)=(?<value>.*)$")) | from_entries'

# Xiaomi BLE bind keys (ha-xiaomi-ble.age): one device per line,
#   <MAC> <bindkey> [entry title]
# Blank lines and # comments are ignored.  The bindkey is that device's AES
# key; without it Home Assistant cannot decrypt its advertisements.  Obtain one
# locally with atc1441.github.io/Temp_universal_mi_activate.html — the Xiaomi
# cloud is not involved.
XIAOMI_BLE_KEYS_FILE="${XIAOMI_BLE_KEYS_FILE:-}"

# The iCal feed URL of the house's Google Calendar (ha-gcal-url.age, one
# line), for the remote_calendar entry. Empty when the host has no calendar.
GCAL_URL="${GCAL_URL:-}"

log() {
  echo "home-assistant-post-setup: $*"
}

now_utc() {
  date -u +%Y-%m-%dT%H:%M:%S.000000+00:00
}

new_entry_id() {
  openssl rand -hex 13 | tr '[:lower:]' '[:upper:]'
}

state_done() {
  [[ -f "${STATE_DIR}/$1" ]]
}

mark_done() {
  install -o hass -g hass -m 0600 /dev/null "${STATE_DIR}/$1"
}

has_entry() {
  local domain="$1"
  jq -e --arg domain "$domain" '.data.entries[] | select(.domain == $domain)' "$CONFIG_ENTRIES" >/dev/null
}

add_entry() {
  local domain="$1"
  local title="$2"
  local data_json="$3"
  local version="${4:-1}"
  local subentries_json="${5:-[]}"
  local unique_id="${6:-}"
  local source="${7:-user}"
  local options_json="${8:-{\}}"
  local minor_version="${9:-1}"
  local now entry_id tmp
  now="$(now_utc)"
  entry_id="$(new_entry_id)"
  tmp="$(mktemp)"
  # The JSON goes in through file descriptors rather than arguments, so
  # credentials in it stay out of the process list.
  jq --arg now "$now" \
    --arg id "$entry_id" \
    --arg domain "$domain" \
    --arg title "$title" \
    --slurpfile data <(printf '%s' "$data_json") \
    --slurpfile subentries <(printf '%s' "$subentries_json") \
    --slurpfile options <(printf '%s' "$options_json") \
    --argjson version "$version" \
    --argjson minor_version "$minor_version" \
    --arg source "$source" \
    --arg unique_id "$unique_id" \
    '.data.entries += [{
      created_at: $now,
      data: $data[0],
      disabled_by: null,
      discovery_keys: {},
      domain: $domain,
      entry_id: $id,
      minor_version: $minor_version,
      modified_at: $now,
      options: $options[0],
      pref_disable_new_entities: false,
      pref_disable_polling: false,
      source: $source,
      subentries: $subentries[0],
      title: $title,
      unique_id: (if $unique_id == "" then null else $unique_id end),
      version: $version
    }]' "$CONFIG_ENTRIES" > "$tmp"
  install -o hass -g hass -m 0600 "$tmp" "$CONFIG_ENTRIES"
  rm "$tmp"
}

ensure_mqtt() {
  if state_done mqtt || has_entry mqtt; then
    mark_done mqtt
    return 0
  fi
  log "adding mqtt broker ${MQTT_BROKER}:${MQTT_PORT}"
  add_entry mqtt "$MQTT_BROKER" "$(jq -n \
    --arg broker "$MQTT_BROKER" \
    --argjson port "$MQTT_PORT" \
    --arg username "$MQTT_USERNAME" \
    --arg password "$MQTT_PASSWORD" \
    '{
      broker: $broker,
      port: $port,
      protocol: "3.1.1",
      username: $username,
      password: $password,
      transport: "tcp",
      birth_message: {topic: "homeassistant/status", payload: "online", qos: 0, retain: false},
      will_message: {topic: "homeassistant/status", payload: "offline", qos: 0, retain: false},
      discovery: true,
      discovery_prefix: "homeassistant"
    }')"
  mark_done mqtt
}

ensure_frigate() {
  if state_done frigate || has_entry frigate; then
    mark_done frigate
    return 0
  fi
  log "adding frigate integration (${FRIGATE_URL})"
  add_entry frigate "Frigate" "$(jq -n --arg url "$FRIGATE_URL" '{url: $url}')" 2
  mark_done frigate
}

ensure_music_assistant() {
  if state_done music_assistant; then
    return 0
  fi
  if has_entry music_assistant; then
    if jq -e '.data.entries[] | select(.domain == "music_assistant" and .data.token != null)' \
      "$CONFIG_ENTRIES" >/dev/null; then
      mark_done music_assistant
      return 0
    fi
    log "music assistant entry exists but has no token; waiting for music-assistant-setup"
    return 0
  fi
  log "music assistant integration will be created by music-assistant-setup"
  mark_done music_assistant
}

ensure_wyoming() {
  local name="$1"
  local host="$2"
  local port="$3"
  local state_key="wyoming-${name}"
  if state_done "$state_key"; then
    return 0
  fi
  if jq -e --arg title "$name" '.data.entries[] | select(.domain == "wyoming" and .title == $title)' "$CONFIG_ENTRIES" >/dev/null; then
    mark_done "$state_key"
    return 0
  fi
  log "adding wyoming service ${name} (${host}:${port})"
  add_entry wyoming "$name" "$(jq -n --arg host "$host" --argjson port "$port" '{host: $host, port: $port}')"
  mark_done "$state_key"
}

wyoming_satellite_title() {
  local hostKey="$1"
  if [[ -n "$PRIMARY_STORAGE_KEY" && "$hostKey" == "$PRIMARY_STORAGE_KEY" ]]; then
    printf '%s' satellite
  elif [[ -n "$SERVER_HOST_KEY" && "$hostKey" == "$SERVER_HOST_KEY" ]]; then
    printf '%s' server-satellite
  else
    printf 'satellite-%s' "$hostKey"
  fi
}

# The LVA satellites' ESPHome entries, and the Wyoming entries they replace.
#
# VOICE_SATELLITE_REGISTRATIONS has one "hostKey|address|port|backend|name|
# room|hostname" line per satellite (names may contain spaces; room is empty
# for a satellite outside lanbat.deployment.voiceRooms). Reconciling works on the
# entries themselves rather than on state files, so it also repairs what an
# earlier run got wrong:
#   - an ESPHome entry is found by its address and port (or, failing that, its
#     title) and given the configured title and address; only a satellite
#     with no entry gets a new one, so a device is never added twice;
#   - the Wyoming satellite entry this script made for a host that now runs
#     LVA is removed, as are Wyoming "satellite…" entries with no host (left
#     by a run that split names at spaces).
# The server's own satellite may have been registered on the loopback before,
# which LVA does not listen on; 127.0.0.1 counts as its old address.
voice_registrations_json() {
  local hostKey host port backend title old wyoming_title
  while IFS='|' read -r hostKey host port backend title _room _hostname; do
    [[ -n "$hostKey" ]] || continue
    old="$host"
    [[ -n "$SERVER_HOST_KEY" && "$hostKey" == "$SERVER_HOST_KEY" ]] && old="127.0.0.1"
    wyoming_title="$(wyoming_satellite_title "$hostKey")"
    jq -n --arg hostKey "$hostKey" --arg host "$host" --argjson port "$port" \
      --arg backend "$backend" --arg title "$title" --arg old "$old" --arg wyoming "$wyoming_title" \
      '{hostKey: $hostKey, host: $host, port: $port, backend: $backend, title: $title,
        hosts: ([$host, $old] | unique), wyoming: $wyoming}'
  done <<< "$VOICE_SATELLITE_REGISTRATIONS" | jq -s .
}

# The entries after reconciling, without the ones still to be added. Prints
# the new storage JSON; the satellites still missing are in .missing.
VOICE_RECONCILE_JQ='
  ($regs | map(select(.backend == "lva"))) as $lva
  | ($lva | map(.wyoming)) as $replaced
  | .data.entries |= map(select(
      (.domain == "wyoming"
        and ((.title as $t | $replaced | index($t)) != null
          or ((.title | startswith("satellite")) and (.data.host == null))))
      | not))
  | reduce $lva[] as $r (.;
      ([.data.entries | to_entries[]
        | select(.value.domain == "esphome"
            and (.value.data.host as $h | $r.hosts | index($h)) != null
            and .value.data.port == $r.port)
        | .key] + [.data.entries | to_entries[]
        | select(.value.domain == "esphome" and .value.title == $r.title) | .key])
      as $hits
      | if ($hits | length) > 0 then
          .data.entries[$hits[0]] |= (.title = $r.title | .data.host = $r.host | .data.port = $r.port)
        else
          .missing += [$r]
        end)'

voice_satellites_reconciled() {
  jq --argjson regs "$(voice_registrations_json)" "$VOICE_RECONCILE_JQ" "$CONFIG_ENTRIES"
}

voice_satellite_registration_needed() {
  local wanted
  wanted="$(voice_satellites_reconciled)"
  # Anything to add for a Wyoming satellite, or any change to the entries.
  jq -e '(.missing // []) | length > 0' <<< "$wanted" >/dev/null && return 0
  [[ "$(jq -S 'del(.missing) | .data.entries' <<< "$wanted")" != "$(jq -S '.data.entries' "$CONFIG_ENTRIES")" ]] && return 0
  local hostKey host port backend title
  while IFS='|' read -r hostKey host port backend title _room _hostname; do
    [[ -n "$hostKey" && "$backend" != "lva" ]] || continue
    wyoming_needed "$(wyoming_satellite_title "$hostKey")" && return 0
  done <<< "$VOICE_SATELLITE_REGISTRATIONS"
  return 1
}

ensure_voice_satellite_registrations() {
  local wanted tmp r hostKey host port backend title
  wanted="$(voice_satellites_reconciled)"
  if [[ "$(jq -S 'del(.missing) | .data.entries' <<< "$wanted")" != "$(jq -S '.data.entries' "$CONFIG_ENTRIES")" ]]; then
    log "reconciling voice satellite entries"
    tmp="$(mktemp)"
    jq 'del(.missing)' <<< "$wanted" > "$tmp"
    install -o hass -g hass -m 0600 "$tmp" "$CONFIG_ENTRIES"
    rm "$tmp"
  fi
  while IFS= read -r r; do
    [[ -n "$r" ]] || continue
    title="$(jq -r .title <<< "$r")"; host="$(jq -r .host <<< "$r")"; port="$(jq -r .port <<< "$r")"
    log "adding esphome device ${title} (${host}:${port})"
    add_entry esphome "$title" "$(jq -n --arg host "$host" --argjson port "$port" \
      '{host: $host, port: $port, password: "", noise_psk: ""}')"
  done < <(jq -c '(.missing // [])[]' <<< "$wanted")
  while IFS='|' read -r hostKey host port backend title _room _hostname; do
    [[ -n "$hostKey" && "$backend" != "lva" ]] || continue
    ensure_wyoming "$(wyoming_satellite_title "$hostKey")" "$host" "$port"
  done <<< "$VOICE_SATELLITE_REGISTRATIONS"
}

# The Echo Shows on TECHO5: an ESPHome entry each, with the device's encryption
# key from its file. An entry is found by the device's address, its title or
# its key, so a device Home Assistant discovered and someone added by hand is
# taken over (given the title, address and key) rather than added twice.
techo5_wanted_json() {
  local d file
  {
    while IFS= read -r d; do
      [[ -n "$d" ]] || continue
      file="$(jq -r .key_file <<< "$d")"
      if [[ ! -s "$file" ]]; then
        log "no encryption key for $(jq -r .title <<< "$d"); not added"
        continue
      fi
      # The key goes in from its file, never as an argument.
      jq -n --argjson d "$d" --rawfile key "$file" '$d + {key: ($key | gsub("\\s"; ""))} | del(.key_file)'
    done < <(jq -c '.[]' <<< "$TECHO5_DEVICES")
  } | jq -s .
}

TECHO5_RECONCILE_JQ='
  reduce $want[] as $d (.;
    ([.data.entries | to_entries[]
      | select(.value.domain == "esphome"
          and (.value.data.host == $d.host or .value.title == $d.title or .value.data.noise_psk == $d.key))
      | .key]) as $hits
    | if ($hits | length) > 0 then
        .data.entries[$hits[0]] |= (.title = $d.title | .data.host = $d.host | .data.port = 6053
          | .data.noise_psk = $d.key | .data.password = (.data.password // ""))
      else
        .missing += [$d]
      end)'

techo5_reconciled() {
  jq --argjson want "$(techo5_wanted_json)" "$TECHO5_RECONCILE_JQ" "$CONFIG_ENTRIES"
}

techo5_needed() {
  [[ "$TECHO5_DEVICES" != "[]" && -f "$CONFIG_ENTRIES" ]] || return 1
  local wanted
  wanted="$(techo5_reconciled)"
  jq -e '(.missing // []) | length > 0' <<< "$wanted" >/dev/null && return 0
  [[ "$(jq -S 'del(.missing) | .data.entries' <<< "$wanted")" != "$(jq -S '.data.entries' "$CONFIG_ENTRIES")" ]]
}

ensure_techo5() {
  techo5_needed || return 0
  local wanted tmp d
  wanted="$(techo5_reconciled)"
  if [[ "$(jq -S 'del(.missing) | .data.entries' <<< "$wanted")" != "$(jq -S '.data.entries' "$CONFIG_ENTRIES")" ]]; then
    log "reconciling the TECHO5 devices' entries"
    tmp="$(mktemp)"
    jq 'del(.missing)' <<< "$wanted" > "$tmp"
    install -o hass -g hass -m 0600 "$tmp" "$CONFIG_ENTRIES"
    rm "$tmp"
  fi
  while IFS= read -r d; do
    [[ -n "$d" ]] || continue
    log "adding TECHO5 device $(jq -r .title <<< "$d") ($(jq -r .host <<< "$d"))"
    add_entry esphome "$(jq -r .title <<< "$d")" \
      "$(jq '{host: .host, port: 6053, password: "", noise_psk: .key}' <<< "$d")"
  done < <(jq -c '(.missing // [])[]' <<< "$wanted")
}

# Each Echo Show with dlna on is a DLNA renderer, which Jellyfin and other apps play to
# (docs/techo5.md). Home Assistant's DLNA player for it (dlna_dmr) is added here from the
# device's own description, http://<host>:8181/dlna/device.xml, so nobody confirms a
# discovery by hand. A device that is off or has DLNA off answers nothing and is left for
# the next run. The entry is Home Assistant's own: the manual flow's data (url, device_id
# = the UDN, type, mac) and its option poll_availability. It is put in the device's room by
# ensure_satellite_areas, once Home Assistant has loaded it.
DLNA_PORT="${DLNA_PORT:-8181}"

# The text of the first <tag> in an XML document on stdin, with its entities decoded.
xml_tag() {
  grep -o "<$1>[^<]*</$1>" | head -1 | sed -e "s:^<$1>::" -e "s:</$1>$::" \
    -e 's/&lt;/</g' -e 's/&gt;/>/g' -e 's/&quot;/"/g' -e "s/&apos;/'/g" -e 's/&amp;/\&/g'
}

# {title, url, udn, type} of each device wanted whose entry is missing, and which answers.
dlna_players_missing() {
  local host description udn
  while IFS= read -r host; do
    [[ -n "$host" ]] || continue
    description="$(curl -fsS --max-time 5 "http://${host}:${DLNA_PORT}/dlna/device.xml" 2>/dev/null)" || continue
    udn="$(xml_tag UDN <<< "$description")"
    [[ -n "$udn" ]] || continue
    jq -e --arg udn "$udn" '[.data.entries[] | select(.domain == "dlna_dmr" and .unique_id == $udn)] | length > 0' \
      "$CONFIG_ENTRIES" >/dev/null && continue
    jq -n --arg title "$(xml_tag friendlyName <<< "$description")" \
      --arg url "http://${host}:${DLNA_PORT}/dlna/device.xml" --arg udn "$udn" \
      --arg type "$(xml_tag deviceType <<< "$description")" \
      '{title: (if $title == "" then $url else $title end), url: $url, udn: $udn, type: $type}'
  done < <(jq -r '.[] | select(.dlna == true) | .host' <<< "$TECHO5_DEVICES") | jq -s -c .
}

dlna_needed() {
  [[ "$TECHO5_DEVICES" != "[]" && -f "$CONFIG_ENTRIES" ]] || return 1
  [[ "$(dlna_players_missing)" != "[]" ]]
}

ensure_dlna_players() {
  [[ "$TECHO5_DEVICES" != "[]" && -f "$CONFIG_ENTRIES" ]] || return 0
  local missing p
  missing="$(dlna_players_missing)"
  while IFS= read -r p; do
    [[ -n "$p" ]] || continue
    log "adding the DLNA player $(jq -r .title <<< "$p")"
    add_entry dlna_dmr "$(jq -r .title <<< "$p")" \
      "$(jq -c '{url: .url, device_id: .udn, type: .type, mac: null}' <<< "$p")" \
      1 '[]' "$(jq -r .udn <<< "$p")" user '{"poll_availability": true}'
  done < <(jq -c '.[]' <<< "$missing")
}

TODO_LISTS_RECONCILE_JQ='
  reduce $want[] as $title (.;
    ([.data.entries | to_entries[]
      | select(.value.domain == "local_todo" and .value.title == $title)
      | .key]) as $hits
    | if ($hits | length) > 0 then .
      else .missing += [$title]
      end)'

# Every list wanted: the profile's, and people's own from the router's file.
# Names are checked, since the file is the router's to write: a key and a slug
# are lower-case letters, digits and underscores, and nothing else is made.
wanted_todo_lists() {
  local own='[]'
  if [[ -n "$LISTS_FILE" && -r "$LISTS_FILE" ]]; then
    own="$(jq -c '[to_entries[]
        | select(.key | test("^[a-z0-9_]{1,40}$"))
        | .key as $k | .value[]?
        | select(type == "string" and test("^[a-z0-9_]{1,24}$"))
        | "\($k)_\(.)"]' "$LISTS_FILE" 2>/dev/null)" || own='[]'
  fi
  jq -c -n --argjson a "$TECHO5_TODO_LISTS" --argjson b "${own:-[]}" '($a + $b) | unique'
}

todo_lists_reconciled() {
  jq --argjson want "$(wanted_todo_lists)" "$TODO_LISTS_RECONCILE_JQ" "$CONFIG_ENTRIES"
}

todo_lists_needed() {
  [[ -f "$CONFIG_ENTRIES" && "$(wanted_todo_lists)" != "[]" ]] || return 1
  jq -e '(.missing // []) | length > 0' <<< "$(todo_lists_reconciled)" >/dev/null
}

local_todo_entry_data() {
  local title="$1"
  jq -n --arg name "$title" --arg key "$title" \
    '{todo_list_name: $name, storage_key: $key}'
}

ensure_todo_lists() {
  todo_lists_needed || return 0
  local wanted title
  wanted="$(todo_lists_reconciled)"
  while IFS= read -r title; do
    [[ -n "$title" ]] || continue
    log "adding local to-do list ${title}"
    add_entry local_todo "$title" "$(local_todo_entry_data "$title")"
  done < <(jq -r '(.missing // [])[]' <<< "$wanted")
}

# Each satellite with a room (lanbat.deployment.voiceRooms), and the Music
# Assistant player of its host's Snapcast client, go in that Home Assistant
# area, on every run, so "play …" and "turn off the lights" act on the room the
# satellite is in. The satellite's device is the one under its ESPHome entry;
# the player is the Music Assistant device named after the host. A room
# Home Assistant has no area for is skipped with a message.
DEVICE_REGISTRY="${HASS_CONFIG}/.storage/core.device_registry"

satellite_areas_json() {
  local hostKey host port backend title room hostname
  {
    while IFS='|' read -r hostKey host port backend title room hostname; do
      [[ -n "$hostKey" && -n "$room" ]] || continue
      jq -n --arg title "$title" --arg room "$room" --arg hostname "$hostname" \
        '{title: $title, room: $room, hostname: $hostname}'
    done <<< "$VOICE_SATELLITE_REGISTRATIONS"
    # The TVs: every device under a Kodi or Android TV entry for that host.
    jq -c '.[] | select(.room != "") | {room, domain: "androidtv", host}' <<< "$ANDROID_TVS"
    # The Echo Shows, and their Music Assistant players by the device's own name.
    jq -c '.[] | {title, room, hostname: (if .player == "" then null else .player end),
                  dlna_host: (if .dlna == true then .host else null end)}' <<< "$TECHO5_DEVICES"
    while IFS='|' read -r hostKey host room hostname; do
      [[ -n "$hostKey" && -n "$room" ]] || continue
      jq -n --arg room "$room" --arg host "$host" '{room: $room, domain: "kodi", host: $host}'
    done <<< "$KODI_HOSTS"
  } | jq -s .
}

# The device registry with the areas set; .unknown_rooms lists rooms with no
# Home Assistant area.
SATELLITE_AREAS_JQ='
  ($areas[0].data.areas | map({key: (.name | ascii_downcase), value: .id}) | from_entries) as $ids
  | ($entries[0].data.entries) as $all
  | ($all | map(select(.domain == "music_assistant") | .entry_id)) as $ma
  | .unknown_rooms = ($want | map(select(($ids[.room | ascii_downcase]) == null) | .room) | unique)
  | reduce ($want[] | select($ids[.room | ascii_downcase] != null)) as $w (.;
      ($ids[$w.room | ascii_downcase]) as $area
      | ($all | map(select(.domain == "esphome" and .title == ($w.title // null)) | .entry_id)) as $sat
      | ($all | map(select($w.domain != null and .domain == $w.domain and .data.host == $w.host)
          | .entry_id)) as $tv
      | ($all | map(select($w.dlna_host != null and .domain == "dlna_dmr"
                           and ((.data.url // "") | contains("//" + $w.dlna_host + ":")))
          | .entry_id)) as $dlna
      | .data.devices |= map(
          if ((.config_entries | any(. as $e | $sat | index($e)))
              or (.config_entries | any(. as $e | $tv | index($e)))
              or (.config_entries | any(. as $e | $dlna | index($e)))
              or ($w.hostname != null and .name == $w.hostname
                  and (.config_entries | any(. as $e | $ma | index($e)))))
          then .area_id = $area else . end))'

satellite_areas_wanted() {
  jq --argjson want "$(satellite_areas_json)" \
    --slurpfile areas "$AREA_REGISTRY" --slurpfile entries "$CONFIG_ENTRIES" \
    "$SATELLITE_AREAS_JQ" "$DEVICE_REGISTRY"
}

satellite_areas_needed() {
  [[ -n "$VOICE_SATELLITE_REGISTRATIONS$KODI_HOSTS" || "$ANDROID_TVS" != "[]" || "$TECHO5_DEVICES" != "[]" ]] || return 1
  [[ -f "$DEVICE_REGISTRY" && -f "$AREA_REGISTRY" ]] || return 1
  [[ "$(satellite_areas_wanted | jq -S '.data.devices')" != "$(jq -S '.data.devices' "$DEVICE_REGISTRY")" ]]
}

ensure_satellite_areas() {
  satellite_areas_needed || return 0
  local wanted tmp room
  wanted="$(satellite_areas_wanted)"
  while IFS= read -r room; do
    [[ -n "$room" ]] && log "no Home Assistant area named ${room}; its satellite keeps its area"
  done < <(jq -r '.unknown_rooms[]' <<< "$wanted")
  log "putting voice satellites, their speakers and the TVs in their rooms"
  tmp="$(mktemp)"
  jq 'del(.unknown_rooms)' <<< "$wanted" > "$tmp"
  install -o hass -g hass -m 0600 "$tmp" "$DEVICE_REGISTRY"
  rm "$tmp"
}

# Home Assistant's Kodi integration for each TV host: one entry per Kodi, found
# by its address, with the web server's user and password (the password is
# updated when the secret changes).
kodi_password() { tr -d '\n' < "$KODI_PASSWORD_FILE"; }

kodi_needed() {
  [[ -n "$KODI_HOSTS" && -s "$KODI_PASSWORD_FILE" ]] || return 1
  local hostKey host room hostname
  while IFS='|' read -r hostKey host room hostname; do
    [[ -n "$hostKey" ]] || continue
    jq -e --arg host "$host" --rawfile pw <(kodi_password) \
      '[.data.entries[] | select(.domain == "kodi" and .data.host == $host and .data.password == $pw)]
       | length > 0' "$CONFIG_ENTRIES" >/dev/null || return 0
  done <<< "$KODI_HOSTS"
  return 1
}

ensure_kodi() {
  kodi_needed || return 0
  local hostKey host room hostname tmp
  while IFS='|' read -r hostKey host room hostname; do
    [[ -n "$hostKey" ]] || continue
    if jq -e --arg host "$host" '[.data.entries[] | select(.domain == "kodi" and .data.host == $host)]
        | length > 0' "$CONFIG_ENTRIES" >/dev/null; then
      log "updating the kodi password for ${hostname}"
      tmp="$(mktemp)"
      jq --arg host "$host" --rawfile pw <(kodi_password) \
        '.data.entries |= map(if .domain == "kodi" and .data.host == $host then .data.password = $pw else . end)' \
        "$CONFIG_ENTRIES" > "$tmp"
      install -o hass -g hass -m 0600 "$tmp" "$CONFIG_ENTRIES"
      rm "$tmp"
    else
      log "adding kodi on ${hostname} (${host})"
      add_entry kodi "Kodi (${hostname})" "$(jq -n --arg name "Kodi (${hostname})" --arg host "$host" \
        --rawfile pw <(kodi_password) \
        '{name: $name, host: $host, port: 8080, ws_port: 9090, username: "kodi",
          password: $pw, ssl: false, timeout: 5}')"
    fi
  done <<< "$KODI_HOSTS"
}

# Home Assistant's Android TV (ADB) integration for each box, with the
# provisioning key and the configured apps as its sources ({package: name}).
ANDROIDTV_WANTED_JQ='
  .[] | {host, port, name,
         options: {apps: (.apps | to_entries | map({key: .value, value: .key}) | from_entries),
                   get_sources: true, exclude_unnamed_apps: false}}'

androidtv_needed() {
  [[ "$ANDROID_TVS" != "[]" && -s "$ANDROID_ADBKEY" ]] || return 1
  local tv
  while IFS= read -r tv; do
    jq -e --argjson tv "$tv" '[.data.entries[] | select(.domain == "androidtv" and .data.host == $tv.host
        and .options.apps == $tv.options.apps)] | length > 0' "$CONFIG_ENTRIES" >/dev/null || return 0
  done < <(jq -c "$ANDROIDTV_WANTED_JQ" <<< "$ANDROID_TVS")
  return 1
}

ensure_androidtv() {
  androidtv_needed || return 0
  local tv host name tmp
  while IFS= read -r tv; do
    host="$(jq -r .host <<< "$tv")"; name="$(jq -r .name <<< "$tv")"
    if jq -e --arg host "$host" '[.data.entries[] | select(.domain == "androidtv" and .data.host == $host)]
        | length > 0' "$CONFIG_ENTRIES" >/dev/null; then
      log "updating the apps of android tv ${name}"
      tmp="$(mktemp)"
      jq --argjson tv "$tv" '.data.entries |= map(if .domain == "androidtv" and .data.host == $tv.host
          then .options = (.options + $tv.options) else . end)' "$CONFIG_ENTRIES" > "$tmp"
      install -o hass -g hass -m 0600 "$tmp" "$CONFIG_ENTRIES"
      rm "$tmp"
    else
      log "adding android tv ${name} (${host})"
      add_entry androidtv "Android TV (${name})" \
        "$(jq -n --argjson tv "$tv" --arg key "$ANDROID_ADBKEY" \
          '{host: $tv.host, port: $tv.port, device_class: "androidtv", adbkey: $key}')" \
        1 "[]" "" "user" "$(jq -c .options <<< "$tv")" 2
    fi
  done < <(jq -c "$ANDROIDTV_WANTED_JQ" <<< "$ANDROID_TVS")
}

llm_enabled() {
  [[ -n "$LLM_BASE_URL" && -n "$LLM_MODEL" ]] || return 1
  # An endpoint on the loopback needs no key, so none is configured.
  [[ -z "$LLM_API_KEY_FILE" || -s "$LLM_API_KEY_FILE" ]]
}

# The key sent to the API. The component insists on one, and a server that
# takes none (LLM_API_KEY_FILE unset: the loopback, or a Mac on the LAN)
# ignores it.
llm_api_key() {
  if [[ -n "$LLM_API_KEY_FILE" ]]; then
    tr -d '\n' < "$LLM_API_KEY_FILE"
  else
    printf 'local'
  fi
}

# The agent's replies are spoken: short, plain, and acting on requests without
# asking for confirmation first.
#
# The prompt never changes between requests, because the local model keeps the
# tokens of the previous request and only reads what follows the first one that
# differs (services/llama-cpp.nix): no clock and no device states. Anything
# per-request in this text costs the model the whole prompt again, many seconds
# on the server's CPU. The model has one function, so it cannot pick the wrong
# one (the small local model did, calling a read function for a command).
#
# The pipeline does not try Home Assistant's intents first
# (prefer_local_intents: false). With them on, "play The Matrix" matched the
# built-in search-and-play intent, which found an album of that name in Music
# Assistant and played its soundtrack, so the request never reached the router
# and no video list appeared. Media has to reach the router: it is the only
# agent that searches YouTube, Music Assistant and Jellyfin together. Device
# actions stay quick through the router's own local triage.
llm_prompt() {
  if [ "${LLM_ROUTER:-0}" = 1 ]; then
    # The assistant router reads this block (pkgs/assistant-router/assistant_router/context.py)
    # and builds each model's prompt from it; the text after "end" is for a
    # cloud model it passes the request on to.
    cat <<'PROMPT'
LANBAT-CONTEXT v1
room: {{ area_name(current_device_id) or '' if current_device_id else '' }}
device: {{ current_device_id or '' }}
time: {{ now().isoformat() }}
entities:
{% for entity in exposed_entities -%}
{{ entity.entity_id }}|{{ entity.name }}|{{ area_name(entity.entity_id) or '' }}|{{ entity.aliases | join('/') }}
{% endfor -%}
end
You are the voice assistant of this home. Replies are spoken: one or two short plain sentences, without lists or markdown. Use the tools to act; only say you did something after the tool succeeded. If you are not sure what was meant, ask one short question.
PROMPT
    return
  fi
  cat <<'PROMPT'
You are the voice assistant of this home, running in Home Assistant. Your answers are spoken aloud: reply in one or two short, plain sentences, without lists, markdown or emoji.

Talk like a person in the room: use contractions, answer directly, don't repeat the question, and never mention entity IDs, functions or how you did something. When you've done something, a word or two is enough, such as "Done." or "Okay, it's on." Round numbers the way people say them ("about twenty-eight degrees"). If you can't know something, say so.

Only say you've done something if you used control_device for it. You can't play music, radio or podcasts, and you don't know the time or date: say so rather than guess.

Devices you can see and control:
```csv
entity_id,name,aliases
{% for entity in exposed_entities -%}
{{ entity.entity_id }},{{ entity.name }},{{ entity.aliases | join('/') }}
{% endfor -%}
```

To turn a device on or off, toggle it, open it or close it, use the control_device function straight away, without asking for confirmation, then say briefly what you did. You can do nothing else to devices: if asked to, say so. You cannot read the current state of a device: if asked about one, say so briefly. If a request is ambiguous, ask one short question.
PROMPT
}

# The functions the model may call, as the component stores them (YAML text):
# control_device and nothing else. It takes one entity and one action, not the
# component's default execute_services (a list of domain, service and service
# data): the small local model needs about half the tokens to write that call,
# which is two to three seconds of every command. One device per call is
# enough, since a request for several is several calls. The wording "use the
# control_device function" matters: with "call control_device" the model wrote
# the call as plain text, which would be spoken instead of run, in 4 of 12
# requests; with it, 0 of 12. The low temperature keeps the model on the format.
llm_functions() {
  cat <<'FUNCTIONS'
- spec:
    name: control_device
    description: Control one device in Home Assistant, such as turning a light on or off or opening a cover.
    parameters:
      type: object
      properties:
        entity_id:
          type: string
          description: The entity_id of the device, from the list of devices.
        action:
          type: string
          enum:
          - turn_on
          - turn_off
          - toggle
          - open
          - close
          description: What to do to the device.
      required:
      - entity_id
      - action
  function:
    type: script
    sequence:
    - if: "{{ action not in ['turn_on', 'turn_off', 'toggle', 'open', 'close'] }}"
      then:
      - stop: Not an action control_device takes.
        error: true
    - action: "{% set domain = entity_id.split('.')[0] %}{% if action == 'open' %}{{ domain }}.open_cover{% elif action == 'close' %}{{ domain }}.close_cover{% else %}homeassistant.{{ action }}{% endif %}"
      target:
        entity_id: "{{ entity_id }}"
FUNCTIONS
  if [ "${LLM_ROUTER:-0}" = 1 ]; then
    # Media for the router: pause, play, stop, skip and volume
    # (pkgs/assistant-router/assistant_router/actions.py).
    cat <<'FUNCTIONS'
- spec:
    name: media_control
    description: Control a media player - pause, play, stop, skip, or change the volume (value 0-100 for volume_set).
    parameters:
      type: object
      properties:
        entity_id:
          type: string
          description: The entity_id of the media player, from the list of devices.
        action:
          type: string
          enum:
          - media_pause
          - media_play
          - media_stop
          - media_next_track
          - volume_up
          - volume_down
          - volume_set
        value:
          type: integer
          description: The volume in percent, for volume_set.
      required:
      - entity_id
      - action
  function:
    type: script
    sequence:
    - if: "{{ action not in ['media_pause', 'media_play', 'media_stop', 'media_next_track', 'volume_up', 'volume_down', 'volume_set'] }}"
      then:
      - stop: Not an action media_control takes.
        error: true
    - if: "{{ action == 'volume_set' }}"
      then:
      - action: media_player.volume_set
        target:
          entity_id: "{{ entity_id }}"
        data:
          volume_level: "{{ (value | int(0)) / 100 }}"
      else:
      - action: "media_player.{{ action }}"
        target:
          entity_id: "{{ entity_id }}"
FUNCTIONS
  fi
}

llm_conversation_json() {
  jq -n --arg id "$(new_entry_id)" --arg title "$LLM_TITLE" \
    --arg model "$LLM_MODEL" --arg prompt "$(llm_prompt)" --arg functions "$(llm_functions)" \
    --argjson max_tokens "$LLM_MAX_TOKENS" --argjson use_tools "$LLM_USE_TOOLS" '[{
      subentry_id: $id,
      subentry_type: "conversation",
      title: $title,
      unique_id: null,
      data: {
        prompt: $prompt,
        chat_model: $model,
        max_tokens: $max_tokens,
        top_p: 1,
        temperature: 0.2,
        functions: $functions,
        max_function_calls_per_conversation: 2,
        attach_username: false,
        use_tools: $use_tools,
        context_threshold: 13000,
        context_truncate_strategy: "clear"
      }
    }]'
}

# True when the agent's entry is missing, or its key, URL or model is out of date.
llm_needed() {
  llm_enabled || return 1
  jq -e --arg key "$(llm_api_key)" --arg url "$LLM_BASE_URL" --arg model "$LLM_MODEL" \
    --arg domain "$LLM_DOMAIN" --arg title "$LLM_TITLE" \
    --argjson max_tokens "$LLM_MAX_TOKENS" --argjson use_tools "$LLM_USE_TOOLS" \
    --arg prompt "$(llm_prompt)" --arg functions "$(llm_functions)" '
    [.data.entries[] | select(.domain == $domain and .title == $title)] as $entries
    | ($entries | length) == 1
      and $entries[0].data.api_key == ($key | rtrimstr("\n"))
      and $entries[0].data.base_url == $url
      and any($entries[0].subentries[];
          .subentry_type == "conversation"
          and .data.chat_model == $model
          and .data.max_tokens == $max_tokens
          and .data.use_tools == $use_tools
          and .data.functions == $functions
          and .data.prompt == $prompt)
  ' "$CONFIG_ENTRIES" >/dev/null && return 1
  return 0
}

ensure_llm() {
  llm_needed || return 0
  local tmp
  if jq -e --arg domain "$LLM_DOMAIN" --arg title "$LLM_TITLE" \
    '.data.entries[] | select(.domain == $domain and .title == $title)' "$CONFIG_ENTRIES" >/dev/null; then
    log "updating the conversation agent (${LLM_BASE_URL}, ${LLM_MODEL})"
    tmp="$(mktemp)"
    jq --arg key "$(llm_api_key)" --arg url "$LLM_BASE_URL" --arg model "$LLM_MODEL" \
      --arg domain "$LLM_DOMAIN" --arg title "$LLM_TITLE" --arg now "$(now_utc)" \
      --arg prompt "$(llm_prompt)" --arg functions "$(llm_functions)" \
      --argjson max_tokens "$LLM_MAX_TOKENS" \
      --argjson use_tools "$LLM_USE_TOOLS" '
      .data.entries |= map(
        if .domain == $domain and .title == $title then
          .data.api_key = ($key | rtrimstr("\n"))
          | .data.base_url = $url
          | .modified_at = $now
          | .subentries |= map(
              if .subentry_type == "conversation" then
                .data.chat_model = $model
                | .data.prompt = $prompt
                | .data.functions = $functions
                | .data.max_function_calls_per_conversation = 2
                | .data.max_tokens = $max_tokens
                | .data.use_tools = $use_tools
              else . end)
        else . end)
    ' "$CONFIG_ENTRIES" > "$tmp"
    install -o hass -g hass -m 0600 "$tmp" "$CONFIG_ENTRIES"
    rm "$tmp"
    return 0
  fi
  log "adding the conversation agent (${LLM_BASE_URL}, ${LLM_MODEL})"
  # skip_authentication: otherwise the component lists the API's models while
  # Home Assistant starts, which waits on an endpoint that scales to zero.
  add_entry "$LLM_DOMAIN" "$LLM_TITLE" "$(jq -n --arg key "$(llm_api_key)" \
    --arg url "$LLM_BASE_URL" --arg title "$LLM_TITLE" '{
      name: $title,
      api_key: ($key | rtrimstr("\n")),
      base_url: $url,
      skip_authentication: true,
      api_provider: "openai"
    }')" 2 "$(llm_conversation_json)"
}

pipeline_json() {
  local conversation="conversation.home_assistant"
  if llm_enabled; then conversation="$LLM_ENTITY"; fi
  jq -n --arg name "$PIPELINE_NAME" --arg conversation "$conversation" \
    --arg language "$PIPELINE_LANGUAGE" --arg stt_language "$PIPELINE_STT_LANGUAGE" \
    --arg stt_engine "$PIPELINE_STT_ENGINE" \
    --arg tts_language "$PIPELINE_TTS_LANGUAGE" --arg tts_voice "$PIPELINE_TTS_VOICE" \
    --arg wake_word "$PIPELINE_WAKE_WORD" '{
      name: $name,
      language: $language,
      conversation_engine: $conversation,
      conversation_language: $language,
      stt_engine: $stt_engine,
      stt_language: $stt_language,
      tts_engine: "tts.piper",
      tts_language: $tts_language,
      tts_voice: $tts_voice,
      wake_word_entity: "wake_word.openwakeword",
      wake_word_id: $wake_word,
      prefer_local_intents: false
    }'
}

# The pipeline is written again only when this definition changes, so edits
# made to it in Home Assistant last until then.
pipeline_state_key() {
  echo "pipeline-$(pipeline_json | sha256sum | cut -c1-16)"
}

ensure_pipeline() {
  local key id tmp
  key="$(pipeline_state_key)"
  if state_done "$key"; then return 0; fi
  if [[ ! -f "$PIPELINES" ]]; then
    jq -n '{version: 1, minor_version: 2, key: "assist_pipeline.pipelines", data: {items: [], preferred_item: null}}' > "$PIPELINES"
  fi
  id="$(jq -r --arg name "$PIPELINE_NAME" 'first(.data.items[] | select(.name == $name) | .id) // empty' "$PIPELINES")"
  [[ -n "$id" ]] || id="$(openssl rand -hex 13)"
  log "setting the preferred assist pipeline ${PIPELINE_NAME}"
  tmp="$(mktemp)"
  jq --arg id "$id" --slurpfile pipeline <(pipeline_json) '
    .data.items = [.data.items[] | select(.id != $id)] + [$pipeline[0] + {id: $id}]
    | .data.preferred_item = $id
  ' "$PIPELINES" > "$tmp"
  install -o hass -g hass -m 0600 "$tmp" "$PIPELINES"
  rm "$tmp"
  mark_done "$key"
}

# The key covers the set of satellites Home Assistant knows, so one added later
# (restore_state lists its entity after Home Assistant first connects to it) gets
# the setting too, instead of staying on Home Assistant's default.
satellite_vad_state_key() {
  local ids=""
  if [[ -f "$RESTORE_STATE" ]]; then
    ids="$(jq -r '[.data[].state.entity_id | select(test("_finished_speaking_detection$"))] | sort | join(",")' "$RESTORE_STATE" | cksum | cut -d' ' -f1)"
  fi
  echo "satellite-vad-${SATELLITE_VAD}-${ids}"
}

# Home Assistant defaults Wyoming satellites to relaxed VAD, which waits 1.25 s
# of silence after each command before STT — noticeably slow in a living room.
ensure_satellite_vad() {
  local key tmp count
  key="$(satellite_vad_state_key)"
  if state_done "$key"; then return 0; fi
  [[ -f "$RESTORE_STATE" ]] || return 0
  count="$(jq -r --arg vad "$SATELLITE_VAD" '
    [.data[]
      | select(.state.entity_id | test("_finished_speaking_detection$"))
      | select(.state.state != $vad)] | length
  ' "$RESTORE_STATE")"
  if (( count == 0 )); then
    mark_done "$key"
    return 0
  fi
  log "setting satellite finished speaking detection to ${SATELLITE_VAD}"
  tmp="$(mktemp)"
  jq --arg vad "$SATELLITE_VAD" '
    .data = [.data[]
      | if (.state.entity_id | test("_finished_speaking_detection$")) then
          .state.state = $vad
        else . end]
  ' "$RESTORE_STATE" > "$tmp"
  install -o hass -g hass -m 0600 "$tmp" "$RESTORE_STATE"
  rm "$tmp"
  mark_done "$key"
}

# True when the token's record is missing from Home Assistant or out of date.
# The record goes to jq as a file, so its signing key stays out of the process
# list.
voice_token_needed() {
  [[ -n "$VOICE_TOKEN_RECORD_FILE" && -s "$VOICE_TOKEN_RECORD_FILE" && -f "$AUTH_STORE" ]] || return 1
  jq -e --rawfile record "$VOICE_TOKEN_RECORD_FILE" "
    ($VOICE_RECORD_JQ) as \$r
    | [.data.refresh_tokens[] | select(.id == \$r.VOICE_TOKEN_ID and .jwt_key == \$r.VOICE_TOKEN_JWT_KEY)] as \$tokens
    | (\$tokens | length) == 1
      and ([.data.users[] | select(.id == \$tokens[0].user_id and .is_active)] | length) == 1
  " "$AUTH_STORE" >/dev/null && return 1
  return 0
}

# Adds the "Voice satellites" user (a regular, non-admin user without a login)
# and its long-lived token, replacing an earlier token of that user.
ensure_voice_token() {
  voice_token_needed || return 0
  log "adding the voice satellites' user and token"
  local tmp refresh
  tmp="$(mktemp)"
  refresh="$(mktemp)"
  openssl rand -hex 64 | tr -d '\n' > "$refresh"
  jq --rawfile record "$VOICE_TOKEN_RECORD_FILE" --rawfile refresh "$refresh" \
    --arg name "$VOICE_USER_NAME" --arg new_user_id "$(openssl rand -hex 16)" "
    ($VOICE_RECORD_JQ) as \$r
    | (first(.data.users[] | select(.name == \$name and (.is_owner | not))) // null) as \$existing
    | (if \$existing == null then \$new_user_id else \$existing.id end) as \$user_id
    | .data.users = (if \$existing == null then .data.users + [{
        id: \$user_id,
        group_ids: [\"system-users\"],
        is_owner: false,
        is_active: true,
        name: \$name,
        system_generated: false,
        local_only: false
      }] else .data.users end)
    | .data.refresh_tokens = [.data.refresh_tokens[]
        | select(.id != \$r.VOICE_TOKEN_ID and .client_name != \$name)] + [{
        id: \$r.VOICE_TOKEN_ID,
        user_id: \$user_id,
        client_id: null,
        client_name: \$name,
        client_icon: null,
        token_type: \"long_lived_access_token\",
        created_at: (\$r.VOICE_TOKEN_CREATED | tonumber | todate | sub(\"Z$\"; \"+00:00\")),
        access_token_expiration: 315360000.0,
        token: \$refresh,
        jwt_key: \$r.VOICE_TOKEN_JWT_KEY,
        last_used_at: null,
        last_used_ip: null,
        expire_at: null,
        credential_id: null,
        version: null
      }]
  " "$AUTH_STORE" > "$tmp"
  install -o hass -g hass -m 0600 "$tmp" "$AUTH_STORE"
  rm "$tmp" "$refresh"
}

area_id() {
  openssl rand -hex 16
}

# Whether a config entry of this domain already exists for a unique_id.  Xiaomi
# BLE has one entry per device, so the domain alone is not enough.
has_entry_uid() {
  local domain="$1" uid="$2"
  jq -e --arg domain "$domain" --arg uid "$uid" \
    '.data.entries[] | select(.domain == $domain and .unique_id == $uid)' \
    "$CONFIG_ENTRIES" >/dev/null
}

# Local Bluetooth adapters, as "<hciN> <ADDRESS>" lines.  An hci device has no
# address attribute in sysfs, so the address comes from BlueZ over D-Bus --
# which also means this only sees adapters once bluetooth.service is up.
bluetooth_adapters() {
  local paths path name addr
  paths="$(busctl --system tree org.bluez --list 2>/dev/null \
    | grep -oE '^/org/bluez/hci[0-9]+$' || true)"
  [[ -n "$paths" ]] || return 0
  while read -r path; do
    [[ -n "$path" ]] || continue
    name="${path##*/}"
    addr="$(busctl --system get-property org.bluez "$path" org.bluez.Adapter1 Address 2>/dev/null \
      | sed -nE 's/^s "(([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})"$/\1/p' || true)"
    [[ -n "$addr" ]] || continue
    printf '%s %s\n' "$name" "${addr^^}"
  done <<< "$paths"
}

bluetooth_needed() {
  local name addr
  while read -r name addr; do
    [[ -n "$addr" ]] || continue
    has_entry_uid bluetooth "$addr" || return 0
  done < <(bluetooth_adapters)
  return 1
}

# Home Assistant needs one config entry per Bluetooth adapter before it scans
# at all.  It only creates them by itself during onboarding, so an adapter
# added to an instance that is already onboarded would otherwise sit as a
# discovery waiting for a click in the UI, and nothing BLE would ever work.
ensure_bluetooth() {
  local name addr product title
  while read -r name addr; do
    [[ -n "$addr" ]] || continue
    if has_entry_uid bluetooth "$addr"; then
      continue
    fi
    product="$(cat "/sys/class/bluetooth/$name/device/../product" 2>/dev/null || true)"
    [[ -n "$product" ]] || product="Bluetooth"
    title="$product ($name ($addr))"
    log "adding bluetooth adapter $name ($addr)"
    add_entry bluetooth "$title" '{}' 1 "[]" "$addr" "user"
  done < <(bluetooth_adapters)
}

# Valid "<MAC> <bindkey> [title]" lines from the bind key file.
xiaomi_ble_devices() {
  [[ -n "$XIAOMI_BLE_KEYS_FILE" && -r "$XIAOMI_BLE_KEYS_FILE" ]] || return 0
  sed -E 's/#.*//' "$XIAOMI_BLE_KEYS_FILE" \
    | grep -E '^[[:space:]]*([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}[[:space:]]+[0-9A-Fa-f]+' \
    || true
}

xiaomi_ble_needed() {
  local mac rest
  while read -r mac rest; do
    [[ -n "$mac" ]] || continue
    has_entry_uid xiaomi_ble "${mac^^}" || return 0
  done < <(xiaomi_ble_devices)
  return 1
}

# Home Assistant discovers these over Bluetooth, but cannot decrypt them
# without the bindkey, and the bindkey can only be entered in the UI.  Writing
# the entry here keeps it declarative: the key stays in agenix.
ensure_xiaomi_ble() {
  local mac key title
  while read -r mac key title; do
    [[ -n "$mac" && -n "$key" ]] || continue
    mac="${mac^^}"
    key="${key,,}"
    # MiBeacon v4/v5 keys are 32 hex characters, v2/v3 are 24.
    if [[ ! "$key" =~ ^([0-9a-f]{24}|[0-9a-f]{32})$ ]]; then
      log "xiaomi_ble: skipping $mac, bindkey is not 24 or 32 hex characters"
      continue
    fi
    if has_entry_uid xiaomi_ble "$mac"; then
      continue
    fi
    [[ -n "$title" ]] || title="Xiaomi BLE ${mac//:/}"
    log "adding xiaomi_ble device $mac"
    # The key goes through the environment, not argv, to keep it out of the
    # process list.
    add_entry xiaomi_ble "$title" \
      "$(bindkey="$key" jq -n '{bindkey: env.bindkey}')" \
      1 "[]" "$mac" "bluetooth"
  done < <(xiaomi_ble_devices)
}

remote_calendar_needed() {
  [[ -n "$GCAL_URL" ]] || return 1
  ! has_entry remote_calendar
}

# The house's Google Calendar: remote_calendar polls the iCal feed, and the
# who page's settings screens offer its calendar entity. Home Assistant
# fetches and parses the feed when the entry sets up, so a bad URL surfaces
# as a setup error in the logs rather than a silent no-op.
ensure_remote_calendar() {
  if [[ -z "$GCAL_URL" ]] || has_entry remote_calendar; then
    return 0
  fi
  log "adding remote_calendar entry for the house calendar"
  # The feed URL is a credential: it goes through the environment, not argv,
  # to keep it out of the process list.
  add_entry remote_calendar "House" \
    "$(url="$GCAL_URL" jq -n '{calendar_name: "House", url: env.url, verify_ssl: true}')"
}

ensure_areas() {
  if state_done areas || [[ ! -f "$AREA_REGISTRY" ]]; then
    [[ -f "$AREA_REGISTRY" ]] && mark_done areas
    return 0
  fi
  log "adding default areas"
  local tmp now new_areas name id
  now="$(now_utc)"
  new_areas='[]'
  for name in Kitchen "Living Room" Bedroom Bathroom Hall Office Garden; do
    id="$(area_id)"
    new_areas="$(jq --arg now "$now" --arg name "$name" --arg id "$id" \
      '. + [{
        aliases: [],
        floor_id: null,
        icon: null,
        id: $id,
        labels: [],
        name: $name,
        picture: null,
        humidity_entity_id: null,
        temperature_entity_id: null,
        created_at: $now,
        modified_at: $now
      }]' <<<"$new_areas")"
  done
  tmp="$(mktemp)"
  jq --argjson new_areas "$new_areas" '
    .data.areas = ($new_areas + (.data.areas // []) | unique_by(.name))
  ' "$AREA_REGISTRY" > "$tmp"
  install -o hass -g hass -m 0600 "$tmp" "$AREA_REGISTRY"
  rm "$tmp"
  mark_done areas
}

wyoming_needed() {
  local name="$1"
  state_done "wyoming-${name}" && return 1
  jq -e --arg title "$name" '.data.entries[] | select(.domain == "wyoming" and .title == $title)' "$CONFIG_ENTRIES" >/dev/null && return 1
  return 0
}

# ── Rooms for devices (settings.deviceAreas) ─────────────────────────────
# DEVICE_AREAS is {"<device name or entity id>": "<room>"}. A room with no
# area yet becomes one; each device named (or owning the entity named) goes
# in its room, on every run. Names compare case-insensitively.
DEVICE_AREAS="${DEVICE_AREAS:-}"
[[ -n "$DEVICE_AREAS" ]] || DEVICE_AREAS='{}'
ENTITY_REGISTRY="${HASS_CONFIG}/.storage/core.entity_registry"

# The rooms of DEVICE_AREAS that have no area yet.
device_areas_missing_rooms() {
  [[ -f "$AREA_REGISTRY" ]] || return 0
  jq -r --argjson want "$DEVICE_AREAS" '
    ([.data.areas[].name | ascii_downcase]) as $have
    | [$want[]] | unique[] | select((ascii_downcase) as $n | $have | index($n) | not)
  ' "$AREA_REGISTRY"
}

# The device registry with DEVICE_AREAS applied (to the area registry in $1);
# .unmatched lists the keys that name no device.
device_areas_wanted() {
  local areas="$1"
  jq --argjson want "$DEVICE_AREAS" --slurpfile areas "$areas" \
    --slurpfile ents "$ENTITY_REGISTRY" '
    ($areas[0].data.areas | map({key: (.name | ascii_downcase), value: .id}) | from_entries) as $ids
    | ([$ents[0].data.entities[] | select(.device_id != null) | {key: .entity_id, value: .device_id}]
       | from_entries) as $dev_of
    | ($want | to_entries) as $w
    | ([.data.devices[] | (.name_by_user // .name // "") | ascii_downcase]) as $names
    | def room_of($d):
        [$w[] | select((.key | ascii_downcase) == (($d.name_by_user // $d.name // "") | ascii_downcase)
                       or $dev_of[.key] == $d.id)] | .[0];
    .unmatched = [$w[] | .key
                  | select($dev_of[.] == null and ((ascii_downcase) as $k | $names | index($k) | not))]
    | .data.devices |= map(room_of(.) as $m
        | if $m != null and $ids[$m.value | ascii_downcase] != null
          then .area_id = $ids[$m.value | ascii_downcase] else . end)
  ' "$DEVICE_REGISTRY"
}

device_areas_needed() {
  [[ "$DEVICE_AREAS" != '{}' && -f "$DEVICE_REGISTRY" && -f "$ENTITY_REGISTRY" ]] || return 1
  [[ -n "$(device_areas_missing_rooms)" ]] && return 0
  [[ "$(device_areas_wanted "$AREA_REGISTRY" | jq -S '.data.devices')" \
     != "$(jq -S '.data.devices' "$DEVICE_REGISTRY")" ]]
}

ensure_device_areas() {
  device_areas_needed || return 0
  local tmp now room wanted key
  now="$(now_utc)"
  tmp="$(mktemp)"
  cp "$AREA_REGISTRY" "$tmp"
  while IFS= read -r room; do
    [[ -n "$room" ]] || continue
    log "adding area ${room}"
    jq --arg now "$now" --arg name "$room" --arg id "$(area_id)" '
      .data.areas += [{
        aliases: [], floor_id: null, icon: null, id: $id, labels: [], name: $name,
        picture: null, humidity_entity_id: null, temperature_entity_id: null,
        created_at: $now, modified_at: $now
      }]' "$tmp" > "$tmp.new"
    mv "$tmp.new" "$tmp"
  done < <(device_areas_missing_rooms)
  install -o hass -g hass -m 0600 "$tmp" "$AREA_REGISTRY"
  wanted="$(device_areas_wanted "$AREA_REGISTRY")"
  while IFS= read -r key; do
    [[ -n "$key" ]] && log "deviceAreas: no device or entity named ${key}"
  done < <(jq -r '.unmatched[]' <<< "$wanted")
  log "putting devices in their rooms"
  jq 'del(.unmatched)' <<< "$wanted" > "$tmp"
  install -o hass -g hass -m 0600 "$tmp" "$DEVICE_REGISTRY"
  rm -f "$tmp"
}

# ── Generated dashboards (pkgs/home-assistant-dashboards) ─────────────────
# The Overview (storage key "lovelace.lovelace", listed as "lovelace") and the
# lanbat-* dashboards are
# rewritten from the registries; other dashboards and the Energy dashboard's
# grid and solar sources are left alone.
HA_DASHBOARDS="${HA_DASHBOARDS:-}"
DASHBOARD_LINKS="${DASHBOARD_LINKS:-}"
[[ -n "$DASHBOARD_LINKS" ]] || DASHBOARD_LINKS='{}'
# The rooms with a screen (techo5-bridge), [{room, path}], and the people.
DASHBOARD_SCREENS="${DASHBOARD_SCREENS:-[]}"
DASHBOARD_PEOPLE="${DASHBOARD_PEOPLE:-{\}}"
STORAGE_DIR="${HASS_CONFIG}/.storage"
GENERATED_STORES=(lovelace.lovelace lovelace.lanbat-cameras lovelace.lanbat-system)
# A screen's dashboard is a store of its own.
while IFS= read -r path; do
  [[ -n "$path" ]] && GENERATED_STORES+=("lovelace.${path}")
done < <(jq -r '.[].path' <<< "$DASHBOARD_SCREENS")

# Generate into $1 and add the merged lovelace_dashboards and energy stores.
dashboards_render() {
  local out="$1"
  "$HA_DASHBOARDS" --storage "$STORAGE_DIR" --out "$out" --links "$DASHBOARD_LINKS" \
    --screens "$DASHBOARD_SCREENS" --people "$DASHBOARD_PEOPLE"
  if [[ -f "$STORAGE_DIR/lovelace_dashboards" ]]; then
    cat "$STORAGE_DIR/lovelace_dashboards"
  else
    echo '{"version": 1, "minor_version": 1, "key": "lovelace_dashboards", "data": {"items": []}}'
  fi | jq --slurpfile ours "$out/dashboards.json" '
    .data.items = ([.data.items[] | select(.id != "lovelace" and (.id | startswith("lanbat-") | not))] + $ours[0])
  ' > "$out/lovelace_dashboards"
  if [[ -f "$STORAGE_DIR/energy" ]]; then
    cat "$STORAGE_DIR/energy"
  else
    echo '{"version": 1, "minor_version": 3, "key": "energy", "data": {"energy_sources": [], "device_consumption": [], "device_consumption_water": []}}'
  fi | jq --slurpfile ours "$out/energy-devices.json" '
    ($ours[0] | map(.stat_consumption)) as $mine
    | .data.device_consumption = ($ours[0]
        + [(.data.device_consumption // [])[] | select(.stat_consumption as $s | $mine | index($s) | not)])
  ' > "$out/energy"
}

# Whether a generated store differs from Home Assistant's.
dashboards_differ() {
  local out="$1" name
  for name in "${GENERATED_STORES[@]}" lovelace_dashboards energy; do
    [[ -f "$STORAGE_DIR/$name" ]] || return 0
    [[ "$(jq -S . "$out/$name")" == "$(jq -S . "$STORAGE_DIR/$name")" ]] || return 0
  done
  return 1
}

dashboards_needed() {
  [[ -n "$HA_DASHBOARDS" && -f "$DEVICE_REGISTRY" && -f "$ENTITY_REGISTRY" ]] || return 1
  local out rc=1
  out="$(mktemp -d)"
  dashboards_render "$out" && dashboards_differ "$out" && rc=0
  rm -rf "$out"
  return "$rc"
}

ensure_dashboards() {
  [[ -n "$HA_DASHBOARDS" && -f "$DEVICE_REGISTRY" && -f "$ENTITY_REGISTRY" ]] || return 0
  local out name
  out="$(mktemp -d)"
  dashboards_render "$out"
  if dashboards_differ "$out"; then
    log "writing the generated dashboards"
    for name in "${GENERATED_STORES[@]}" lovelace_dashboards energy; do
      install -o hass -g hass -m 0600 "$out/$name" "$STORAGE_DIR/$name"
    done
  fi
  rm -rf "$out"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then

needs_work=false
[[ -f "$CONFIG_ENTRIES" ]] || { log "waiting for Home Assistant storage"; exit 0; }

mkdir -p "$STATE_DIR"
chown hass:hass "$STATE_DIR"
chmod 0700 "$STATE_DIR"

if ! state_done mqtt && ! has_entry mqtt; then needs_work=true; fi
if ! state_done frigate && ! has_entry frigate; then needs_work=true; fi
if ! state_done music_assistant && ! has_entry music_assistant; then needs_work=true; fi
if ! state_done areas && [[ -f "$AREA_REGISTRY" ]]; then needs_work=true; fi
for svc in openwakeword faster-whisper piper; do
  wyoming_needed "$svc" && needs_work=true
done
if [[ -n "$VOICE_ID_PORT" ]] && wyoming_needed voice-id; then needs_work=true; fi
if [[ -n "$VOICE_SATELLITE_REGISTRATIONS" ]]; then
  voice_satellite_registration_needed && needs_work=true
else
  wyoming_needed satellite && needs_work=true
  if [[ -n "$LOCAL_SATELLITE_PORT" ]] && wyoming_needed server-satellite; then needs_work=true; fi
  for entry in $EXTRA_SATELLITES; do
    wyoming_needed "satellite-${entry%%=*}" && needs_work=true
  done
fi
if kodi_needed; then needs_work=true; fi
if techo5_needed; then needs_work=true; fi
if dlna_needed; then needs_work=true; fi
if todo_lists_needed; then needs_work=true; fi
if androidtv_needed; then needs_work=true; fi
if satellite_areas_needed; then needs_work=true; fi
if llm_needed; then needs_work=true; fi
if ! state_done "$(pipeline_state_key)"; then needs_work=true; fi
if ! state_done "$(satellite_vad_state_key)"; then needs_work=true; fi
if voice_token_needed; then needs_work=true; fi
if bluetooth_needed; then needs_work=true; fi
if xiaomi_ble_needed; then needs_work=true; fi
if remote_calendar_needed; then needs_work=true; fi
if device_areas_needed; then needs_work=true; fi
if dashboards_needed; then needs_work=true; fi

if [[ "$needs_work" != true ]]; then
  log "post-setup already complete"
  exit 0
fi

log "applying home assistant post-setup changes"
systemctl stop home-assistant.service

ensure_mqtt
ensure_frigate
ensure_music_assistant
ensure_wyoming "openwakeword" "127.0.0.1" 10300
ensure_wyoming "faster-whisper" "127.0.0.1" 10301
if [[ -n "$VOICE_ID_PORT" ]]; then
  ensure_wyoming "voice-id" "127.0.0.1" "$VOICE_ID_PORT"
fi
ensure_wyoming "piper" "127.0.0.1" 10302
if [[ -n "$VOICE_SATELLITE_REGISTRATIONS" ]]; then
  ensure_voice_satellite_registrations
else
  ensure_wyoming "satellite" "$PI_HOST" 10700
  if [[ -n "$LOCAL_SATELLITE_PORT" ]]; then
    ensure_wyoming "server-satellite" "127.0.0.1" "$LOCAL_SATELLITE_PORT"
  fi
  for entry in $EXTRA_SATELLITES; do
    ensure_wyoming "satellite-${entry%%=*}" "${entry#*=}" 10700
  done
fi
ensure_kodi
ensure_androidtv
ensure_techo5
ensure_dlna_players
ensure_todo_lists
ensure_satellite_areas
ensure_llm
ensure_pipeline
ensure_satellite_vad
ensure_voice_token
ensure_bluetooth
ensure_xiaomi_ble
ensure_remote_calendar
ensure_areas
ensure_device_areas
ensure_dashboards

systemctl start home-assistant.service
log "post-setup complete"

fi
