# tests/jellyfin-dlna.nix
#
# jellyfin-bootstrap's DLNA step: the plugin's configuration gets its two
# switches set from DLNA_ENABLE and DLNA_SERVER, and a configuration without
# the keys is left alone. The jq filter is taken from the script itself.
{ pkgs }:

pkgs.runCommand "jellyfin-dlna-check"
  {
    nativeBuildInputs = [
      pkgs.bash
      pkgs.jq
    ];
  }
  ''
    set -euo pipefail
    script=${../pkgs/jellyfin-bootstrap/bootstrap-jellyfin.sh}
    # The assignment DLNA_JQ='...' up to its closing quote line.
    eval "$(sed -n "/^DLNA_JQ='/,/^  | (if has(\"EnableServer\").*'\$/p" "$script")"
    [[ -n "$DLNA_JQ" ]] || { echo "DLNA_JQ not found in the bootstrap" >&2; exit 1; }

    apply() { jq -c --argjson play "$1" --argjson server "$2" "$DLNA_JQ" <<<"$3"; }

    # Play-to on, server off, the rest of the configuration untouched.
    got="$(apply true false '{"EnablePlayTo":false,"EnableServer":true,"BlastAliveMessages":true}')"
    [[ "$got" == '{"EnablePlayTo":true,"EnableServer":false,"BlastAliveMessages":true}' ]] \
      || { echo "on: $got" >&2; exit 1; }
    # Both off again.
    got="$(apply false false '{"EnablePlayTo":true,"EnableServer":true}')"
    [[ "$got" == '{"EnablePlayTo":false,"EnableServer":false}' ]] || { echo "off: $got" >&2; exit 1; }
    # A key the plugin doesn't have is not made up.
    got="$(apply true true '{"EnableServer":false,"Other":1}')"
    [[ "$got" == '{"EnableServer":true,"Other":1}' ]] || { echo "one key: $got" >&2; exit 1; }
    got="$(apply true true '{"Other":1}')"
    [[ "$got" == '{"Other":1}' ]] || { echo "no keys: $got" >&2; exit 1; }
    # The script installs the plugin only when DLNA is on, by the catalog's id.
    grep -q 'DLNA_GUID="33eba9cd-7da1-4720-967f-dd7dae7b74a1"' "$script"
    grep -q '"$DLNA_ENABLE" == 1 \]\] && install_plugin "DLNA" "$DLNA_GUID"' "$script"

    touch "$out"
  ''
