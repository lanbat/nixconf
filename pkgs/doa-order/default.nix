# pkgs/doa-order/default.nix
#
# A one-liner to discover the microphone channel order for Stack-chan sound
# direction (pkgs/doa-order/doa_order.py, docs/stackchan.md).
{
  lib,
  python3Packages,
  pipewire,
}:

python3Packages.buildPythonApplication {
  pname = "doa-order";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 doa_order.py $out/bin/doa-order
  '';
  makeWrapperArgs = [
    "--set-default PW_RECORD ${pipewire}/bin/pw-record"
  ];
  doCheck = false;
  meta = {
    description = "Discover the microphone channel order for Stack-chan sound direction";
    mainProgram = "doa-order";
  };
}
