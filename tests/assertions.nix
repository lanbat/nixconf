# tests/assertions.nix
#
# Checks that modules/wiring/checks.nix rejects broken service descriptions
# and accepts a valid one. Pure evaluation: the derivation only builds when
# every case produced the expected messages.
{ lib, pkgs }:

let
  evalServices =
    extraConfig: services:
    let
      system = lib.nixosSystem {
        modules = [
          ../modules/core/services.nix
          ../modules/wiring/accounts.nix
          {
            boot.isContainer = true;
            nixpkgs.hostPlatform = "x86_64-linux";
            system.stateVersion = "25.11";
            lanbat.services = services;
            # A server carries both; the placement cases take one away.
            lanbat.wiring = {
              onDemand = lib.mkDefault true;
              workloadGate = lib.mkDefault true;
            };
            systemd.services.demo.script = "true";
          }
          extraConfig
        ];
      };
      checks = import ../modules/wiring/checks.nix {
        inherit lib;
        inherit (system) config;
      };
    in
    map (a: a.message) (lib.filter (a: !a.assertion) checks.assertions);

  expectWith =
    name: extraConfig: services: fragments:
    let
      messages = evalServices extraConfig services;
      missing = lib.filter (f: !lib.any (lib.hasInfix f) messages) fragments;
      unexpected = fragments == [ ] && messages != [ ];
    in
    if missing != [ ] || unexpected then
      throw "assertion test '${name}' failed: expected ${builtins.toJSON fragments}, got ${builtins.toJSON messages}"
    else
      name;

  expect = name: expectWith name { };

  gatedDemo = {
    demo = {
      tier = "workload";
      state = [ "demo" ];
      units = [ "demo" ];
    };
  };

  results = [
    (expect "valid service passes" {
      demo = {
        subdomain = "demo";
        port = 8000;
        tier = "workload";
        state = [ "demo" ];
        units = [ "demo" ];
      };
    } [ ])

    (expect "port clash" {
      a.port = 8080;
      b.extraPorts = [ 8080 ];
    } [ "port 8080 is used by a, b" ])

    (expect "subdomain clash" {
      a = {
        subdomain = "x";
        port = 1;
      };
      b = {
        subdomain = "x";
        port = 2;
      };
    } [ "subdomain x is used by a, b" ])

    (expect "UID clash" {
      a.account.uid = 950;
      b.account.uid = 950;
    } [ "UID 950 is used by a, b" ])

    (expect "workload tier without state" {
      demo = {
        tier = "workload";
        units = [ "demo" ];
      };
    } [ "demo is workload-gated but declares no state" ])

    (expect "state without workload tier" {
      demo.state = [ "demo" ];
    } [ "declares state but isn't workload-gated" ])

    (expect "unit that no module defines" {
      demo = {
        tier = "workload";
        state = [ "demo" ];
        units = [ "no-such-unit" ];
      };
    } [ "references unit no-such-unit, which no module defines" ])

    (expect "on-demand without a port" {
      demo = {
        units = [ "demo" ];
        onDemand.activatorPort = 3000;
      };
    } [ "demo is on-demand but has no port" ])

    (expect "dashboard without subdomain" {
      demo.dashboard = {
        group = "G";
        name = "Demo";
        description = "d";
      };
    } [ "demo is on the dashboard but has no subdomain" ])

    (expectWith "boot unit that pulls in a gated unit" {
      systemd.services.helper = {
        script = "true";
        wantedBy = [ "multi-user.target" ];
        requires = [ "demo.service" ];
      };
    } gatedDemo [ "helper pulls in the workload-gated demo.service" ])

    (expectWith "boot unit that needs a workload directory" {
      systemd.services.helper = {
        script = "true";
        unitConfig.RequiresMountsFor = "/var/lib/demo/cache";
      };
    } gatedDemo [ "helper needs /var/lib/demo/cache, which is on the workload layer" ])

    (expectWith "timer of a gated unit that starts at boot" {
      systemd.timers.demo = {
        wantedBy = [ "timers.target" ];
        timerConfig.OnBootSec = "5m";
      };
    } gatedDemo [ "demo.timer starts the workload-gated demo.service outside the gate" ])

    (expectWith "workload-gated service on a host without the gate" {
      lanbat.wiring.workloadGate = false;
    } gatedDemo [ "demo is workload-gated, but this host has no workload gate" ])

    (expectWith "always-on service on a host without the gate passes" {
      lanbat.wiring.workloadGate = false;
    } { demo.units = [ "demo" ]; } [ ])

    (expectWith "on-demand service on a host without on-demand wiring"
      { lanbat.wiring.onDemand = false; }
      {
        demo = {
          port = 8000;
          units = [ "demo" ];
          onDemand.activatorPort = 3000;
        };
      }
      [ "demo is on-demand, but this host has no on-demand wiring" ]
    )

    (expect "access groups that nothing enforces" {
      demo = {
        subdomain = "demo";
        port = 8000;
        auth = "app";
        access.groups = [ "admins" ];
      };
    } [ "demo limits access to Authentik groups" ])

    (expect "tang with an endpoint" {
      tang.endpoint.port = 7500;
    } [ "tang publishes an endpoint" ])

    (expect "tang without an endpoint passes" {
      tang.extraPorts = [ 7500 ];
    } [ ])
  ];
in
pkgs.runCommand "lanbat-assertions" { } ''
  echo ${lib.escapeShellArg (lib.concatStringsSep "\n" results)}
  touch $out
''
