# pkgs/snapclient-watchdog/default.nix
{
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "snapclient-watchdog";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 watchdog.py $out/bin/snapclient-watchdog
  '';
  doCheck = false;
  meta = {
    description = "Restart snapclient when its connection to snapserver is gone, stale or stuck";
    mainProgram = "snapclient-watchdog";
  };
}
