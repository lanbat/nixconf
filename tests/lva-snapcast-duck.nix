# tests/lva-snapcast-duck.nix
#
# The duck service's voice boost (pkgs/lva-snapcast-duck): see
# tests/lva-snapcast-duck.py. The package is built too, so its dependencies
# resolve.
{ pkgs }:

let
  service = pkgs.callPackage ../pkgs/lva-snapcast-duck { };
in
pkgs.runCommand "lva-snapcast-duck-check"
  { nativeBuildInputs = [ (pkgs.python3.withPackages (p: [ p.websockets ])) ]; }
  ''
    python3 ${./lva-snapcast-duck.py} ${../pkgs/lva-snapcast-duck}
    test -x ${service}/bin/lva-snapcast-duck
    touch $out
  ''
