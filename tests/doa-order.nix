# tests/doa-order.nix
#
# The microphone channel-order discovery tool (pkgs/doa-order): see
# tests/doa_order.py for what is checked. The package is built too, so its
# dependencies (python3, pipewire's pw-record) resolve.
{ pkgs }:

let
  tool = pkgs.callPackage ../pkgs/doa-order { };
in
pkgs.runCommand "doa-order-check" { nativeBuildInputs = [ pkgs.python3 ]; } ''
  python3 ${./doa_order.py} ${../pkgs/doa-order}
  test -x ${tool}/bin/doa-order
  touch $out
''
