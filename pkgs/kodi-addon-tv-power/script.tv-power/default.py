"""The TV box power menu.

Kodi opens this instead of its own shutdown menu (keymaps/power-menu.xml
overrides the power key). Each item is a Kodi builtin that runs as the media
user; the two session switches call tv-switch, which the polkit rule in
modules/storage/tv-box.nix lets that user run.

System.Exec does not run a shell: Kodi splits its parameter on commas and
hands the pieces to execvp, so `System.Exec("<path> games")` looks for a
program whose name contains a space and silently does nothing. Each session
therefore has its own one-word wrapper (tv-switch-games, tv-switch-chiaki),
like the ones modules/storage/tv-box.nix installs.
"""

import xbmc
import xbmcgui

# (label, builtin to run)
ITEMS = [
    ("Restart Kodi", "RestartApp"),
    ("Screen off (CEC)", "CECStandby"),
    ("EmulationStation", 'System.Exec("/run/current-system/sw/bin/tv-switch-games")'),
    ("chiaki-ng", 'System.Exec("/run/current-system/sw/bin/tv-switch-chiaki")'),
    ("Reboot", "Reboot"),
]

choice = xbmcgui.Dialog().select("Power", [label for label, _ in ITEMS])
if choice >= 0:
    xbmc.executebuiltin(ITEMS[choice][1])
