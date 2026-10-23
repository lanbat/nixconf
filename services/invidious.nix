# services/invidious.nix
#
# Invidious — privacy-respecting front-end for YouTube.
#
# Auth: intentionally NONE, like SearXNG.
#   Invidious has no OIDC, and the LAN is trusted; its browser UI is served
#   without the Authentik middleware. Accounts, if used, are its own.
#
# Always-on: yes, by choice.
#   Its database holds cached video/channel metadata and, if someone creates
#   an account, subscriptions and watch history. That is mild personal data,
#   so — like Home Assistant and Authentik — it starts at boot instead of
#   waiting for unlock-workload. To keep it encrypted at rest instead, set
#   tier = "workload", list "invidious" in state/units, and move
#   lanbat.postgresql.databases.invidious to the "workload" instance (which
#   then also removes the password below: the workload instance uses peer auth).
#
# Database:
#   The "invidious" database lives on the always-on PostgreSQL instance (port
#   5433). The NixOS module wants the password as a bare value in
#   database.passwordFile, while lanbat.postgresql's passwordFile is a
#   POSTGRES_PASSWORD=... shell file, so the two cannot be the same file. The
#   secret here is the bare password: invidious-db-password sets it on the
#   role once the instance is up, and the module reads it from a systemd
#   credential, so it never reaches the Nix store.
#
# YouTube is a moving target: playback can break when YouTube changes its
# client checks. services.invidious.sig-helper (inv-sig-helper) and
# http3-ytproxy are two upstream answers; both stay off here because
# sig-helper runs JavaScript downloaded from YouTube.
{
  config,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  port = 8083;
  alwaysOn = config.lanbat.postgresql.instance "always-on";
in
{
  lanbat.services.invidious = {
    subdomain = "youtube";
    inherit port;
    auth = "none";
    secrets = {
      # A bare password, one line. postgresql-always-on-setup creates the
      # role; invidious-db-password sets this on it, and the Invidious module
      # reads it from the systemd credential below. Group postgres so the
      # oneshot can read it as that user.
      invidious-db-pass = {
        owner = "root";
        group = "postgres";
        mode = "0440";
      };
    };
    dashboard = {
      group = "Utilities";
      name = "Invidious";
      description = "YouTube front-end";
    };
  };

  # Always-on instance, so it is up at boot with the rest of Invidious.
  lanbat.postgresql.databases.invidious = {
    instance = "always-on";
  };

  services.invidious = {
    enable = true;
    address = "127.0.0.1";
    port = config.lanbat.services.invidious.port;
    domain = "youtube.${domain}";

    database = {
      # Managed by lanbat.postgresql above, not a private instance.
      createLocally = false;
      host = "127.0.0.1";
      port = alwaysOn.port;
      # Read through the systemd credential below, never the Nix store.
      passwordFile = "$CREDENTIALS_DIRECTORY/invidious-db-password";
    };

    settings = {
      # Caddy terminates TLS on 443.
      https_only = true;
      external_port = 443;
    };
  };

  systemd.services.invidious = {
    after = [
      alwaysOn.unit
      "invidious-db-password.service"
    ];
    requires = [
      alwaysOn.unit
      "invidious-db-password.service"
    ];
    serviceConfig.LoadCredential = [
      "invidious-db-password:${config.lanbat.secrets.invidious-db-pass.path}"
    ];
  };

  # Set the role's password after the always-on instance has created it. The
  # module's database.passwordFile is a bare value, so this cannot use
  # lanbat.postgresql's POSTGRES_PASSWORD=... file directly.
  systemd.services.invidious-db-password = {
    description = "Set the Invidious PostgreSQL password";
    after = [ alwaysOn.unit ];
    requires = [ alwaysOn.unit ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "postgres";
    };
    script = ''
      set -euo pipefail
      # Via stdin so psql interpolates :'pw' even on older versions.
      echo "ALTER ROLE invidious WITH PASSWORD :'pw';" | \
        ${config.services.postgresql.finalPackage}/bin/psql \
          -h ${alwaysOn.socket} -p ${toString alwaysOn.port} -d postgres \
          -v pw="$(cat ${config.lanbat.secrets.invidious-db-pass.path})"
    '';
  };
}
