"""Play a random video from the private folder.

Started from Add-ons > Programs (or a favourite made from that entry). The
folder is a browse-only source in Kodi, never scanned, so this is the only
"play something" action that exists for it.
"""

import os
import random
import sys

import xbmc
import xbmcgui

PRIVATE_ROOT = "/mnt/storage-b/media/adult/"
EXTENSIONS = (
    ".3gp",
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogm",
    ".ogv",
    ".ts",
    ".webm",
    ".wmv",
)


def videos(root):
    for directory, _subdirs, names in os.walk(root):
        for name in names:
            # Kodi skips what it cannot see; match it.
            if name.startswith("."):
                continue
            if name.lower().endswith(EXTENSIONS):
                yield os.path.join(directory, name)


def notify(message):
    xbmcgui.Dialog().notification("Private media", message)


try:
    found = list(videos(PRIVATE_ROOT))
except OSError as error:
    notify(f"Folder not available: {error.strerror}")
    sys.exit(0)

if not found:
    notify("No videos in the folder.")
    sys.exit(0)

xbmc.Player().play(random.choice(found))
