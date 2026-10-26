# services/authentik/blueprints.nix
#
# Authentik blueprints: providers, applications, the embedded outpost, and the
# instance's users and groups (deployment.authentik).
#
# Applied by Authentik on startup from /blueprints/custom/, with state
# "present" (idempotent create/update) throughout.
#
# The app catalogue is generated from the service descriptions by
# ./catalogue.nix, which documents the rules; there is nothing to edit here
# when a service is added. A service gets:
#   - a proxy provider, an application and a place on the embedded outpost
#     when it sets auth = "forward-auth";
#   - an OAuth2 provider and an application when it sets oidc.
# Services placed on this host are included, and so is every service with a
# subdomain that runs only on other hosts, whose vhost Caddy serves from here.
#
# The deployment.authentik option (lanbat.deployment.authentik) is turned into
# the users blueprint the same way: every named group is created if missing,
# and every declared user is created with — or brought to — exactly its
# declared name, email and full group membership. Passwords are never set by
# the blueprint; they are set in the instance's UI.
#
# Secrets
# -------
# OIDC client secrets are stored in authentik-oidc-secrets.age and injected
# into the authentik containers via environmentFiles.  The blueprint reads
# them with the !Env tag so they never appear in the Nix store.
#
# File format for authentik-oidc-secrets.age (one KEY=value per line, one
# line per OIDC client, AUTHENTIK_<NAME>_CLIENT_SECRET unless the service
# sets oidc.secretVariable):
#   AUTHENTIK_GRAFANA_CLIENT_SECRET=<40+ random chars>
#   AUTHENTIK_NEXTCLOUD_CLIENT_SECRET=<40+ random chars>
#   AUTHENTIK_IMMICH_CLIENT_SECRET=<40+ random chars>
#   AUTHENTIK_HA_CLIENT_SECRET=<40+ random chars>
#   AUTHENTIK_JELLYFIN_CLIENT_SECRET=<40+ random chars>
#   AUTHENTIK_ROMM_CLIENT_SECRET=<40+ random chars>
#   AUTHENTIK_VAULTWARDEN_CLIENT_SECRET=<40+ random chars>
#
# Each secret must also appear in the corresponding service env file so the
# service side knows the shared secret:
#   grafana-env.age       → GF_AUTH_GENERIC_OAUTH_CLIENT_SECRET=<grafana-value>
#   nextcloud-oidc-env.age → NEXTCLOUD_OIDC_CLIENT_ID=nextcloud
#                            NEXTCLOUD_OIDC_CLIENT_SECRET=<nextcloud-value>
#   immich-oidc-env.age   → IMMICH_OAUTH_CLIENT_ID=immich
#                            IMMICH_OAUTH_CLIENT_SECRET=<immich-value>
#   romm-oidc-env.age     → OIDC_CLIENT_SECRET=<romm-value>
#   vaultwarden-env.age   → SSO_CLIENT_SECRET=<vaultwarden-value>
#
# Home Assistant requires manual UI setup on its side:
#   Settings → Devices & Services → Add Integration → search "Authentik"
#   (or use HACS: https://github.com/jchonig/ha-authentik)
{
  config,
  pkgs,
  lib,
  ...
}:

let
  yaml = import ./yaml.nix { inherit lib; };

  # Services with a subdomain that run only on other hosts, from the
  # profile-wide table. Caddy on this host serves their vhosts
  # (modules/wiring/caddy.nix), so their forward-auth providers and OIDC
  # clients belong on this Authentik too. The port is only used for the
  # internal_host that forward_single mode ignores, and is left out for them.
  remoteServices =
    lib.mapAttrs
      (
        name: entry:
        {
          inherit name;
          port = null;
        }
        // removeAttrs entry.web [ "caddy" ]
      )
      (
        lib.filterAttrs (
          name: entry: (entry.web or null) != null && !(config.lanbat.services ? ${name})
        ) config.lanbat.endpoints
      );

  catalogue = import ./catalogue.nix { inherit lib; } {
    inherit (config.lanbat.deployment) domain authentik;
    authentikHost = "https://${config.lanbat.services.authentik.subdomain}.${config.lanbat.deployment.domain}";
    services = config.lanbat.services // remoteServices;
  };

  header = ''
    # Generated from lanbat.services by services/authentik/catalogue.nix.
  '';

  proxyBlueprint = pkgs.writeText "10-proxy-providers.yaml" (header + yaml.render catalogue.proxy);
  oidcBlueprint = pkgs.writeText "20-oidc-providers.yaml" (header + yaml.render catalogue.oidc);
  usersBlueprint = pkgs.writeText "30-users.yaml" (header + yaml.render catalogue.users);

  blueprintsDir = pkgs.runCommand "authentik-blueprints" { } ''
    mkdir -p $out
    cp ${proxyBlueprint} $out/10-proxy-providers.yaml
    cp ${oidcBlueprint}  $out/20-oidc-providers.yaml
    cp ${usersBlueprint} $out/30-users.yaml
  '';
in
{
  options.lanbat.authentik.blueprints = lib.mkOption {
    type = lib.types.attrsOf lib.types.anything;
    internal = true;
    readOnly = true;
    description = ''
      The generated blueprints as Nix values, keyed "proxy", "oidc" and
      "users", so tests can inspect the catalogue without building the YAML.
    '';
  };

  config = {
    lanbat.authentik.blueprints = catalogue;

    # Mount blueprints into both containers.  The Nix store path is read-only
    # on the host, so :ro is both safe and accurate.
    virtualisation.oci-containers.containers."authentik-server".volumes = [
      "${blueprintsDir}:/blueprints/custom:ro"
    ];
    virtualisation.oci-containers.containers."authentik-worker".volumes = [
      "${blueprintsDir}:/blueprints/custom:ro"
    ];

    # Inject OIDC client secrets so the blueprint can read them via !Env.
    # These are appended to the existing environmentFiles list (which already
    # contains authentik-env from authentik.nix).
    virtualisation.oci-containers.containers."authentik-server".environmentFiles = [
      config.lanbat.secrets.authentik-oidc-secrets.path
    ];
    virtualisation.oci-containers.containers."authentik-worker".environmentFiles = [
      config.lanbat.secrets.authentik-oidc-secrets.path
    ];
  };
}
