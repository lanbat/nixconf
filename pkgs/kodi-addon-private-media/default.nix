{
  lib,
  buildKodiAddon,
}:

# The private-group folder's add-on (script.private-media). Kodi has no random
# play for a folder and no per-source delete, so this provides both, scoped to
# media/adult and nothing else: default.py plays one random video from it, and
# delete.py is a context-menu item that only appears on files under it.
#
# The path is hard-coded because Kodi runs the add-on from the read-only store
# and the module's browse-only source points at the same folder
# (modules/storage/tv-box.nix, settings.privateMedia).
buildKodiAddon rec {
  pname = "private-media";
  namespace = "script.private-media";
  version = "1.0.0";

  src = ./.;

  meta = {
    description = "Play a random video from the private folder, and delete the one selected";
    license = lib.licenses.mit;
    maintainers = [ ];
  };
}
