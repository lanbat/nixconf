"""Delete a video from the private folder, after a confirmation.

Registered as a context-menu item (addon.xml). It only appears on files under
PRIVATE_ROOT, and this file refuses anything else, so the film and music
libraries keep the add-on's hands off them.
"""

import os
import sys

import xbmc
import xbmcgui

PRIVATE_ROOT = "/mnt/storage-b/media/adult/"


def notify(message):
    xbmcgui.Dialog().notification("Private media", message)


try:
    path = sys.listitem.getPath()
except AttributeError:
    # Started outside a context menu (no ListItem to read).
    notify("Select a file first.")
    sys.exit(0)

if not path.startswith(PRIVATE_ROOT) or not os.path.isfile(path):
    notify("That is not a file in the private folder.")
    sys.exit(0)

name = os.path.basename(path)
if not xbmcgui.Dialog().yesno("Delete video", f"Delete {name} from disk?"):
    sys.exit(0)

try:
    os.remove(path)
except OSError as error:
    notify(f"Could not delete {name}: {error.strerror}")
    sys.exit(0)

notify(f"Deleted {name}")
xbmc.executebuiltin("Container.Refresh")
