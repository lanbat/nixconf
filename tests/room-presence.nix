# tests/room-presence.nix
#
# The room-presence scanner's recognition and reporting (pkgs/room-presence):
# see tests/room-presence.py. The package is built too, so its dependencies
# resolve.
{ pkgs }:

let
  scanner = pkgs.callPackage ../pkgs/room-presence { };
  python = pkgs.python3.withPackages (p: [ p.cryptography ]);
in
pkgs.runCommand "room-presence-check" { nativeBuildInputs = [ python ]; } ''
  python3 ${./room-presence.py} ${../pkgs/room-presence}
  test -x ${scanner}/bin/room-presence
  touch $out
''
