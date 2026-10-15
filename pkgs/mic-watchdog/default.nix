# pkgs/mic-watchdog/default.nix
{
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "mic-watchdog";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 watchdog.py $out/bin/mic-watchdog
  '';
  doCheck = false;
  meta = {
    description = "Reopen a satellite's USB microphone when PipeWire's reads of it keep failing";
    mainProgram = "mic-watchdog";
  };
}
