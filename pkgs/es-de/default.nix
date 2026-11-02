# pkgs/es-de/default.nix
#
# ES-DE (EmulationStation Desktop Edition), the game browser of the Pi's TV
# games session (modules/storage/tv-box.nix). nixpkgs removed ES-DE together with
# FreeImage, which ES-DE still needs, so this wraps the upstream AArch64
# AppImage, which bundles its own libraries.
#
# passthru.systems is ES-DE's bundled Linux system list; modules/storage/tv-box.nix
# generates its emulator overrides from it.
{
  appimageTools,
  fetchurl,
}:

let
  pname = "es-de";
  version = "3.5.0";

  # "ES-DE_aarch64.AppImage" of https://gitlab.com/es-de/emulationstation-de/-/releases/v3.5.0
  src = fetchurl {
    url = "https://gitlab.com/es-de/emulationstation-de/-/package_files/357718204/download";
    name = "ES-DE_aarch64.AppImage";
    hash = "sha256-ttNGV4QtuJYo1bxC7UZbYFEItFyx0WsVMy1sn2ATaks=";
  };

  extracted = appimageTools.extract { inherit pname version src; };
in
appimageTools.wrapType2 {
  inherit pname version src;

  passthru.systems = "${extracted}/usr/share/es-de/resources/systems/linux/es_systems.xml";

  meta = {
    description = "Frontend for browsing and launching games in emulators";
    homepage = "https://es-de.org/";
    platforms = [ "aarch64-linux" ];
    mainProgram = "es-de";
  };
}
