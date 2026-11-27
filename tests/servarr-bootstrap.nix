# tests/servarr-bootstrap.nix
#
# The Radarr/Sonarr bootstrap (pkgs/servarr-bootstrap) run twice against a mock
# app: see tests/servarr-bootstrap.py.
{ pkgs }:

let
  bootstrap = pkgs.callPackage ../pkgs/servarr-bootstrap { };
in
pkgs.runCommand "servarr-bootstrap-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  python3 ${./servarr-bootstrap.py} ${bootstrap}/bin/servarr-bootstrap
  touch $out
''
