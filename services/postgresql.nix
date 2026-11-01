# services/postgresql.nix
#
# Two PostgreSQL instances, one per storage tier:
#
#   postgresql            Workload tier: data on the workload LUKS layer
#                         (/var/lib/postgresql), starts after unlock-workload.
#                         Port 5432, socket /run/postgresql. Managed by the
#                         NixOS module. Immich, Bitmagnet, RomM.
#
#   postgresql-always-on  Always-on tier: data on the host root
#                         (/var/lib/postgresql-always-on), starts at boot.
#                         Port 5433, socket /run/postgresql-always-on.
#                         Authentik (logins must work before unlock), Home
#                         Assistant, Grafana.
#
# Services add a database with lanbat.postgresql.databases.<name>: a database
# and an owner role of the same name on the chosen instance. A local system
# user of that name logs in over the socket without a password (peer). A
# passwordFile also sets a password for TCP logins from containers.
#
# Connection details for consumers: lanbat.postgresql.instances.<instance>.
{
  config,
  lib,
  pkgs,
  ...
}:

let

  databases = config.lanbat.postgresql.databases;
  onInstance = instance: lib.filterAttrs (_: db: db.instance == instance) databases;

  package = config.services.postgresql.finalPackage;
  instances = config.lanbat.postgresql.instances;
  alwaysOn = instances.always-on;
  alwaysOnDataDir = "/var/lib/postgresql-always-on";

  authentication = ''
    # TYPE  DATABASE        USER            ADDRESS           METHOD
    local   all             all                               peer
    host    all             all             127.0.0.1/32      scram-sha-256
  '';

  # Shell steps for one database. ensure: create the role and database first
  # (the NixOS module does that for the workload instance).
  databaseSteps =
    {
      psql,
      ensure,
    }:
    name: db:
    let
      password = "$" + db.passwordVariable;
    in
    ''
      ${lib.optionalString ensure ''
        if [ -z "$(${psql} -tAc "SELECT 1 FROM pg_roles WHERE rolname = '${name}'")" ]; then
          ${psql} -c 'CREATE ROLE "${name}" LOGIN'
        fi
        if [ -z "$(${psql} -tAc "SELECT 1 FROM pg_database WHERE datname = '${name}'")" ]; then
          ${psql} -c 'CREATE DATABASE "${name}" OWNER "${name}"'
        fi
      ''}
      ${lib.optionalString (db.passwordFile != null) ''
        if [ -r ${db.passwordFile} ]; then
          (
            ${db.passwordVariable}=""
            source ${db.passwordFile}
            echo "ALTER ROLE \"${name}\" WITH PASSWORD :'password';" | ${psql} -v password="${password}"
          )
        else
          echo "${db.passwordFile} is missing; not setting the ${name} password" >&2
        fi
      ''}
      ${lib.optionalString (db.extraSql != "") "${psql} -d ${name} -c ${lib.escapeShellArg db.extraSql}"}
    '';
