# pkgs/snapcast-client-latency/default.nix
{
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "snapcast-client-latency";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 latency.py $out/bin/snapcast-client-latency
  '';
  doCheck = false;
  meta = {
    description = "Apply per-client latency offsets to a running snapserver";
    mainProgram = "snapcast-client-latency";
  };
}
