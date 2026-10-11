{
  description = "lanbat homelab — extensible multi-machine NixOS";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

    # Zigbee2MQTT only.  The pinned nixpkgs above carries 2.9.1, whose bundled
    # zigbee-herdsman-converters does not list the manufacturer name reported by
    # the TS0601 soil sensor (see services/zigbee2mqtt.nix).  Deliberately not
    # following nixpkgs: the point is a newer lock.
    nixpkgs-z2m.url = "github:NixOS/nixpkgs/nixos-unstable";

    nixos-raspberrypi.url = "github:nvmd/nixos-raspberrypi/main";

    agenix = {
      url = "github:ryantm/agenix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    disko = {
      url = "github:nix-community/disko/latest";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    deploy-rs = {
      url = "github:serokell/deploy-rs";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    lanbat-justpark-parking = {
      url = "github:lanbat/lanbat-justpark-parking";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      nixos-raspberrypi,
      agenix,
      disko,
      deploy-rs,
      ...
    }@inputs:
    let
      inherit (nixpkgs) lib;

      lanbatPlugins = import ./plugins;

      inputsWithSelf = inputs // {
        self = self // {
          inherit lanbatPlugins;
        };
      };

      loadDeployments = import ./lib/load-deployments.nix { inherit lib; };

      hasDeploy = builtins.pathExists ./deploy.nix;

      deployRaw =
        if hasDeploy then
          import ./deploy.nix { inputs = inputsWithSelf; }
        else
          import ./deployments/example/deploy.nix { inputs = inputsWithSelf; };

      profiles =
        if hasDeploy then
          loadDeployments.normalize deployRaw
        else
          {
            example = import ./deployments/example/deploy.nix { inputs = inputsWithSelf; };
          };

      lanbatLib = import ./lib {
        self = inputsWithSelf.self;
        inputs = inputsWithSelf;
        inherit
          nixpkgs
          nixos-raspberrypi
          agenix
          disko
          deploy-rs
          profiles
          hasDeploy
          ;
        root = ./.;
      };

      pkgs = nixpkgs.legacyPackages.x86_64-linux;
    in
    {
      inherit lanbatPlugins;

      lib.lanbat = {
        inherit (lanbatLib)
          hostsWithRole
          primaryHost
          hostIp
          hostHostname
          hostInterface
          voiceRoomForHost
          validatePlugin
          resolvePlugins
          mkHost
          mkProfile
          hostFlakeName
          deployLib
          deployQuery
          secretsRecipients
          ;
        loadDeployments = loadDeployments.normalize;
      };

      nixosModules = {
        lanbat = ./modules/core;
        lanbat-server = ./lib/roles/server.nix;
        lanbat-storage-pi = ./lib/roles/storage-pi.nix;
        lanbat-voice-pi = ./lib/roles/voice-pi.nix;
        # What every built-in role shares (hostname, static address, firewall
        # baseline), for a role a plugin adds (docs/plugins.md#host-roles).
        lanbat-role-common = ./lib/roles/common.nix;
        lanbat-android = ./modules/server/android-devices.nix;
      };

      nixosConfigurations = lanbatLib.configurations;

      deploy.nodes = if hasDeploy then lanbatLib.deployNodes else { };

      checks.x86_64-linux = {
        android-devices = import ./tests/android-devices.nix { inherit lib pkgs; };
        android-provision = pkgs.callPackage ./pkgs/android-provision { };
        assertions = import ./tests/assertions.nix { inherit lib pkgs; };
        private-media = import ./tests/private-media.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        authentik-catalogue = import ./tests/authentik-catalogue.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        music-assistant = import ./tests/music-assistant.nix { inherit pkgs; };
        media-feed-bridge = import ./tests/media-feed-bridge.nix { inherit pkgs; };
        pkgs-build = import ./tests/pkgs-build.nix { inherit pkgs; };
        ha-dashboards = import ./tests/ha-dashboards.nix { inherit pkgs; };
        stackchan-bridge = import ./tests/stackchan-bridge.nix { inherit pkgs; };
        doa-order = import ./tests/doa-order.nix { inherit pkgs; };
        plugins = import ./tests/plugins.nix { inherit lib pkgs; };
        endpoints-table = import ./tests/endpoints-table.nix { inherit lib pkgs; };
        backup-server = import ./tests/backup-server.nix { inherit pkgs; };
        frigate-settings = import ./tests/frigate-settings.nix { inherit lib pkgs; };
        llama-cpp-settings = import ./tests/llama-cpp-settings.nix { inherit lib pkgs; };
        qbittorrent-userns = import ./tests/qbittorrent-userns.nix { inherit lib pkgs; };
        qbittorrent-settings = import ./tests/qbittorrent-settings.nix { inherit lib pkgs; };
        syncthing-settings = import ./tests/syncthing-settings.nix { inherit lib pkgs; };
        policy = import ./tests/policy.nix { inherit lib pkgs; };
        dns = import ./tests/dns.nix { inherit pkgs; };
        dns-records = import ./tests/dns-records.nix { inherit lib pkgs; };
        caddy-remote = import ./tests/caddy-remote.nix { inherit lib pkgs; };
        caddy-audit = import ./tests/caddy-audit.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        overlay = import ./tests/overlay.nix { inherit lib pkgs; };
        on-demand-activator = import ./tests/on-demand-activator.nix { inherit pkgs; };
        postgresql = import ./tests/postgresql.nix { inherit pkgs; };
        secrets = import ./tests/secrets.nix { inherit lib pkgs; };
        music-assistant-sources = import ./tests/music-assistant-sources.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        voice-compute = import ./tests/voice-compute.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        service-settings = import ./tests/service-settings.nix (
          {
            part = 1;
            parts = 3;
          }
          // {
            inherit
              lib
              pkgs
              inputs
              self
              agenix
              disko
              deploy-rs
              nixpkgs
              nixos-raspberrypi
              ;
          }
        );
        service-settings-2 = import ./tests/service-settings.nix (
          {
            part = 2;
            parts = 3;
          }
          // {
            inherit
              lib
              pkgs
              inputs
              self
              agenix
              disko
              deploy-rs
              nixpkgs
              nixos-raspberrypi
              ;
          }
        );
        service-settings-3 = import ./tests/service-settings.nix (
          {
            part = 3;
            parts = 3;
          }
          // {
            inherit
              lib
              pkgs
              inputs
              self
              agenix
              disko
              deploy-rs
              nixpkgs
              nixos-raspberrypi
              ;
          }
        );
        settings-guard = import ./tests/settings-guard.nix { inherit lib pkgs; };
        settings-schema = import ./tests/settings-schema.nix { inherit lib pkgs; };
        validate-deploy = import ./tests/validate-deploy.nix { inherit lib pkgs; };
        load-deployments = import ./tests/load-deployments.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        deploy-rs-fixture = import ./tests/deploy-rs-fixture.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        no-auto-upgrade = import ./tests/no-auto-upgrade.nix {
          inherit
            lib
            pkgs
            self
            agenix
            disko
            nixpkgs
            nixos-raspberrypi
            ;
          inputs = inputsWithSelf;
        };
        storage-drives = import ./tests/storage-drives.nix {
          inherit
            lib
            pkgs
            inputs
            self
            agenix
            disko
            deploy-rs
            nixpkgs
            nixos-raspberrypi
            ;
        };
        server = import ./tests/server.nix {
          inherit pkgs inputs;
          inherit (inputs) agenix disko;
        };
        overlay-mesh = import ./tests/overlay-mesh.nix {
          inherit pkgs;
          inherit (inputs) agenix;
        };
        workload-gate = import ./tests/workload-gate.nix { inherit pkgs; };
      }
      // lib.optionalAttrs hasDeploy (
        (lanbatLib.deployLib "x86_64-linux").deployChecks { nodes = lanbatLib.deployNodes; }
      );

      checks.aarch64-linux = {
        storage-pi = import ./tests/storage-pi.nix {
          pkgs = nixos-raspberrypi.inputs.nixpkgs.legacyPackages.aarch64-linux;
          inherit (inputs)
            agenix
            nixpkgs
            nixos-raspberrypi
            disko
            ;
          inputs = inputsWithSelf;
        };

        voice-pi = import ./tests/voice-pi.nix {
          pkgs = nixos-raspberrypi.inputs.nixpkgs.legacyPackages.aarch64-linux;
          inherit (inputs)
            agenix
            nixpkgs
            nixos-raspberrypi
            disko
            ;
          inputs = inputsWithSelf;
        };
      };

      devShells.x86_64-linux.default = pkgs.mkShell {
        packages = [
          pkgs.deploy-rs
          agenix.packages.x86_64-linux.default
          pkgs.nixos-anywhere
        ];
      };

      formatter = lib.genAttrs [ "x86_64-linux" "aarch64-linux" ] (
        system: nixpkgs.legacyPackages.${system}.nixfmt-tree
      );

      apps.x86_64-linux = {
        deploy-query = {
          type = "app";
          program = toString (
            pkgs.writeShellScript "deploy-query" ''
              set -euo pipefail
              KEY="''${1:?usage: deploy-query <key> [profile]}"
              PROFILE="''${2:-}"
              REF=".#lib.lanbat.deployQuery.\"''${KEY}\""
              if [ -n "''$PROFILE" ]; then
                exec ${pkgs.nix}/bin/nix eval --raw --apply "f: f \"''$PROFILE\"" "''$REF"
              else
                exec ${pkgs.nix}/bin/nix eval --raw --apply "f: f null" "''$REF"
              fi
            ''
          );
        };

        hosts = {
          type = "app";
          program = toString (
            pkgs.writeShellScript "hosts" ''
              exec ${pkgs.nix}/bin/nix eval --raw --apply "f: f null" .#lib.lanbat.deployQuery.hosts
            ''
          );
        };

        validate-deploy = {
          type = "app";
          program = toString (
            pkgs.writeShellScript "validate-deploy" ''
              exec ${pkgs.nix}/bin/nix build .#checks.x86_64-linux.validate-deploy --no-link
            ''
          );
        };

        # The agenix recipients of each secret, derived from where the hosts of
        # a profile require it: nix run .#secrets-recipients [--json] [profile]
        secrets-recipients = {
          type = "app";
          program = toString (
            pkgs.writeShellScript "secrets-recipients" ''
              set -euo pipefail
              if [ "''${1:-}" = "--json" ]; then JSON=1; shift; else JSON=; fi
              PROFILE="''${1:-}"
              if [ -n "$PROFILE" ]; then ARG="\"$PROFILE\""; else ARG=null; fi
              REF=.#lib.lanbat.secretsRecipients
              if [ -n "$JSON" ]; then
                exec ${pkgs.nix}/bin/nix eval --json "$REF" \
                  --apply "f: { inherit (f $ARG) profile recipients off; }"
              else
                exec ${pkgs.nix}/bin/nix eval --raw "$REF" --apply "f: (f $ARG).text + \"\n\""
              fi
            ''
          );
        };

        overlay-keys = {
          type = "app";
          program = "${
            pkgs.callPackage ./pkgs/overlay-keys { agenix = agenix.packages.x86_64-linux.default; }
          }/bin/overlay-keys";
        };

        android-provision = {
          type = "app";
          program = "${pkgs.callPackage ./pkgs/android-provision { }}/bin/android-provision";
        };
        android-update = {
          type = "app";
          program = "${pkgs.writeShellScript "android-update" ''
            exec ${pkgs.callPackage ./pkgs/android-provision { }}/bin/android-provision \
              update --lockfile "''${1:-pkgs/android-provision/apks.lock.json}" "''${@:2}"
          ''}";
        };
      };
    };
}
