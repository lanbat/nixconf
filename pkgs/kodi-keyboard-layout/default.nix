# pkgs/kodi-keyboard-layout/default.nix
#
# Keep Kodi's physical keyboard layout in step with its on-screen keyboard.
# Kodi switches locale.activekeyboardlayout when the on-screen layout button is
# pressed but never touches input.libinputkeyboardlayout, which is what its
# libinput keyboard uses. This service follows the active layout and sets the
# physical one over Kodi's password-less TCP JSON-RPC (port 9090); Kodi applies
# it live through libinput.
{
  python3Packages,
}:

python3Packages.buildPythonApplication {
  pname = "kodi-keyboard-layout";
  version = "1.0";
  format = "other";
  src = ./.;
  dontBuild = true;
  installPhase = ''
    install -Dm755 keyboard_layout.py $out/bin/kodi-keyboard-layout
  '';
  doCheck = false;
  meta.description = "Follow Kodi's on-screen keyboard layout with the physical keyboard";
  meta.mainProgram = "kodi-keyboard-layout";
}
