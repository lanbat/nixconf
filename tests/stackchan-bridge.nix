# tests/stackchan-bridge.nix
#
# The Stack-chan bridge's decisions (pkgs/lva-stackchan): see
# tests/stackchan-bridge.py for what is checked. The package is built too, so
# its dependencies resolve.
{ pkgs }:

let
  bridge = pkgs.callPackage ../pkgs/lva-stackchan { };
in
pkgs.runCommand "stackchan-bridge-check"
  {
    nativeBuildInputs = [
      pkgs.python3
      pkgs.openssl # the Wi-Fi link's certificate
    ];
  }
  ''
    python3 ${./stackchan-bridge.py} ${../pkgs/lva-stackchan}
    test -x ${bridge}/bin/lva-stackchan
    touch $out
  ''
