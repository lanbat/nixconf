{
  lib,
  fetchzip,
  buildKodiAddon,
  infotagger,
  requests,
  inputstream-adaptive,
  inputstreamhelper,
}:

# plugin.video.invidious, the Invidious client petterreinholdtsen maintains
# (a fork of TheAssassin's add-on) and the Kodi repository ships.  nixpkgs
# pins 0.2.6 from 2023; 0.2.9 carries the fixes since (instance error
# reporting, proxy support, Python 3.9 login).  The add-on itself is in the
# Kodi mirror under the Nexus build; it declares xbmc.python 3.0.0, which
# Kodi 21 satisfies.
buildKodiAddon rec {
  pname = "invidious";
  namespace = "plugin.video.invidious";
  version = "0.2.9";

  src = fetchzip {
    url = "https://mirrors.kodi.tv/addons/omega/plugin.video.invidious/plugin.video.invidious-${version}+nexus.0.zip";
    hash = "sha256-Lj9+UYO06a+gRfXzXrBUdQBdJz1keJvWPBF1f+6DOOw=";
  };

  propagatedBuildInputs = [
    infotagger
    requests
    inputstream-adaptive
    inputstreamhelper
  ];

  # 0.2.9's "local" setting crashes every request that passes no params
  # (search and video lookups do): _make_get_request defaults params to None
  # and then assigns params["local"].  Upstream master still has the line;
  # make the assignment safe.  Without this, local=true cannot play anything.
  postPatch = ''
    substituteInPlace resources/lib/invidious_api.py \
      --replace-fail 'params["local"] = "true"' 'params = {**(params or {}), "local": "true"}'
  '';

  # The add-on declares <extension point="xbmc.python.module"
  # library="resources/lib"/>, so the module's import path.
  passthru.pythonPath = "resources/lib";

  meta = {
    homepage = "https://github.com/petterreinholdtsen/kodi-invidious-plugin";
    description = "Watch YouTube through an Invidious instance in Kodi";
    license = lib.licenses.mit;
    maintainers = [ ];
  };
}
