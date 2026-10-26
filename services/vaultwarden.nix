# services/vaultwarden.nix
#
# Vaultwarden — self-hosted Bitwarden-compatible password manager.
#
# Design
# ------
# - NixOS-native service (no container needed).
# - SQLite backend — simple and sufficient for a small homelab.
# - Listens on localhost:8222 (HTTP) and 3012 (WebSocket notifications).
# - Caddy terminates TLS and reverse-proxies both endpoints.
# - Login is Vaultwarden's own (Bitwarden): signups are disabled and users
#   are invited from the admin panel. When the identity provider runs on the
#   host, login goes through it instead — the catalogue provisions the
#   OAuth2 provider and application (client id "vaultwarden"), SSO signs in
#   by matching the email to an existing account, and the offline_access
#   scope lets Bitwarden clients refresh without a new consent.
# - Admin panel is protected by the admin token secret (not Authentik
#   forward auth, because Bitwarden clients need unauthenticated API access
#   to /api, /identity, etc.).
#
# Secrets
# -------
# vaultwarden-env.age — KEY=value lines the service reads:
#   ADMIN_TOKEN=<token> — used to access /admin.  Generate with:
#     echo -n "yourpassword" | argon2 $(openssl rand -base64 32) -id -t 3 \
#       -m 65540 -p 4 | grep "Encoded:" | cut -d' ' -f2
#     (a plain random string also works; Vaultwarden accepts it as-is)
#   SSO_CLIENT_SECRET=<secret> — the same value as
#     AUTHENTIK_VAULTWARDEN_CLIENT_SECRET in authentik-oidc-secrets.age;
#     generate both with secrets/generate-oidc-secrets.sh.
#
# State
# -----
# /var/lib/vaultwarden  — SQLite DB + attachments (back this up!)
#
# Always-on: yes.  No NFS dependency.
{ config, lib, ... }:

# SSO is only offered when the identity provider runs on this host;
# otherwise the admin-token login (master password) remains the way in.
let
  hasSso = config.lanbat.hasService "authentik";
in
{
  lanbat.services.vaultwarden = {
    subdomain = "vault";
    port = 8222;
    extraPorts = [ 3012 ]; # WebSocket notifications
    apiClients = true; # Bitwarden apps and browser extensions
    caddy.extraConfig = ''
      # WebSocket endpoint (browser extension live sync).
      handle /notifications/hub {
        reverse_proxy localhost:3012
      }
      handle /notifications/hub/negotiate {
        reverse_proxy localhost:3012
      }
    '';
    oidc = {
      redirectPaths = [ "/identity/connect/oidc-signin" ];
      # Refresh tokens, so Bitwarden clients stay signed in.
      extraScopes = [ "offline_access" ];
    };
    access.groups = [ "vaultwarden" ];
    tier = "workload";
    state = [ "vaultwarden" ];
    units = [ "vaultwarden" ];
    secrets.vaultwarden-env = { };
    dashboard = {
      group = "Utilities";
      name = "Vaultwarden";
      description = "Password manager";
    };
  };

  services.vaultwarden = {
    enable = true;

    # State dir — NixOS creates /var/lib/vaultwarden automatically.
    # The service runs as the "vaultwarden" user (created by the module).

    config = {
      # Bind to localhost only; Caddy is the public-facing entry point.
      ROCKET_ADDRESS = "127.0.0.1";
      ROCKET_PORT = 8222;

      # WebSocket notifications (used by browser extensions for live sync).
      WEBSOCKET_ENABLED = true;
      WEBSOCKET_ADDRESS = "127.0.0.1";
      WEBSOCKET_PORT = 3012;

      # Disable open signup — invite users from the admin panel.
      SIGNUPS_ALLOWED = false;
      INVITATIONS_ALLOWED = true;

      # Public URL — must match what Caddy exposes.
      DOMAIN = "https://vault.${config.lanbat.deployment.domain}";

      # Log level: warn is quiet enough for daily use.
      LOG_LEVEL = "warn";

      # SSO via the identity provider (see Design). Matching the email to an
      # existing account means the account must already exist (invited); a
      # fresh email cannot create one over SSO.
      SSO_ENABLED = hasSso;
      SSO_CLIENT_ID = "vaultwarden";
      SSO_AUTHORITY = "https://auth.${config.lanbat.deployment.domain}/application/o/vaultwarden/";
      SSO_SCOPES = "email profile offline_access";
      # SSO signs in by matching the OIDC email to a Vaultwarden account
      # (Bitwarden's model: the account is the vault, so one must exist — see
      # Design). Trust Authentik's verified email for an account created here
      # from the admin API, so signing in never needs an invitation email.
      SSO_SIGNUPS_MATCH_EMAIL = true;
      SSO_ALLOW_UNKNOWN_EMAIL_VERIFICATION = true;

      # ADMIN_TOKEN and SSO_CLIENT_SECRET come from the environmentFile below.
    };

    # KEY=value lines from the agenix-managed secret file (see Secrets).
    environmentFile = config.lanbat.secrets.vaultwarden-env.path;
  };

  # SSO token/userinfo calls hit https://auth.<domain> server-side; trust
  # the internal Caddy CA (global environment.variables do not reach units).
  systemd.services.vaultwarden = lib.mkIf hasSso {
    serviceConfig.Environment = [
      "SSL_CERT_FILE=/var/lib/caddy-local-ca/ca-certificates.crt"
    ];
  };
}
