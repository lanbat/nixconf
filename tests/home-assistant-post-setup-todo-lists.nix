# Reconcile default techo5 to-do lists (local_todo config entries) in setup-ha.sh.
{ pkgs }:

pkgs.runCommand "home-assistant-post-setup-todo-lists-check"
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
    export TECHO5_TODO_LISTS='["alex_todo","alex_reminders","sam_todo","sam_reminders"]'
    tmp="$(mktemp -d)"
    export HASS_CONFIG="$tmp"
    export CONFIG_ENTRIES="$HASS_CONFIG/.storage/core.config_entries"
    mkdir -p "$HASS_CONFIG/.storage"
    echo '{"version":1,"minor_version":1,"key":"core.config_entries","data":{"entries":[]}}' \
      > "$CONFIG_ENTRIES"

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

    missing="$(jq '(.missing // []) | length' <<< "$(todo_lists_reconciled)")"
    if [[ "$missing" != 4 ]]; then
      echo "expected 4 missing local_todo entries, got $missing" >&2
      exit 1
    fi

    ensure_todo_lists
    count="$(jq '[.data.entries[] | select(.domain == "local_todo")] | length' "$CONFIG_ENTRIES")"
    if [[ "$count" != 4 ]]; then
      echo "ensure_todo_lists: expected 4 local_todo entries, got $count" >&2
      exit 1
    fi

    for title in alex_todo alex_reminders sam_todo sam_reminders; do
      if ! jq -e --arg title "$title" '
        .data.entries[]
        | select(.domain == "local_todo" and .title == $title)
        | .data
        | . == {todo_list_name: $title, storage_key: $title}
      ' "$CONFIG_ENTRIES" >/dev/null; then
        echo "local_todo entry for $title has wrong title or data" >&2
        jq --arg title "$title" '.data.entries[] | select(.title == $title)' "$CONFIG_ENTRIES" >&2
        exit 1
      fi
    done

    ensure_todo_lists
    count2="$(jq '[.data.entries[] | select(.domain == "local_todo")] | length' "$CONFIG_ENTRIES")"
    if [[ "$count2" != 4 ]]; then
      echo "second ensure_todo_lists: expected 4 entries, got $count2" >&2
      exit 1
    fi
    if todo_lists_needed; then
      echo "todo_lists_needed should be false after ensure_todo_lists" >&2
      exit 1
    fi

    # People's own lists, from the router's file: made once, bad names never.
    export LISTS_FILE="$tmp/lists.json"
    echo '{"alex":["todo","shopping"],"sam":["hardware"],"Bad Key":["x"],"alex_2":["a/b","UP",7]}' > "$LISTS_FILE"
    if [[ "$(wanted_todo_lists)" != '["alex_reminders","alex_shopping","alex_todo","sam_hardware","sam_reminders","sam_todo"]' ]]; then
      echo "wanted_todo_lists: $(wanted_todo_lists)" >&2
      exit 1
    fi
    ensure_todo_lists
    count3="$(jq '[.data.entries[] | select(.domain == "local_todo")] | length' "$CONFIG_ENTRIES")"
    if [[ "$count3" != 6 ]]; then
      echo "lists.json: expected 6 entries, got $count3" >&2
      exit 1
    fi
    if ! jq -e '.data.entries[] | select(.domain == "local_todo" and .title == "alex_shopping")' "$CONFIG_ENTRIES" >/dev/null; then
      echo "alex_shopping was not made" >&2
      exit 1
    fi
    if todo_lists_needed; then
      echo "todo_lists_needed should be false after the router's lists are made" >&2
      exit 1
    fi
    # A file that isn't JSON, or isn't an object, adds nothing and breaks nothing.
    echo 'nonsense' > "$LISTS_FILE"
    [[ "$(wanted_todo_lists)" == '["alex_reminders","alex_todo","sam_reminders","sam_todo"]' ]]
    echo '[1,2]' > "$LISTS_FILE"
    [[ "$(wanted_todo_lists)" == '["alex_reminders","alex_todo","sam_reminders","sam_todo"]' ]]

    touch "$out"
  ''