in
{
  config = {
    # The contract is declared in modules/core/database.nix; this provider
    # answers it by advertising what it serves.
    lanbat.postgresql.instances = {
      workload = {
        port = 5432;
        socket = "/run/postgresql";
        unit = "postgresql.target";
      };
      always-on = {
        port = 5433;
        socket = "/run/postgresql-always-on";
        unit = "postgresql-always-on-setup.service";
      };
    };

    lanbat.services.postgresql = {
      extraPorts = [ instances.workload.port ];
      tier = "workload";
      state = [ "postgresql" ];
      units = [
        "postgresql"
        "postgresql-setup"
      ];
      workloadDirs."postgresql".user = "postgres";
    };

    lanbat.services.postgresql-always-on.extraPorts = [ alwaysOn.port ];

    # ── Workload instance (NixOS module) ──────────────────────────────────────
    services.postgresql = {
      enable = true;
      package = pkgs.postgresql_16;
      extensions =
        ps: with ps; [
          pgvector
          vectorchord
        ];

      settings = {
        # Containers reach it through --network=host.
        listen_addresses = lib.mkForce "127.0.0.1";
        # vectorchord (Immich's vector search) must be preloaded.
        shared_preload_libraries = "vchord.so";
        max_connections = 200;
      };

      authentication = lib.mkForce authentication;

      ensureDatabases = lib.attrNames (onInstance "workload");
      ensureUsers = map (name: {
        inherit name;
        ensureDBOwnership = true;
      }) (lib.attrNames (onInstance "workload"));
    };

    # The module starts postgresql.target from multi-user.target; the target
    # would pull the gated services in at boot.
    systemd.targets.postgresql = {
      wantedBy = lib.mkForce [ "workload-online.target" ];
      after = [ "workload-online.target" ];
      bindsTo = [ "workload-online.target" ];
    };

    systemd.services =
      lib.mapAttrs' (
        name: db:
        lib.nameValuePair "postgresql-${name}-init" {
          description = "Set up the ${name} database";
          after = [
            "postgresql.service"
            "postgresql-setup.service"
          ];
          wantedBy = [ "postgresql.service" ];
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            User = "postgres";
            ExecStart = pkgs.writeShellScript "postgresql-${name}-init" ''
              set -euo pipefail
              ${databaseSteps {
                psql = "${package}/bin/psql -v ON_ERROR_STOP=1";
                ensure = false;
              } name db}
            '';
          };
        }
      ) (lib.filterAttrs (_: db: db.passwordFile != null || db.extraSql != "") (onInstance "workload"))
      // {
        # ── Always-on instance ────────────────────────────────────────────────
        postgresql-always-on = {
          description = "PostgreSQL (always-on instance)";
          after = [ "network.target" ];
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "notify";
            User = "postgres";
            Group = "postgres";
            StateDirectory = "postgresql-always-on";
            StateDirectoryMode = "0700";
            RuntimeDirectory = "postgresql-always-on";
            RuntimeDirectoryMode = "0755";
            ExecStartPre = pkgs.writeShellScript "postgresql-always-on-initdb" ''
              if [ ! -s ${alwaysOnDataDir}/PG_VERSION ]; then
                ${package}/bin/initdb -D ${alwaysOnDataDir} -U postgres --encoding=UTF8 --locale=C
              fi
            '';
            ExecStart = lib.concatStringsSep " " [
              "${package}/bin/postgres"
              "-D ${alwaysOnDataDir}"
              "-c port=${toString alwaysOn.port}"
              "-c listen_addresses=127.0.0.1"
              "-c unix_socket_directories=${alwaysOn.socket}"
              "-c hba_file=${pkgs.writeText "pg_hba.conf" authentication}"
              "-c max_connections=200"
              "-c log_destination=stderr"
              "-c logging_collector=off"
            ];
            ExecReload = "${pkgs.coreutils}/bin/kill -HUP $MAINPID";
            KillSignal = "SIGINT";
            KillMode = "mixed";
            TimeoutSec = 120;
          };
        };

        postgresql-always-on-setup = {
          description = "Set up the databases of the always-on PostgreSQL";
          after = [ "postgresql-always-on.service" ];
          requires = [ "postgresql-always-on.service" ];
          wantedBy = [ "multi-user.target" ];
          serviceConfig = {
            Type = "oneshot";
            RemainAfterExit = true;
            User = "postgres";
            ExecStart = pkgs.writeShellScript "postgresql-always-on-setup" ''
              set -euo pipefail
              ${lib.concatStringsSep "\n" (
                lib.mapAttrsToList (databaseSteps {
                  psql = "${package}/bin/psql -h ${alwaysOn.socket} -p ${toString alwaysOn.port} -v ON_ERROR_STOP=1";
                  ensure = true;
                }) (onInstance "always-on")
              )}
            '';
          };
        };
      };
  };
}
