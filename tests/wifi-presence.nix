# tests/wifi-presence.nix
#
# The wifi-presence service's parts (pkgs/wifi-presence): see
# tests/wifi-presence.py. The package is built too, so its dependencies
# resolve.
{ pkgs }:

let
  service = pkgs.callPackage ../pkgs/wifi-presence { };
in
pkgs.runCommand "wifi-presence-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  python3 ${./wifi-presence.py} ${../pkgs/wifi-presence}
  test -x ${service}/bin/wifi-presence
  touch $out
''
