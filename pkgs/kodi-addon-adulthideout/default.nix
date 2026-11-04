{
  lib,
  unzip,
  buildKodiAddon,
  requests,
}:

# plugin.video.adulthideout (AdultHideout), Vashiel's third-party add-on that
# streams adult videos from public sites. nixpkgs does not carry it; the
# upstream repository.adulthideout publishes this release zip, which declares
# xbmc.python 3.0.0 and minversion 21.0.0, so Kodi 21 accepts it. It needs no
# account or secret, only script.module.requests.
#
# The zip is vendored: upstream force-pushes repository.adulthideout and
# re-uploads its release zips (1.0.25, 1.0.27 and several intermediate 1.0.26s
# all vanished or changed content within hours). Pinning raw/<commit-sha> did
# not hold, because the commit itself changed under a force-push, so the hash
# kept drifting. The committed zip cannot.
buildKodiAddon rec {
  pname = "adulthideout";
  namespace = "plugin.video.adulthideout";
  version = "1.0.26";

  src = ./plugin.video.adulthideout-1.0.26.zip;

  # The committed zip needs unzip for stdenv's unpackPhase; buildKodiAddon
  # otherwise unpacks nothing (the upstream fetchzip used to provide it).
  nativeBuildInputs = [ unzip ];

  propagatedBuildInputs = [ requests ];

  meta = {
    homepage = "https://github.com/Vashiel/repository.adulthideout";
    description = "AdultHideout: adult video streams from third-party sites";
    license = lib.licenses.gpl2Plus;
    maintainers = [ ];
  };
}
