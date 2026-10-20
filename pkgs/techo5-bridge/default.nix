# pkgs/techo5-bridge
#
# Echo Shows running TECHO5 as bodies of the assistant router: who is in front
# of them, faces for recognition and enrolment, greetings, the room's people's
# own screens, the sign-in code, the screen off in an empty room, and the
# household's phones in what their Bluetooth hears. See
# services/techo5-bridge.nix and docs/techo5.md.
{ python3Packages }:

python3Packages.buildPythonApplication {
  pname = "techo5-bridge";
  version = "1.0";
  pyproject = true;
  src = ./.;
  # Room presence's phone recognition (pkgs/room-presence), as a module of its own.
  postPatch = ''
    cp ${../room-presence/room_presence.py} room_presence.py
  '';
  build-system = [ python3Packages.setuptools ];
  dependencies = [
    python3Packages.aioesphomeapi
    python3Packages.aiohttp
    python3Packages.cryptography
    python3Packages.segno
  ];
  nativeCheckInputs = [
    python3Packages.pytestCheckHook
    python3Packages.pytest-asyncio
  ];
  meta.mainProgram = "techo5-bridge";
}
