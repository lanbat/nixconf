{
  lib,
  fetchzip,
  buildKodiAddon,
}:

# script.globalsearch, the "Global Search" add-on Kodi's own repository
# distributes (ronie's). It searches the whole video and music library, plus
# the searchable add-ons, from one window; discover it from Add-ons, or the
# "Global Search" favourite modules/storage/tv-box.nix seeds.
#
# The Kodi mirror serves the Omega build, which declares xbmc.python 3.0.0
# (Kodi 19+), so Kodi 21 accepts it.
buildKodiAddon rec {
  pname = "globalsearch";
  namespace = "script.globalsearch";
  version = "9.0.7";

  src = fetchzip {
    url = "https://mirrors.kodi.tv/addons/omega/script.globalsearch/script.globalsearch-${version}.zip";
    hash = "sha256-Y+9Vb/u8gtycv/9ouTx8KXGQPrrZNBtyMQ7+z/k2g7c=";
  };

  meta = {
    homepage = "https://gitlab.com/ronie/script.globalsearch/";
    description = "Global search across Kodi's media libraries and add-ons";
    license = lib.licenses.gpl2Only;
    maintainers = [ ];
  };
}
