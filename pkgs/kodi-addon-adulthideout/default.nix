{
  lib,
  fetchzip,
  buildKodiAddon,
  requests,
}:

# plugin.video.adulthideout (AdultHideout), Vashiel's third-party add-on that
# streams adult videos from public sites. nixpkgs does not carry it; the
# upstream repository.adulthideout publishes this release zip, which declares
# xbmc.python 3.0.0 and minversion 21.0.0, so Kodi 21 accepts it. It needs no
# account or secret, only script.module.requests.
buildKodiAddon rec {
  pname = "adulthideout";
  namespace = "plugin.video.adulthideout";
  version = "1.0.25";

  src = fetchzip {
    url = "https://github.com/Vashiel/repository.adulthideout/raw/master/zips/${namespace}/${namespace}-${version}.zip";
    hash = "sha256-mj1m93W549mk5sJ6X3FB141vRQDY8lIKtwoHh5GdN0U=";
  };

  propagatedBuildInputs = [ requests ];

  meta = {
    homepage = "https://github.com/Vashiel/repository.adulthideout";
    description = "AdultHideout: adult video streams from third-party sites";
    license = lib.licenses.gpl2Plus;
    maintainers = [ ];
  };
}
