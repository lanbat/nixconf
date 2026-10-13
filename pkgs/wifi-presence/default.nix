# pkgs/wifi-presence/default.nix
#
# Who is home, from the household's phones on the LAN, for the assistant
# router (wifi_presence.py, services/wifi-presence.nix, docs/people.md).
{ python3Packages }:

python3Packages.buildPythonApplication {
  pname = "wifi-presence";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 wifi_presence.py $out/bin/wifi-presence
  '';
  dependencies = [ python3Packages.websockets ];
  doCheck = false;
  meta = {
    description = "Tell the assistant router whose phones are on the home network";
    mainProgram = "wifi-presence";
  };
}
