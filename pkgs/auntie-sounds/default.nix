{
  python3Packages,
}:

# Music Assistant's BBC Sounds provider needs it, and nixpkgs does not package
# it yet ("missing auntie-sounds" in the music-assistant package's providers).
# Pure Python; the wheel is the version the provider pins (MA 2.10.x).
python3Packages.buildPythonPackage rec {
  pname = "auntie-sounds";
  version = "2.0.9";
  format = "wheel";

  src = python3Packages.fetchPypi {
    pname = "auntie_sounds";
    inherit version format;
    dist = "py3";
    python = "py3";
    sha256 = "81ef9e3332ea1032687ba78a9d0f78cf4f3157f5441204b20b43029e4d2d5d57";
  };

  dependencies = with python3Packages; [
    aiohttp
    appdirs
    beautifulsoup4
    colorlog
    pytz
    typing-extensions
  ];

  # Importing it creates its cache directory under $HOME, which the build
  # sandbox does not have (HOME=/homeless-shelter); the import check runs later
  # in this same shell.
  postInstall = ''
    export HOME=$(mktemp -d)
  '';

  # Imports only: its tests are not in the wheel.
  pythonImportsCheck = [ "sounds" ];

  doCheck = false;
}
