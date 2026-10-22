# tests/jellyfin-dlna.nix
#
# jellyfin-bootstrap's DLNA step: the plugin's EnablePlayTo is set from
# DLNA_ENABLE, the rest of its configuration is left as it is, and a
# configuration without the key is left alone. The jq filter is taken from the
# script itself; the fixture holds the settings of DLNA 10.0.0.0's own
# configuration page (read from the plugin package), none of which is a server
# switch.
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
    eval "$(sed -n "/^DLNA_JQ='/,/^  (if has(\"EnablePlayTo\").*'\$/p" "$script")"
    [[ -n "$DLNA_JQ" ]] || { echo "DLNA_JQ not found in the bootstrap" >&2; exit 1; }

    apply() { jq -c --argjson play "$1" "$DLNA_JQ" <<<"$2"; }

    real='{"AliveMessageIntervalSeconds":1800,"BlastAliveMessages":true,"ClientDiscoveryIntervalSeconds":60,"DefaultUserId":null,"EnablePlayTo":false,"SendOnlyMatchedHost":true}'
    # Turned on: only that key changes.
    got="$(apply true "$real")"
    [[ "$got" == '{"AliveMessageIntervalSeconds":1800,"BlastAliveMessages":true,"ClientDiscoveryIntervalSeconds":60,"DefaultUserId":null,"EnablePlayTo":true,"SendOnlyMatchedHost":true}' ]] \
      || { echo "on: $got" >&2; exit 1; }
    # And off again.
    got="$(apply false '{"EnablePlayTo":true,"Other":1}')"
    [[ "$got" == '{"EnablePlayTo":false,"Other":1}' ]] || { echo "off: $got" >&2; exit 1; }
    # A key the plugin doesn't have is not made up.
    got="$(apply true '{"Other":1}')"
    [[ "$got" == '{"Other":1}' ]] || { echo "no key: $got" >&2; exit 1; }
    # The script installs the plugin only when DLNA is on, by the catalog's id.
    grep -q 'DLNA_GUID="33eba9cd-7da1-4720-967f-dd7dae7b74a1"' "$script"
    grep -q '"$DLNA_ENABLE" == 1 \]\] && install_plugin "DLNA" "$DLNA_GUID"' "$script"

    touch "$out"
  ''
