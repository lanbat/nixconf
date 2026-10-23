{
  lib,
  fetchFromGitHub,
  buildKodiAddon,
  requests,
  pythonTwitch,
}:

buildKodiAddon rec {
  pname = "twitch";
  namespace = "plugin.video.twitch";
  version = "3.0.3";

  src = fetchFromGitHub {
    owner = "anxdpanic";
    repo = "plugin.video.twitch";
    rev = "v${version}";
    hash = "sha256-uuDlGFxvPhpVVOvq6FYVIshiXRZeeRvgmJ9RKPBXpyE=";
  };

  propagatedBuildInputs = [
    requests
    pythonTwitch
  ];

  meta = {
    homepage = "https://github.com/anxdpanic/plugin.video.twitch";
    description = "Watch Twitch streams, clips and VODs in Kodi";
    license = lib.licenses.gpl3Only;
    maintainers = [ ];
  };
}
