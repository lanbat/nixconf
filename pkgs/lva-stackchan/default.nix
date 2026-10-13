# pkgs/lva-stackchan/default.nix
#
# The Stack-chan robot as the face of a Linux Voice Assistant satellite
# (stackchan_bridge.py, modules/pi/stackchan.nix, docs/stackchan.md).
{
  lib,
  python3Packages,
  pipewire,
  openssl,
}:

python3Packages.buildPythonApplication {
  pname = "lva-stackchan";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 stackchan_bridge.py $out/bin/lva-stackchan
  '';
  dependencies = [
    python3Packages.websockets
    python3Packages.pyserial-asyncio-fast
  ];
  makeWrapperArgs = [
    "--set-default PW_RECORD ${pipewire}/bin/pw-record"
    # The Wi-Fi link's certificate, made on first start.
    "--set-default OPENSSL ${lib.getExe openssl}"
  ];
  doCheck = false;
  meta = {
    description = "Drive a Stack-chan robot from a Linux Voice Assistant satellite's events";
    mainProgram = "lva-stackchan";
  };
}
