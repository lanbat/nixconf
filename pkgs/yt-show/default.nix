# pkgs/yt-show
#
# YouTube for the Echo Show: find videos, and hand out the one H.264 MP4 the
# screen can play (services/yt-show.nix, docs/techo5.md). Invidious when it is
# there, yt-dlp when it is not.
#
# yt-dlp is not a dependency: it is an executable, given by the service
# (YTDLP, on its PATH), so a nixpkgs bump of yt-dlp reaches the service
# without a rebuild of this package.
{ python3Packages }:

python3Packages.buildPythonApplication {
  pname = "yt-show";
  version = "1.0";
  pyproject = true;
  src = ./.;
  build-system = [ python3Packages.setuptools ];
  dependencies = [ python3Packages.aiohttp ];
  nativeCheckInputs = [
    python3Packages.pytestCheckHook
    python3Packages.pytest-aiohttp
    python3Packages.pytest-asyncio
  ];
  meta.mainProgram = "yt-show";
}
