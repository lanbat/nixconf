# tests/home-assistant-post-setup-dlna.nix
#
# home-assistant-post-setup adds Home Assistant's DLNA player (dlna_dmr) for each
# Echo Show with dlna on, from the device's own description, and puts it in the
# device's room. A fake device serves TECHO5's device.xml from a local HTTP
# server: the entry has the manual flow's data, it is made once, a device that
# is off or has dlna off gets none, and the area step reaches the DLNA device.
{ pkgs }:

pkgs.runCommand "home-assistant-post-setup-dlna-check"
  {
    nativeBuildInputs = [
      pkgs.bash
      pkgs.jq
      pkgs.curl
      pkgs.openssl
      pkgs.python3
    ];
  }
  ''
    set -euo pipefail
    export MQTT_PASSWORD=test
    export PI_HOST=127.0.0.1
    tmp="$(mktemp -d)"
    mkdir -p "$tmp/www/dlna" "$tmp/hass/.storage"

    # What TECHO5 serves (echod/internal/feature/dlna/http.go), with an entity in the name.
    cat > "$tmp/www/dlna/device.xml" <<'XML'
    <root xmlns="urn:schemas-upnp-org:device-1-0"><specVersion><major>1</major><minor>0</minor></specVersion><device><deviceType>urn:schemas-upnp-org:device:MediaRenderer:1</deviceType><friendlyName>Echo &amp; Show 8</friendlyName><manufacturer>TECHO5</manufacturer><UDN>uuid:11111111-2222-3333-4444-555555555555</UDN></device></root>
    XML
    export DLNA_PORT=18181
    python3 -m http.server "$DLNA_PORT" --bind 127.0.0.1 --directory "$tmp/www" >/dev/null 2>&1 &
    server=$!
    trap 'kill $server' EXIT
    for _ in $(seq 1 50); do curl -fs "http://127.0.0.1:$DLNA_PORT/dlna/device.xml" >/dev/null && break; sleep 0.1; done

    export HASS_CONFIG="$tmp/hass"
    export CONFIG_ENTRIES="$HASS_CONFIG/.storage/core.config_entries"
    echo '{"version":1,"minor_version":1,"key":"core.config_entries","data":{"entries":[]}}' > "$CONFIG_ENTRIES"
    # A device on, one with dlna off, and one that is off (nothing listens at its address).
    export TECHO5_DEVICES='[
      {"title":"TECHO5 Kitchen","host":"127.0.0.1","room":"Kitchen","key_file":"/x","player":"Echo & Show 8","dlna":true},
      {"title":"TECHO5 Den","host":"127.0.0.2","room":"Den","key_file":"/x","player":"","dlna":false},
      {"title":"TECHO5 Attic","host":"127.0.0.3","room":"Attic","key_file":"/x","player":"","dlna":true}]'

    # shellcheck source=../pkgs/home-assistant-post-setup/setup-ha.sh
    source ${../pkgs/home-assistant-post-setup/setup-ha.sh}

    install() {
      local mode=0600
      local args=()
      while [[ $# -gt 0 ]]; do
        case "$1" in
          -o|-g) shift 2 ;;
          -m) mode="$2"; shift 2 ;;
          *) args+=("$1"); shift ;;
        esac
      done
      command install -m "$mode" "''${args[@]}"
    }

    dlna_needed || { echo "dlna_needed should be true before the player is added" >&2; exit 1; }
    ensure_dlna_players
    count="$(jq '[.data.entries[] | select(.domain == "dlna_dmr")] | length' "$CONFIG_ENTRIES")"
    [[ "$count" == 1 ]] || { echo "expected one dlna_dmr entry, got $count" >&2; exit 1; }
    jq -e '.data.entries[] | select(.domain == "dlna_dmr")
      | .title == "Echo & Show 8"
        and .unique_id == "uuid:11111111-2222-3333-4444-555555555555"
        and .version == 1 and .source == "user"
        and .data == {url: "http://127.0.0.1:18181/dlna/device.xml",
                      device_id: "uuid:11111111-2222-3333-4444-555555555555",
                      type: "urn:schemas-upnp-org:device:MediaRenderer:1", mac: null}
        and .options == {poll_availability: true}' "$CONFIG_ENTRIES" >/dev/null \
      || { echo "wrong dlna_dmr entry:" >&2; jq '.data.entries' "$CONFIG_ENTRIES" >&2; exit 1; }
    if dlna_needed; then echo "dlna_needed should be false once it is added" >&2; exit 1; fi
    ensure_dlna_players
    count="$(jq '[.data.entries[] | select(.domain == "dlna_dmr")] | length' "$CONFIG_ENTRIES")"
    [[ "$count" == 1 ]] || { echo "a second run made another: $count" >&2; exit 1; }

    # Once Home Assistant has loaded it, its device goes in the room of the device it belongs to.
    entry="$(jq -r '.data.entries[] | select(.domain == "dlna_dmr") | .entry_id' "$CONFIG_ENTRIES")"
    echo '{"version":1,"minor_version":1,"key":"core.area_registry","data":{"areas":[{"id":"kitchen_id","name":"Kitchen","aliases":[],"floor_id":null,"icon":null,"labels":[],"picture":null,"humidity_entity_id":null,"temperature_entity_id":null,"created_at":"x","modified_at":"x"}]}}' > "$AREA_REGISTRY"
    jq -n --arg e "$entry" '{"version":1,"minor_version":1,"key":"core.device_registry","data":{"devices":[
      {"id":"dlna_dev","name":"Echo & Show 8","config_entries":[$e],"area_id":null},
      {"id":"other_dev","name":"A lamp","config_entries":["zzz"],"area_id":null}],"deleted_devices":[]}}' > "$DEVICE_REGISTRY"
    got="$(satellite_areas_wanted)"
    [[ "$(jq -r '.data.devices[] | select(.id == "dlna_dev") | .area_id' <<< "$got")" == kitchen_id ]] \
      || { echo "the DLNA player was not put in the Kitchen" >&2; jq '.data.devices' <<< "$got" >&2; exit 1; }
    [[ "$(jq -r '.data.devices[] | select(.id == "other_dev") | .area_id' <<< "$got")" == null ]] \
      || { echo "another device got an area" >&2; exit 1; }

    touch "$out"
  ''
