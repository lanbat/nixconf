# services/forgejo.nix
#
# Forgejo — a lightweight self-hosted git forge (a community-maintained Gitea
# fork), the home's git host at https://git.<domain>.
#
# Design
# ------
# - NixOS-native service (no container) with a SQLite database: a small forge
#   needs nothing more, and its whole state is one directory.
# - HTTPS clone/pull/push with personal access tokens from the web UI, and SSH
#   clone/pull/push through Forgejo's own SSH server on port 2222. That server
#   is separate from the admin sshd on 22, so it changes neither who can log in
#   to the host nor the deploy path.
# - Login is Authentik over OIDC: the catalogue provisions the OAuth2 provider
#   and application (client id "forgejo"). Forgejo keeps its own accounts, so
#   the bootstrap pre-creates the owner's account as the admin with a
#   break-glass password, and OIDC links to it (ACCOUNT_LINKING = auto, and
#   USERNAME = nickname, the claim Authentik fills with the username); every
#   other Authentik user is auto-registered on first sign-in. This is the
#   Vaultwarden model, except Forgejo links by username rather than by email.
#
# Secrets
# -------
# forgejo-env.age — KEY=value lines the bootstrap reads:
#   FORGEJO_ADMIN_PASSWORD=<break-glass password for the owner admin>
#   FORGEJO_OAUTH_CLIENT_SECRET=<the same value as
#     AUTHENTIK_FORGEJO_CLIENT_SECRET in authentik-oidc-secrets.age>
# Generate both with secrets/generate-oidc-secrets.sh.
#
# State
# -----
# /var/lib/forgejo — SQLite DB, repositories, LFS, custom config (back this up!)
#
# Workload tier: repositories are user data, available once the workload layer
# is unlocked, like Nextcloud's files.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  port = 3001;

  cfg = config.lanbat.services.forgejo.settings;

  # SSO is only offered when the identity provider runs on this host; without
  # it Forgejo keeps its own accounts (the break-glass admin).
  hasSso = config.lanbat.hasService "authentik";

  # Name of the OAuth2 authentication source; it appears in the callback path.
  authSource = "Authentik";

  # The admin email: the declared Authentik user's email when there is one,
  # else the same derivation Immich uses for its admin (owner@rootDomain).
  ownerEmail =
    if cfg.adminEmail != null then
      cfg.adminEmail
    else
      config.lanbat.deployment.authentik.users.${cfg.adminUser}.email
        or "${cfg.adminUser}@${config.lanbat.deployment.rootDomain}";

  forgejoSettings = {
    options = {
      adminUser = lib.mkOption {
        type = lib.types.str;
        default = "akadmin";
        description = ''
          Authentik user pre-created as Forgejo's admin (break-glass). Its OIDC
          login links to this account, so it must be the authentik username.
        '';
      };
      adminEmail = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = ''
          Email of the admin account. Null takes the declared Authentik user's
          email (lanbat.deployment.authentik.users.<adminUser>.email), or
          <adminUser>@<rootDomain> when the user is not declared.
        '';
      };
    };
  };
