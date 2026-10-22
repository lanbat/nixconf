# tests/satellite-camera.nix
#
# The satellite camera client's gate and messages (pkgs/satellite-camera): see
# tests/satellite-camera.py. The package is built too, so its dependencies resolve.
{ pkgs }:

let
  camera = pkgs.callPackage ../pkgs/satellite-camera { };
  python = pkgs.python3.withPackages (p: [ p.websockets ]);
in
pkgs.runCommand "satellite-camera-check" { nativeBuildInputs = [ python ]; } ''
  python3 ${./satellite-camera.py} ${../pkgs/satellite-camera}
  test -x ${camera}/bin/satellite-camera
  touch $out
''
