# modules/core/services.nix
#
# The service interface. Every service file describes itself once under
# lanbat.services.<name>, and the wiring modules (modules/wiring/) turn those
# descriptions into Caddy vhosts, workload gating, NFS dependencies,
# on-demand activators, container accounts, secrets and Homepage
# entries. modules/wiring/checks.nix rejects inconsistent descriptions at
# evaluation time.
#
# Importing a service file enables the service; there is no enable option.
#
# Example (services/jellyfin.nix):
#
#   lanbat.services.jellyfin = {
#     subdomain = "media";
#     port = 8096;
#     apiClients = true;
#     tier = "workload";
#     state = [ "jellyfin" ];
#     units = [ "jellyfin" ];
#     nfs.drives = [ "a" ];
#     account = { uid = 992; extraGroups = [ "media" ]; };
#     dashboard = { group = "Media"; name = "Jellyfin"; description = "Media server"; };
#   };
{ config, lib, ... }:

let
  inherit (lib) mkOption types;

  # The host's configuration, for the submodules below that shadow config.
  hostConfig = config;

  dirSubmodule = types.submodule (
    { config, ... }:
    {
      options = {
        user = mkOption {
          type = types.str;
          description = "Owner of the directory.";
        };
        group = mkOption {
          type = types.str;
          default = config.user;
          defaultText = lib.literalExpression "user";
          description = "Group of the directory.";
        };
        mode = mkOption {
          type = types.str;
          default = "0750";
          description = "Access mode of the directory.";
        };
      };
    }
  );

  secretSubmodule =
    defaultOwner:
    types.submodule {
      options = {
        enable = mkOption {
          type = types.bool;
          default = true;
          example = lib.literalExpression "config.lanbat.deployment.haLlm != null";
          description = ''
            Whether the secret is required. Set it from the setting that uses
            the secret, so that a feature which is off needs no secret: a
            requirement that is off is not provisioned, and lanbat.secrets
            has no entry for it.
          '';
        };
        owner = mkOption {
          type = types.str;
          default = defaultOwner;
          description = "User that can read the decrypted secret. Defaults to the service account, or the service name.";
        };
        group = mkOption {
          type = types.nullOr types.str;
          default = null;
          description = "Group of the decrypted secret (the provider's default when null).";
        };
        mode = mkOption {
          type = types.nullOr types.str;
          default = null;
          description = "Mode of the decrypted secret (the provider's default, 0400, when null).";
        };
      };
    };

  endpointSubmodule = types.submodule {
    options = {
      scheme = mkOption {
        type = types.str;
        default = "http";
        example = "mqtt";
        description = "Scheme consumers use to reach the service.";
      };
      port = mkOption {
        type = types.port;
        description = "Port the service listens on for consumers.";
      };
      transport = mkOption {
        type = types.enum [
          "overlay"
          "lan"
        ];
        # Read with `or` because a host assembled without the settings module,
        # as the pure-eval tests do, has no deployment to ask.
        default =
          if (config.lanbat.deployment.overlay.provider or "none") == "none" then "lan" else "overlay";
        defaultText = lib.literalExpression ''if deployment.overlay.provider == "none" then "lan" else "overlay"'';
        description = ''
          Network consumers reach the service over. "overlay" is the default
          whenever the profile runs one; "lan" keeps an edge on the LAN address
          regardless, for a path that must not depend on the overlay being up.
        '';
      };
    };
  };

  serviceSubmodule = types.submodule (
    {
      name,
      config,
      options,
      ...
    }:
    {
      options = {
        name = mkOption {
          type = types.str;
          internal = true;
          readOnly = true;
          default = name;
          defaultText = lib.literalExpression "<the attribute name>";
          description = ''
            The service's own name, so that wiring which iterates over the
            values alone can still say which service it is talking about.
          '';
        };

        # ── Service configuration (the service module itself) ─────────────────
        settings = mkOption {
          type = types.submoduleWith {
            shorthandOnlyDefinesConfig = true;
            modules = [
              { freeformType = types.attrsOf types.anything; }
            ]
            ++ lib.optional (
              hostConfig.lanbat.settingsSchema ? ${name}
            ) hostConfig.lanbat.settingsSchema.${name};
          };
          default = { };
          example = {
            detector.device = "CPU";
            retention.motionDays = 30;
          };
          description = ''
            The service's own configuration, rendered by its module.

            A service declares the keys it accepts as typed options of this
            submodule, through lanbat.settingsSchema.<name>:

              lanbat.settingsSchema.frigate = {
                options.record = lib.mkOption { type = ...; };
              };

            Once it declares any, modules/wiring/checks.nix rejects a key it
            does not declare, unless settingsFreeform is set. A service that
            declares none keeps the whole set freeform: every key reaches its
            module, which renders what it knows.

            A module supplies its own values with lib.mkDefault in config, never
            as an option default, so a profile or a user module overrides them
            without lib.mkForce.
          '';
        };

        settingsKeys = mkOption {
          type = types.listOf types.str;
          internal = true;
          readOnly = true;
          default = lib.attrNames (
            removeAttrs (options.settings.type.getSubOptions [ ]) [
              "_module"
              "_freeformOptions"
            ]
          );
          defaultText = lib.literalMD "the options declared under `settings`";
          description = "The settings keys this service declares as options.";
        };

        settingsFreeform = mkOption {
          type = types.bool;
          default = config.settingsKeys == [ ];
          defaultText = lib.literalExpression "settingsKeys == [ ]";
          description = ''
            Whether settings keys the service does not declare are passed
            through rather than rejected. The default is strict as soon as the
            service declares any key; set it for a service that models some keys
            and renders the rest into its configuration unchanged.
          '';
        };

        implementation = mkOption {
          type = types.enum [
            "core"
            "none"
          ];
          default = "core";
          description = ''
            core: the service module configures the service.
            none: the module contributes this description only and you supply
            the implementation yourself. The wiring still applies, so the vhost,
            tier gating, account and secrets keep working.
          '';
        };

        # ── Web exposure (modules/wiring/caddy.nix) ───────────────────────────
        subdomain = mkOption {
          type = types.nullOr types.str;
          default = null;
          example = "media";
          description = ''
            Serve the service at https://<subdomain>.<lanbat.domain> through Caddy.
            When the service runs on a host without Caddy, Caddy proxies to its
            endpoint on that host, and that host's generated policy admits Caddy's.
          '';
        };

        port = mkOption {
          type = types.nullOr types.port;
          default = null;
          description = "Local HTTP port of the service. Caddy proxies to it (or to the on-demand activator).";
        };

        extraPorts = mkOption {
          type = types.listOf types.port;
          default = [ ];
          description = "Other ports the service listens on. Only used to detect port clashes.";
        };

        auth = mkOption {
          type = types.enum [
            "app"
            "forward-auth"
            "none"
          ];
          default = "app";
          description = ''
            Who authenticates users of the web UI:
            - app: the service itself (native OIDC or its own accounts)
            - forward-auth: Caddy checks the Authentik session first
            - none: open to the LAN on purpose
          '';
        };

        access.groups = mkOption {
          type = types.listOf types.str;
          default = [ ];
          example = [ "authentik Admins" ];
          description = ''
            Authentik groups whose members may open the service; empty admits
            every Authentik user. Enforced at the service's Authentik
            applications (forward auth and OIDC), so it needs one of them.
            A group Authentik does not have yet is created, empty, so a
            mistyped name locks the service rather than opening it.
            Blueprints only add and update, so a group removed from this list
            keeps its binding until it is deleted in Authentik's admin UI
            (Applications → the application → Policy / Group / User Bindings).
          '';
        };

        apiClients = mkOption {
          type = types.bool;
          default = false;
          description = ''
            Whether non-browser clients (mobile or desktop apps) call the service
            directly. With forward-auth, Caddy exempts /auth/token* and /api/*
            so clients can authenticate with the app while the browser UI stays
            behind Authentik.
          '';
        };

        # ── Single sign-on (services/authentik/catalogue.nix) ─────────────────
        oidc = mkOption {
          type = types.nullOr (
            types.submodule {
              options = {
                redirectPaths = mkOption {
                  type = types.listOf types.str;
                  default = [ ];
                  example = [ "/login/generic_oauth" ];
                  description = "Login callback paths on https://<subdomain>.<domain>.";
                };
                redirectUris = mkOption {
                  type = types.listOf types.str;
                  default = [ ];
                  example = [ "app.immich:///oauth-callback" ];
                  description = "Further callback URIs, such as a mobile app's custom scheme.";
                };
                secretVariable = mkOption {
                  type = types.nullOr types.str;
                  default = null;
                  description = ''
                    Variable in the authentication provider's client-secret file
                    that holds this client's secret. Null lets the provider
                    derive it from the service name.
                  '';
                };
                extraScopes = mkOption {
                  type = types.listOf types.str;
                  default = [ ];
                  example = [ "offline_access" ];
                  description = ''
                    Extra OpenID Connect scopes beyond the catalogue's default
                    `openid`, `email` and `profile`, each appended to the
                    provider's property mappings. The scope must exist as an
                    Authentik scope mapping (the system blueprint provides the
                    standard ones, such as `offline_access`).
                  '';
                };
              };
            }
          );
          default = null;
          description = ''
            Register the service as an OpenID Connect client of the
            authentication provider, with the service name as client ID.
            Independent of auth: a forward-auth service can be a client too.
          '';
        };

        caddy = {
          extraConfig = mkOption {
            type = types.lines;
            default = "";
            description = "Extra Caddyfile directives for the vhost, placed before the generated reverse_proxy.";
          };
          proxyOptions = mkOption {
            type = types.lines;
            default = "";
            description = "Directives inside the generated reverse_proxy block.";
          };
          authBypassPaths = mkOption {
            type = types.listOf types.str;
            default = [ ];
            example = [
              "/info"
              "/ws"
            ];
            description = ''
              URL paths that bypass Authentik forward-auth and reach the service
              directly. Used when the app must authenticate or discover itself
              (Music Assistant probes /info and /ws before its own login screen).
            '';
          };
          auditLog = mkOption {
            type = types.bool;
            default = false;
            description = ''
              Record who does what on the subdomain. Each line of the vhost's
              access log (/var/log/caddy/access-<subdomain>.<domain>.log, JSON,
              kept 90 days) names the signed-in user in a `user` field, and the
              log keeps page loads (GET /) and requests that change something
              (any method but GET and HEAD), leaving out the reads a web UI
              polls with. Needs auth = "forward-auth" and a provider that names
              the user (lanbat.authProvider.userHeader). Requests to
              authBypassPaths are logged without a user.
            '';
          };
        };

        # ── Cross-host endpoints (modules/wiring/endpoints.nix) ───────────────
        endpoint = mkOption {
          type = types.nullOr endpointSubmodule;
          default = if config.port != null then { inherit (config) port; } else null;
          defaultText = lib.literalExpression ''{ scheme = "http"; port = port; } when port is set, otherwise null'';
          description = ''
            What this service publishes for other services to consume. Consumers
            name it in their own consumes list and the wiring resolves the
            address, whichever host each of them runs on.

            This is part of the description, so it must not depend on the
            resolved endpoints of other services.
          '';
        };

        consumes = mkOption {
          type = types.listOf types.str;
          default = [ ];
          example = [
            "mosquitto"
            "postgresql"
          ];
          description = ''
            Services this one connects to, by name. modules/wiring/endpoints.nix
            resolves each to an address, opens the provider's firewall to this
            host and orders the units: locally with After= and BindsTo=,
            remotely with a restart policy.
          '';
        };

        # ── Storage tier (modules/wiring/workload-gate.nix) ───────────────────
        tier = mkOption {
          type = types.enum [
            "always-on"
            "workload"
          ];
          default = "always-on";
          description = ''
            always-on: starts at boot, state on the unencrypted host root.
            workload: starts only after unlock-workload, state on the workload LUKS layer.
          '';
        };

        state = mkOption {
          type = types.listOf types.str;
          default = [ ];
          example = [ "jellyfin" ];
          description = "Directories under /var/lib that live on the workload layer (workload tier only).";
        };

        workloadDirs = mkOption {
          type = types.attrsOf dirSubmodule;
          default = { };
          example = {
            "immich/thumbs".user = "immich";
          };
          description = ''
            Directories under /mnt/workload to create with this ownership when the
            layer is unlocked. An always-on service may declare one too (voice-id's
            voiceprints): it keeps running while the layer is locked and must cope
            without the directory until then.
          '';
        };

        units = mkOption {
          type = types.listOf types.str;
          default = [ ];
          example = [ "podman-immich-server" ];
          description = "systemd services (without .service) that make up the service.";
        };

        # ── Pi storage (modules/wiring/nfs.nix) ───────────────────────────────
        nfs = {
          storageHost = mkOption {
            type = types.nullOr types.str;
            default = null;
            description = ''
              Host key of the storage-pi that exports the drives. Defaults to
              deployment.primaryStorage when null.
            '';
          };
          drives = mkOption {
            type = types.listOf (types.strMatching "[a-z0-9]+");
            default = [ ];
            example = [ "a" ];
            description = ''
              Pi storage drives the service reads or writes (/srv/storage/<drive>),
              by their keys in the storage host's storage.drives.
              modules/wiring/checks.nix rejects a name that host does not have.
            '';
          };
          units = mkOption {
            type = types.listOf types.str;
            default = config.units;
            defaultText = lib.literalExpression "units";
            description = "Units that stop when a drive disappears.";
          };
        };

        # ── On-demand activation (modules/wiring/on-demand.nix) ───────────────
        onDemand = mkOption {
          type = types.nullOr (
            types.submodule {
              options = {
                activatorPort = mkOption {
                  type = types.port;
                  description = "Port of the activator that Caddy proxies to.";
                };
                unit = mkOption {
                  type = types.str;
                  default = "${lib.head config.units}.service";
                  defaultText = lib.literalExpression ''"''${head units}.service"'';
                  description = "Unit the activator starts and the idle timer stops.";
                };
                idleMinutes = mkOption {
                  type = types.ints.positive;
                  default = 30;
                  description = "Stop the service after this long without requests.";
                };
              };
            }
          );
          default = null;
          description = "Start the service on the first request and stop it when idle.";
        };

        # ── Service account (modules/wiring/accounts.nix) ─────────────────────
        account = mkOption {
          type = types.nullOr (
            types.submodule {
              options = {
                name = mkOption {
                  type = types.str;
                  default = name;
                  defaultText = lib.literalExpression "<service name>";
                  description = "User and group name.";
                };
                uid = mkOption {
                  type = types.ints.between 900 999;
                  description = "UID and GID. Must be unique; checked at evaluation.";
                };
                container = mkOption {
                  type = types.bool;
                  default = false;
                  description = "Set up rootless Podman: linger, a home directory and sub-UID/GID ranges.";
                };
                extraGroups = mkOption {
                  type = types.listOf types.str;
                  default = [ ];
                  description = "Supplementary groups.";
                };
                extraSubGidRanges = mkOption {
                  type = types.listOf (types.attrsOf types.int);
                  default = [ ];
                  example = [
                    {
                      startGid = 303;
                      count = 1;
                    }
                  ];
                  description = "Extra sub-GID ranges, e.g. host device groups to pass into containers.";
                };
              };
            }
          );
          default = null;
          description = "Dedicated user and group with a pinned UID/GID.";
        };

        # ── Secrets (modules/wiring/secrets.nix) ──────────────────────────────
        secrets = mkOption {
          type = types.attrsOf (
            secretSubmodule (if config.account != null then config.account.name else name)
          );
          default = { };
          example = {
            vaultwarden-env = { };
          };
          description = ''
            Secrets this service requires, by name. The profile's secrets
            provider satisfies each one (under agenix, from
            <deployment.secrets.root>/<name>.age), and the service reads the
            decrypted file as lanbat.secrets.<name>.path. A requirement whose
            enable is false is not provisioned.
          '';
        };

        readsSecrets = mkOption {
          type = types.listOf types.str;
          default = [ ];
          example = [ "mosquitto-frigate-pass" ];
          description = ''
            Shared secrets: secrets this service reads that another service on
            the same host declares in its own secrets. The declaring service
            sets owner, group and mode once; this one reads
            lanbat.secrets.<name>.path like the declarer, from a unit that the
            declaration lets read the file (root, the owner or the group), and
            is listed in lanbat.secrets.<name>.readers.

            Make the entry as conditional as the read. Evaluation fails, naming
            both sides, when no service on the host declares the secret or its
            requirement is off.
          '';
        };

        # ── Dashboard (services/homepage.nix) ─────────────────────────────────
        dashboard = mkOption {
          type = types.nullOr (
            types.submodule {
              options = {
                group = mkOption {
                  type = types.str;
                  description = "Homepage group the entry appears in.";
                };
                name = mkOption {
                  type = types.str;
                  description = "Display name.";
                };
                description = mkOption {
                  type = types.str;
                  description = "One-line description.";
                };
                icon = mkOption {
                  type = types.str;
                  default = name;
                  defaultText = lib.literalExpression "<service name>";
                  description = "Homepage icon name.";
                };
                widget = mkOption {
                  type = types.nullOr (types.attrsOf types.anything);
                  default = null;
                  description = "Homepage widget settings. url defaults to the service URL.";
                };
              };
            }
          );
          default = null;
          description = "Show the service on the Homepage dashboard.";
        };
      };
    }
  );
in
{
  options.lanbat.services = mkOption {
    type = types.attrsOf serviceSubmodule;
    default = { };
    description = "Self-descriptions of the services on this host. See modules/core/services.nix.";
  };

  options.lanbat.endpoints = mkOption {
    type = types.attrsOf (types.attrsOf types.anything);
    internal = true;
    default = { };
    description = ''
      Every service in this deployment profile and where it runs, keyed by
      service name: the host key, that host's address and hostname, the
      endpoint it publishes, its service account, and the part of its web
      description (subdomain, auth, caddy, oidc) a Caddy or Authentik on
      another host needs to serve it.

      lib/ builds this by evaluating each host's service descriptions once,
      before building the hosts themselves, and hands the result to every host.
      That first pass is cheap because the module system is lazy: reading
      lanbat.services forces the descriptions, not the systemd units or the
      package set behind them.

      Because the first pass runs with this table empty, a description must not
      depend on it. Settings and configuration bodies may; endpoint, account
      and the web description may not, or the two passes would disagree about
      the very thing being resolved.
    '';
  };

  options.lanbat.hasService = mkOption {
    type = types.functionTo types.bool;
    internal = true;
    readOnly = true;
    description = ''
      Whether a service is part of this deployment, by name.

      A service module uses it to make an integration with another service
      conditional, so that a deployment which leaves that other service out
      still evaluates and does not start a unit that would retry forever
      against something absent.

      It answers for this host only. Once lib/ resolves the endpoint table
      across the profile, this is the single definition that widens to answer
      for the whole deployment, and its callers do not change.
    '';
  };

  options.lanbat.settingsSchema = mkOption {
    type = types.attrsOf types.deferredModule;
    default = { };
    example = lib.literalExpression ''
      {
        frigate = {
          options.record.days = lib.mkOption {
            type = lib.types.ints.positive;
            description = "Days of recordings to keep.";
          };
        };
      }
    '';
    description = ''
      The settings a service accepts, by service name: a module whose options
      are the keys of lanbat.services.<name>.settings. It is merged into that
      service's settings submodule, so the declared keys are type checked and
      modules/wiring/checks.nix rejects any other key, naming the service and
      the key, unless the service sets settingsFreeform.
    '';
  };

  # Which placement-dependent wiring this host carries. Each wiring module sets
  # its own flag, so modules/wiring/checks.nix can reject a service placed on a
  # host whose role leaves that wiring out, rather than let its description be
  # silently ignored there.
  options.lanbat.wiring = {
    onDemand = mkOption {
      type = types.bool;
      internal = true;
      default = false;
      description = "Whether modules/wiring/on-demand.nix is imported on this host.";
    };
    workloadGate = mkOption {
      type = types.bool;
      internal = true;
      default = false;
      description = "Whether modules/wiring/workload-gate.nix is imported on this host.";
    };
  };

  config.lanbat.hasService = name: config.lanbat.services ? ${name};
}
