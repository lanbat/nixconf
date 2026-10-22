# Reconcile default techo5 to-do lists (local_todo config entries) in setup-ha.sh.
{ pkgs }:

pkgs.runCommand "home-assistant-post-setup-todo-lists-check"
  {
    nativeBuildInputs = [
      pkgs.bash
      pkgs.jq
    ];
  }
  ''
    set -euo pipefail
    export MQTT_PASSWORD=test
    export PI_HOST=127.0.0.1
    export TECHO5_TODO_LISTS='["alex_todo","alex_reminders","sam_todo","sam_reminders"]'
    tmp="$(mktemp -d)"
    export HASS_CONFIG="$tmp"
    export CONFIG_ENTRIES="$HASS_CONFIG/.storage/core.config_entries"
    mkdir -p "$HASS_CONFIG/.storage"
    echo '{"version":1,"minor_version":1,"key":"core.config_entries","data":{"entries":[]}}' \
      > "$CONFIG_ENTRIES"

    # shellcheck source=../pkgs/home-assistant-post-setup/setup-ha.sh
    source ${../pkgs/home-assistant-post-setup/setup-ha.sh}

    missing="$(jq '(.missing // []) | length' <<< "$(todo_lists_reconciled)")"
    if [[ "$missing" != 4 ]]; then
      echo "expected 4 missing local_todo entries, got $missing" >&2
      exit 1
    fi

    for title in alex_todo alex_reminders sam_todo sam_reminders; do
      jq --arg title "$title" \
        '.data.entries += [{
          created_at: "2026-01-01T00:00:00.000000+00:00",
          data: {},
          disabled_by: null,
          discovery_keys: {},
          domain: "local_todo",
          entry_id: ("id-" + $title),
          minor_version: 1,
          modified_at: "2026-01-01T00:00:00.000000+00:00",
          options: {},
          pref_disable_new_entities: false,
          pref_disable_polling: false,
          source: "user",
          subentries: [],
          title: $title,
          unique_id: null,
          version: 1
        }]' "$CONFIG_ENTRIES" > "$CONFIG_ENTRIES.new"
      mv "$CONFIG_ENTRIES.new" "$CONFIG_ENTRIES"
    done

    missing="$(jq '(.missing // []) | length' <<< "$(todo_lists_reconciled)")"
    if [[ "$missing" != 0 ]]; then
      echo "expected 0 missing after seeding entries, got $missing" >&2
      exit 1
    fi
    if todo_lists_needed; then
      echo "todo_lists_needed should be false when every list exists" >&2
      exit 1
    fi

    touch "$out"
  ''
