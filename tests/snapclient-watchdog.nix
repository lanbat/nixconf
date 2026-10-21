# tests/snapclient-watchdog.nix
#
# The snapclient watchdog (pkgs/snapclient-watchdog): see
# tests/snapclient-watchdog.py. The package is built too.
{ pkgs }:

let
  service = pkgs.callPackage ../pkgs/snapclient-watchdog { };
in
pkgs.runCommand "snapclient-watchdog-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  python3 ${./snapclient-watchdog.py} ${../pkgs/snapclient-watchdog}
  test -x ${service}/bin/snapclient-watchdog
  touch $out
''
