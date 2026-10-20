import os
import sys

# Outside the package build, room_presence is pkgs/room-presence's own file.
here = os.path.dirname(__file__)
for path in (os.path.join(here, ".."), os.path.join(here, "..", "..", "room-presence")):
    sys.path.insert(0, os.path.abspath(path))
