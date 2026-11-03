# pkgs/broadlink-async/default.nix
#
# broadlink-async (imported as `broadlink_async`): the maintained async fork of
# python-broadlink that Home Assistant's Broadlink integration requires since
# HA 2025.2.  nixpkgs ships the older sync `broadlink` (0.19.0), which HA no
# longer imports, so this package fills the gap.  Called from Home Assistant's
# own Python package set, which provides `cryptography`.
{
  lib,
  buildPythonPackage,
  fetchurl,
  cryptography,
  setuptools,
}:

buildPythonPackage rec {
  pname = "broadlink-async";
  version = "1.1.0";

  src = fetchurl {
    url = "https://files.pythonhosted.org/packages/63/ec/29a4ba3b6d3c384c4d5707b67800ec8e18104f4ade29af41b5ddf4acc1f0/broadlink_async-1.1.0.tar.gz";
    sha256 = "0hqbx3n28a8j24rlk44l2blaxfxdzb41hzggnz5cwyl18rq296k5";
  };

  nativeBuildInputs = [ setuptools ];

  propagatedBuildInputs = [ cryptography ];

  # PEP 517 build (pyproject.toml declares a setuptools build-system).
  pyproject = true;

  # The upstream tests need pytest and network; nothing here exercises them.
  doCheck = false;

  pythonImportsCheck = [ "broadlink_async" ];

  meta = with lib; {
    description = "Async fork of python-broadlink for controlling Broadlink devices locally";
    homepage = "https://github.com/DAB-LABS/python-broadlink";
    license = licenses.mit;
    maintainers = with maintainers; [ ];
  };
}
