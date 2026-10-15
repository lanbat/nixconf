# tests/mic-watchdog.nix
#
# The microphone watchdog (pkgs/mic-watchdog): see tests/mic-watchdog.py. The
# package is built too.
{ pkgs }:

let
  service = pkgs.callPackage ../pkgs/mic-watchdog { };
in
pkgs.runCommand "mic-watchdog-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  python3 ${./mic-watchdog.py} ${../pkgs/mic-watchdog}
  test -x ${service}/bin/mic-watchdog
  touch $out
''