in
{
  # The schema is merged into lanbat.services.forgejo.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.forgejo = forgejoSettings;

  lanbat.services.forgejo = {
    subdomain = "git";
    inherit port;
    extraPorts = [ 2222 ]; # Forgejo's built-in SSH server
    auth = "app";
    # Forgejo's OIDC callback. The source name is part of the path, so keep
    # authSource above in step with the auth source the bootstrap adds.
    oidc.redirectPaths = [ "/user/oauth2/${authSource}/callback" ];
    access.groups = [ "git" ];
    tier = "workload";
    state = [ "forgejo" ];
    units = [
      "forgejo"
      "forgejo-bootstrap"
    ];
    # The nixpkgs module creates the forgejo user itself (no account.uid is
    # pinned). Its preStart copies app.ini into custom/conf and the database
    # lives in data/, so the workload layer must create those on the mount
    # before the service starts: tmpfiles only made them on the locked stub.
    workloadDirs = {
      "forgejo".user = "forgejo";
      "forgejo/custom".user = "forgejo";
      "forgejo/custom/conf".user = "forgejo";
      "forgejo/data".user = "forgejo";
      "forgejo/log".user = "forgejo";
      "forgejo/repositories".user = "forgejo";
    };
    secrets.forgejo-env = { };
    dashboard = {
      group = "Utilities";
      name = "Forgejo";
      description = "Git hosting";
    };
  };

  services.forgejo = {
    enable = true;

    settings = {
      server = {
        # Localhost HTTP; Caddy is the public entry point.
        HTTP_ADDR = "127.0.0.1";
        HTTP_PORT = port;
        DOMAIN = "git.${domain}";
        ROOT_URL = "https://git.${domain}/";

        # SSH clone/push through Forgejo's built-in server on 2222, separate
        # from the admin sshd on 22. SSH_DOMAIN and SSH_PORT are what the clone
        # URLs show: ssh://git@git.<domain>:2222/<owner>/<repo>.git.
        DISABLE_SSH = false;
        START_SSH_SERVER = true;
        SSH_LISTEN_HOST = "0.0.0.0";
        SSH_LISTEN_PORT = 2222;
        SSH_PORT = 2222;
        SSH_DOMAIN = "git.${domain}";
      };

      # Accounts come from OIDC auto-registration, not open signup.
      service.DISABLE_REGISTRATION = true;
      session.COOKIE_SECURE = true;

      # Authentik as the OIDC client. ACCOUNT_LINKING = auto is what lets the
      # pre-created owner admin be taken over by the matching Authentik login
      # instead of becoming a second account.
      oauth2_client = {
        ENABLE_AUTO_REGISTRATION = true;
        ACCOUNT_LINKING = "auto";
        # Forgejo accepts userid, nickname or email here; Authentik's nickname
        # claim is the username, so it is the one that links to the owner.
        USERNAME = "nickname";
        OPENID_CONNECT_SCOPES = "openid email profile";
        UPDATE_AVATAR = true;
        REGISTER_EMAIL_CONFIRM = false;
      };
    };
  };

  # SSH clone/push. Forgejo's built-in server listens on 2222; the admin sshd
  # stays on 22, so this adds no way into the host itself.
  networking.firewall.allowedTCPPorts = [ 2222 ];

  # The state directory is a bind mount from the workload layer. On an
  # incremental switch (workload-online.target already active) the mount can
  # start late, so order the secret helper and the service after it. Otherwise
  # the three credential files can land on two different filesystems: the
  # mount appears between the writes, hiding the first one.
  systemd.services.forgejo-secrets = {
    requires = [ "var-lib-forgejo.mount" ];
    after = [ "var-lib-forgejo.mount" ];
  };

  # The OIDC token exchange calls https://auth.<domain> server-side; trust
  # Caddy's internal CA (global environment.variables do not reach units).
  systemd.services.forgejo = {
    requires = [ "var-lib-forgejo.mount" ];
    after = [ "var-lib-forgejo.mount" ];
  }
  // lib.optionalAttrs hasSso {
    environment.SSL_CERT_FILE = "/var/lib/caddy-local-ca/ca-certificates.crt";
  };

  # Create the owner admin and register the OIDC source once Forgejo is up.
  # Idempotent: it checks for the account and the auth source first.
  systemd.services.forgejo-bootstrap = {
    description = "Create Forgejo's owner admin and register Authentik OIDC";
    after = [ "forgejo.service" ];
    wants = [ "forgejo.service" ];
    path = [
      config.services.forgejo.package
      pkgs.git
      pkgs.gnugrep
      pkgs.gawk
      pkgs.coreutils
    ];
    environment = {
      USER = "forgejo";
      HOME = "/var/lib/forgejo";
      FORGEJO_WORK_DIR = "/var/lib/forgejo";
      FORGEJO_CUSTOM = "/var/lib/forgejo/custom";
    }
    // lib.optionalAttrs hasSso {
      # The discovery URL is https, signed by Caddy's internal CA when the
      # token is exchanged; trust it here too in case the CLI resolves it
      # while adding the source.
      SSL_CERT_FILE = "/var/lib/caddy-local-ca/ca-certificates.crt";
    };
    serviceConfig = {
      Type = "oneshot";
      User = "forgejo";
      Group = "forgejo";
      RemainAfterExit = true;
      Restart = "on-failure";
      RestartSec = "30s";
    };
    script = ''
      set -euo pipefail
      set -a
      . ${config.lanbat.secrets.forgejo-env.path}
      set +a

      if forgejo admin user list | awk '{print $2}' | grep -qx ${lib.escapeShellArg cfg.adminUser}; then
        echo "Forgejo admin ${cfg.adminUser} already exists"
      else
        forgejo admin user create \
          --username ${lib.escapeShellArg cfg.adminUser} \
          --email ${lib.escapeShellArg ownerEmail} \
          --password "$FORGEJO_ADMIN_PASSWORD" \
          --admin \
          --must-change-password=false
      fi

      ${lib.optionalString hasSso ''
        if forgejo admin auth list | grep -q ${lib.escapeShellArg authSource}; then
          echo "Forgejo OIDC source ${authSource} already exists"
        else
          forgejo admin auth add-oauth \
            --name ${lib.escapeShellArg authSource} \
            --provider openidConnect \
            --key forgejo \
            --secret "$FORGEJO_OAUTH_CLIENT_SECRET" \
            --auto-discover-url https://auth.${domain}/application/o/forgejo/.well-known/openid-configuration
        fi
      ''}
    '';
  };
}
