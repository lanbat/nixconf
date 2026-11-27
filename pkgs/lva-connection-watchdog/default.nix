# pkgs/lva-connection-watchdog/default.nix
{
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "lva-connection-watchdog";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 watchdog.py $out/bin/lva-connection-watchdog
  '';
  doCheck = false;
  meta = {
    description = "Restart Linux Voice Assistant when it stays disconnected from Home Assistant";
    mainProgram = "lva-connection-watchdog";
  };
}
