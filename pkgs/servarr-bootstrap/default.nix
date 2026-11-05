{
  lib,
  stdenv,
  makeWrapper,
  coreutils,
  curl,
  gnused,
  jq,
}:

stdenv.mkDerivation {
  pname = "servarr-bootstrap";
  version = "1.0";

  src = ./bootstrap-servarr.sh;

  dontUnpack = true;

  nativeBuildInputs = [ makeWrapper ];

  installPhase = ''
    install -Dm755 $src $out/bin/servarr-bootstrap
    wrapProgram $out/bin/servarr-bootstrap \
      --prefix PATH : "${
        lib.makeBinPath [
          coreutils
          curl
          gnused
          jq
        ]
      }"
  '';
}
