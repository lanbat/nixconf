# Jackett newer than the one the locked nixpkgs carries.
#
# Jackett's indexer definitions ship inside the build, and the sites they
# describe change constantly, so an old build fails more of them: Jackett
# itself warns "very old" and says to update. nixpkgs bumps Jackett with the
# flake lock; this keeps it current on its own, without moving the rest of
# the system. It is nixpkgs' pkgs/by-name/ja/jackett/package.nix with a newer
# version, so drop this directory once the locked nixpkgs catches up.
#
# To update: change `version` and `hash`, then regenerate deps.json:
#   nix build --no-link --print-out-paths .#<host pkgs>.jackett.fetch-deps
#   <that path> pkgs/jackett/deps.json
# `hash` is `nix-prefetch-url --unpack` of the GitHub tag archive, as SRI.
{
  lib,
  stdenv,
  buildDotnetModule,
  fetchFromGitHub,
  dotnetCorePackages,
  openssl,
  mono,
}:

buildDotnetModule (finalAttrs: {
  pname = "jackett";
  version = "0.24.2985";

  src = fetchFromGitHub {
    owner = "jackett";
    repo = "jackett";
    tag = "v${finalAttrs.version}";
    hash = "sha256-qKYj9UTsxobDfL5PaVXGg4HsOyt7NweN0Y2xnkA2lU8=";
  };

  projectFile = "src/Jackett.Server/Jackett.Server.csproj";
  nugetDeps = ./deps.json;

  dotnet-runtime = dotnetCorePackages.aspnetcore_9_0;
  dotnet-sdk = dotnetCorePackages.sdk_9_0;

  dotnetInstallFlags = [
    "--framework"
    "net9.0"
  ];

  postPatch = ''
    substituteInPlace ${finalAttrs.projectFile} ${finalAttrs.testProjectFile} \
      --replace-fail '<TargetFrameworks>net9.0;net471</' '<TargetFrameworks>net9.0</'
  '';

  runtimeDeps = [ openssl ];
  doCheck = !stdenv.hostPlatform.isDarwin;
  nativeCheckInputs = [ mono ];
  testProjectFile = "src/Jackett.Test/Jackett.Test.csproj";

  postFixup = ''
    # For compatibility
    ln -s $out/bin/jackett $out/bin/Jackett || :
    ln -s $out/bin/Jackett $out/bin/jackett || :
  '';

  meta = {
    description = "API Support for your favorite torrent trackers";
    mainProgram = "jackett";
    homepage = "https://github.com/Jackett/Jackett/";
    changelog = "https://github.com/Jackett/Jackett/releases/tag/v${finalAttrs.version}";
    license = lib.licenses.gpl2Only;
  };
})
