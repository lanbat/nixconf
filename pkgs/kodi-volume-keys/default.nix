# pkgs/kodi-volume-keys/default.nix
#
# Forward a TV keyboard's media volume keys to Kodi. Kodi's GBM evdev input
# grabs every /dev/input device but only maps its main keyboard; on keyboards
# whose volume keys are a separate HID "Consumer Control" interface, Kodi
# grabs the events and drops them. modules/storage/tv-box.nix keeps that
# device root-only so Kodi cannot grab it, and this service reads it and sends
# the action to Kodi's password-less TCP JSON-RPC on port 9090.
{
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "kodi-volume-keys";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 volume_keys.py $out/bin/kodi-volume-keys
  '';
  dependencies = [ python3Packages.evdev ];
  doCheck = false;
  meta.description = "Forward a TV keyboard's media volume keys to Kodi";
  meta.mainProgram = "kodi-volume-keys";
}
