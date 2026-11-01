{
  lib,
  buildKodiAddon,
}:

# The TV box's own power menu (script.tv-power). It replaces Kodi's shutdown
# menu, whose stock items we do not want: Restart Kodi, Screen off (CEC),
# EmulationStation, chiaki-ng, Reboot. Opened by the power key through
# kodi-tv-config/keymaps/power-menu.xml (Kodi maps <power> to
# ActivateWindow(ShutdownMenu) by default), and seeded as a favourite so it is
# always reachable from the UI.
buildKodiAddon rec {
  pname = "tv-power";
  namespace = "script.tv-power";
  version = "1.0.0";

  src = ./.;

  meta = {
    description = "Power menu for the TV box: restart Kodi, screen off over CEC, switch sessions, reboot";
    license = lib.licenses.mit;
    maintainers = [ ];
  };
}
