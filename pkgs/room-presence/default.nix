# pkgs/room-presence/default.nix
#
# Whose phone is in the room, from Bluetooth LE advertisements, for the
# assistant router (room_presence.py, modules/pi/room-presence.nix,
# docs/people.md).
{ python3Packages }:

python3Packages.buildPythonApplication {
  pname = "room-presence";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 room_presence.py $out/bin/room-presence
  '';
  dependencies = [
    python3Packages.bleak
    python3Packages.cryptography
    python3Packages.websockets
  ];
  doCheck = false;
  meta = {
    description = "Recognise household phones over Bluetooth LE and tell the assistant router who is near";
    mainProgram = "room-presence";
  };
}
