{
  lib,
  fetchFromGitHub,
  buildKodiAddon,
  six,
  requests,
}:

buildKodiAddon rec {
  pname = "python.twitch";
  namespace = "script.module.python.twitch";
  version = "3.0.3";

  src = fetchFromGitHub {
    owner = "anxdpanic";
    repo = "script.module.python.twitch";
    rev = "v${version}";
    hash = "sha256-p6DAAPZL1a84B5hSNutNVOSO6/z0DfyV9CJsrmEW+EA=";
  };

  propagatedBuildInputs = [
    six
    requests
  ];

  # The addon declares <extension point="xbmc.python.module"
  # library="resources/lib"/>, so the module's import path.
  passthru = {
    pythonPath = "resources/lib";
  };

  meta = {
    homepage = "https://github.com/anxdpanic/script.module.python.twitch";
    description = "Python wrapper for the Twitch API (Helix and GQL), for Kodi add-ons";
    license = lib.licenses.gpl3Only;
    maintainers = [ ];
  };
}
