{
  stdenv,
  kodi-gbm,
}:

# Estuary with its Power buttons rerouted to the TV box's own menu. Installed
# as a user add-on at ~/.kodi/addons/skin.estuary (modules/storage/tv-box.nix),
# where Kodi prefers it over the bundled copy, so the home-screen Power button
# opens script.tv-power instead of Kodi's stock shutdown menu. Built from the
# same kodi-gbm as the system, so it never drifts from the skin Kodi ships.
stdenv.mkDerivation {
  pname = "kodi-skin-estuary-power";
  version = "1.0";

  src = "${kodi-gbm}/share/kodi/addons/skin.estuary";

  installPhase = ''
    cp -r $src $out
    chmod -R u+w $out
    # Home.xml (home menu) and Includes.xml (bottom bar) carry the Power
    # buttons. --replace-fail so a skin change that moves them breaks the
    # build instead of silently leaving the stock menu.
    substituteInPlace $out/xml/Home.xml \
      --replace-fail 'ActivateWindow(shutdownmenu)' 'RunScript(script.tv-power)'
    substituteInPlace $out/xml/Includes.xml \
      --replace-fail 'ActivateWindow(shutdownmenu)' 'RunScript(script.tv-power)'
  '';

  meta.description = "Estuary skin whose Power buttons open script.tv-power";
}
