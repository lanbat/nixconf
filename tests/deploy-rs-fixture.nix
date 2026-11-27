# tests/deploy-rs-fixture.nix
#
# Build deployChecks from a fixture profile, independent of ./deploy.nix.
{
  lib,
  pkgs,
  inputs,
  self,
  agenix,
  disko,
  deploy-rs,
  nixpkgs,
  nixos-raspberrypi,
}:

let
  loadDeployments = import ../lib/load-deployments.nix { inherit lib; };

  lanbatPlugins = import ../plugins;

  inputsWithSelf = inputs // {
    self = self // {
      inherit lanbatPlugins;
    };
  };

  # This check is about deploy-rs's wiring (the schema, and an activation script
  # built from the node's profile), not about the services. A server that names
  # none gets all of them, and building that whole closure compiled paperless,
  # redlib, snapcast and spacy on every pull request (about 40 minutes). One
  # service stands in; the services are evaluated by the `eval` job and built by
  # the nightly server VM test.
  fixture = lib.recursiveUpdate (import ./fixtures/multi-profile-deploy.nix {
    inputs = inputsWithSelf;
  }) { profiles.homelab.hosts.server.services = [ "caddy" ]; };

  profiles = loadDeployments.normalize fixture;

  lanbatLib = import ../lib {
    self = inputsWithSelf.self;
    inputs = inputsWithSelf;
    inherit
      nixpkgs
      nixos-raspberrypi
      agenix
      disko
      deploy-rs
      profiles
      ;
  };

  serverNodes = lib.filterAttrs (n: _: n == "homelab-server") lanbatLib.deployNodes;

  checks =
    if serverNodes ? homelab-server then
      (lanbatLib.deployLib "x86_64-linux").deployChecks { nodes = serverNodes; }
    else
      throw "expected homelab-server deploy node";
in
pkgs.runCommand "deploy-rs-fixture"
  {
    nativeBuildInputs = [
      checks.deploy-activate
      checks.deploy-schema
    ];
  }
  ''
    touch $out
  ''
