# tests/lva-connection-watchdog.nix
#
# The connection watchdog (pkgs/lva-connection-watchdog): see
# tests/lva-connection-watchdog.py. The decision runs without systemd.
{ pkgs }:

let
  service = pkgs.callPackage ../pkgs/lva-connection-watchdog { };
in
pkgs.runCommand "lva-connection-watchdog-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  python3 ${./lva-connection-watchdog.py} ${../pkgs/lva-connection-watchdog}
  test -x ${service}/bin/lva-connection-watchdog
  touch $out
''
