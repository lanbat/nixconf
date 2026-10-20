# pkgs/techo5-dashcast
#
# TECHO5's dashcast: Home Assistant dashboards drawn by a headless Chrome and
# streamed to the Echo Shows as pictures, over a Noise link keyed with
# techo5-dashcast-key (services/techo5-bridge.nix, docs/techo5.md). Pinned to
# the TECHO5 release the devices run, whose protocol it speaks.
{
  buildGoModule,
  fetchFromGitHub,
}:

buildGoModule rec {
  pname = "techo5-dashcast";
  version = "1.2.24";
  src = fetchFromGitHub {
    owner = "HuskerMinion";
    repo = "techo5";
    rev = "v${version}";
    hash = "sha256-UE+TahyjdkaMwd7/H93QC4rVuwKDVX2nTnYmQ4mHo3M=";
  };
  sourceRoot = "${src.name}/dashcast";
  # Its go.mod names a newer patch release of Go 1.26 than nixpkgs has; it
  # needs nothing from it.
  postPatch = ''
    substituteInPlace go.mod --replace-fail "go 1.26.8" "go 1.26"
  '';
  vendorHash = "sha256-TNxUVMdCfGfSSDbTTFHA95x3JtRhHu8HY/WwIymle70=";
  ldflags = [
    "-s"
    "-w"
  ];
  # The tests drive a real Chrome.
  doCheck = false;
  meta.mainProgram = "dashcast";
}
