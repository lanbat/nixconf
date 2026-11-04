{
  stdenv,
}:

stdenv.mkDerivation {
  pname = "kodi-tv-config";
  version = "1.0";

  src = ./.;

  installPhase = ''
    install -Dm644 advancedsettings.xml $out/advancedsettings.xml
    install -Dm644 sources.xml $out/sources.xml
    install -Dm644 keymaps/power-menu.xml $out/keymaps/power-menu.xml
    install -Dm644 keymaps/keyboard-layout.xml $out/keymaps/keyboard-layout.xml
  '';

  meta.description = "Seeded Kodi userdata for the Pi TV frontend";
}
