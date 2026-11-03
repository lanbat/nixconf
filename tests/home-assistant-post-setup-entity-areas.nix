# tests/home-assistant-post-setup-entity-areas.nix
#
# setup-ha.sh clears an entity's own area when it has no name of its own (so it
# represents its device) and its device already carries an area: Home Assistant
# requires the area to sit on the device, not the entity. Entities that do have
# a name, or whose device has no area, keep their area.
{ pkgs }:

pkgs.runCommand "home-assistant-post-setup-entity-areas-check"
  {
    nativeBuildInputs = [
      pkgs.bash
      pkgs.jq
      pkgs.openssl
    ];
  }
  ''
    set -euo pipefail
    export MQTT_PASSWORD=test
    export PI_HOST=127.0.0.1
    tmp="$(mktemp -d)"
    export HASS_CONFIG="$tmp/hass"
    mkdir -p "$HASS_CONFIG/.storage"

    cat > "$HASS_CONFIG/.storage/core.entity_registry" <<'JSON'
    {"version":1,"minor_version":24,"key":"core.entity_registry","data":{"entities":[
      {"id":"e1","entity_id":"media_player.binturong","unique_id":"ma_binturong","platform":"music_assistant","config_entry_id":"ma","device_id":"dev_binturong","name":null,"original_name":null,"area_id":"bedroom","disabled_by":null,"has_entity_name":true},
      {"id":"e2","entity_id":"media_player.box_r_4k_plus","unique_id":"ma_box","platform":"music_assistant","config_entry_id":"ma","device_id":"dev_box","name":null,"original_name":null,"area_id":"living","disabled_by":null,"has_entity_name":true},
      {"id":"e3","entity_id":"media_player.named_player","unique_id":"ma_named","platform":"music_assistant","config_entry_id":"ma","device_id":"dev_named","name":null,"original_name":"Named Player","area_id":"bedroom","disabled_by":null,"has_entity_name":true},
      {"id":"e4","entity_id":"media_player.user_named","unique_id":"ma_usernamed","platform":"music_assistant","config_entry_id":"ma","device_id":"dev_named","name":"User Named","original_name":null,"area_id":"bedroom","disabled_by":null,"has_entity_name":true},
      {"id":"e5","entity_id":"media_player.no_device_area","unique_id":"ma_orphan","platform":"music_assistant","config_entry_id":"ma","device_id":"dev_orphan","name":null,"original_name":null,"area_id":"office","disabled_by":null,"has_entity_name":true},
      {"id":"e6","entity_id":"media_player.area_no_device","unique_id":"ma_nodev","platform":"music_assistant","config_entry_id":"ma","device_id":null,"name":null,"original_name":null,"area_id":"office","disabled_by":null,"has_entity_name":true}
    ],"deleted_entities":[]}}
    JSON

    cat > "$HASS_CONFIG/.storage/core.device_registry" <<'JSON'
    {"version":1,"minor_version":3,"key":"core.device_registry","data":{"devices":[
      {"id":"dev_binturong","name":"binturong","area_id":"bedroom","config_entry_id":"ma"},
      {"id":"dev_box","name":"Box R 4K Plus","area_id":"living","config_entry_id":"ma"},
      {"id":"dev_named","name":"Named Device","area_id":"bedroom","config_entry_id":"ma"},
      {"id":"dev_orphan","name":"Orphan","area_id":null,"config_entry_id":"ma"}
    ],"deleted_devices":[]}}
    JSON

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

    entity_areas_needed || { echo "entity_areas_needed should be true before clearing" >&2; exit 1; }

    ensure_entity_areas_cleared

    area() {
      jq -r --arg id "$1" '.data.entities[] | select(.id == $id) | .area_id // "null"' \
        "$HASS_CONFIG/.storage/core.entity_registry"
    }

    # The two device-representing entities lose their own area (their device
    # already has it), the rest keep theirs.
    [[ "$(area e1)" == "null" ]] || { echo "binturong kept its own area" >&2; exit 1; }
    [[ "$(area e2)" == "null" ]] || { echo "box_r_4k_plus kept its own area" >&2; exit 1; }
    [[ "$(area e3)" == "bedroom" ]] || { echo "named player lost its area" >&2; exit 1; }
    [[ "$(area e4)" == "bedroom" ]] || { echo "user-named player lost its area" >&2; exit 1; }
    [[ "$(area e5)" == "office" ]] || { echo "entity whose device has no area lost its area" >&2; exit 1; }
    [[ "$(area e6)" == "office" ]] || { echo "entity without a device lost its area" >&2; exit 1; }

    if entity_areas_needed; then
      echo "entity_areas_needed should be false after ensure_entity_areas_cleared" >&2
      exit 1
    fi

    # Idempotent: a second run changes nothing.
    ensure_entity_areas_cleared
    [[ "$(area e1)" == "null" ]] || { echo "second run changed binturong" >&2; exit 1; }
    [[ "$(area e3)" == "bedroom" ]] || { echo "second run changed the named player" >&2; exit 1; }

    touch "$out"
  ''
