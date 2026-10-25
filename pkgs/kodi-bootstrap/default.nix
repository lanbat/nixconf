{
  lib,
  stdenv,
  makeWrapper,
  coreutils,
  gnugrep,
  sqlite,
  util-linux,
  xmlstarlet,
}:

stdenv.mkDerivation {
  pname = "kodi-bootstrap";
  version = "1.0";

  src = ./bootstrap-kodi.sh;

  dontUnpack = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    install -Dm755 $src $out/bin/kodi-bootstrap
    wrapProgram $out/bin/kodi-bootstrap \
      --prefix PATH : "${
        lib.makeBinPath [
          coreutils
          gnugrep
          sqlite
          util-linux
          xmlstarlet
        ]
      }"
  '';
}
