"""The TV box power menu.

Kodi opens this instead of its own shutdown menu (keymaps/power-menu.xml
overrides the power key). Each item is a Kodi builtin that runs as the media
user; the two session switches call tv-switch, which the polkit rule in
modules/storage/tv-box.nix lets that user run.
"""

import xbmc
import xbmcgui

# (label, builtin to run)
ITEMS = [
    ("Restart Kodi", "RestartApp"),
    ("Screen off (CEC)", "CECStandby"),
    ("EmulationStation", 'System.Exec("/run/current-system/sw/bin/tv-switch games")'),
    ("chiaki-ng", 'System.Exec("/run/current-system/sw/bin/tv-switch chiaki")'),
    ("Reboot", "Reboot"),
]

choice = xbmcgui.Dialog().select("Power", [label for label, _ in ITEMS])
if choice >= 0:
    xbmc.executebuiltin(ITEMS[choice][1])
