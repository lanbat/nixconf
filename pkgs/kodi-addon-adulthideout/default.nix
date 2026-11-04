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
  # Upstream deletes and re-uploads the release zips on raw/master (1.0.25,
  # 1.0.27 and an intermediate 1.0.26 all vanished within hours), so pin the
  # exact commit the zip is stored at; raw/<sha> is immutable.
  version = "1.0.26";

  src = fetchzip {
    url = "https://github.com/Vashiel/repository.adulthideout/raw/fa1606fdbf524c864fa57e509dac926cb2c34287/zips/${namespace}/${namespace}-${version}.zip";
    hash = "sha256-ZGInHp7oxLw3lZFT+J33IB05totE2TPvxbFbSE7y4vM=";
  };

  propagatedBuildInputs = [ requests ];

  meta = {
    homepage = "https://github.com/Vashiel/repository.adulthideout";
    description = "AdultHideout: adult video streams from third-party sites";
    license = lib.licenses.gpl2Plus;
    maintainers = [ ];
  };
}
