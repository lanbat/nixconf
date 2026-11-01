# services/homepage.nix
#
# Homepage — service dashboard and LAN landing page at home.<domain>.
# No authentication: it's the entry point.
#
# The dashboard entries are generated from lanbat.services.<name>.dashboard,
# grouped by dashboard.group and sorted by name. Config files are built with
# Nix and mounted read-only, so they update on every deploy.
#
# Networking
# ----------
# Homepage uses host networking so widget requests can reach Caddy on
# 127.0.0.1:443. Caddy's internal CA root cert is mounted into the container
# and trusted via NODE_EXTRA_CA_CERTS. Caddy generates the cert on the first
# TLS request; if Homepage starts earlier, widgets error until it restarts.
#
# Widget credentials
# ------------------
# Widget API keys and passwords are not baked into the /nix/store. services.yaml
# is generated at container start from a manifest plus agenix secrets under
# /run/agenix. Service files reference secrets with `_secret` values in their
# dashboard.widget blocks. Run `bash secrets/generate-homepage-widgets.sh` on the
# server after deploy to create the API keys/tokens that can be automated.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  homepageConfig = pkgs.callPackage ../pkgs/homepage-config { };
  homepageStateDir = "/var/lib/homepage";
  homepageManifest = "${homepageStateDir}/manifest.json";
  homepageServicesYaml = "${homepageStateDir}/services.yaml";
  agenixDir = "/run/agenix";

  # Group order on the page, with icons. Groups not listed here come last.
  groupIcons = [
    {
      name = "Identity & Auth";
      icon = "mdi-shield-account";
    }
    {
      name = "Media";
      icon = "mdi-television-play";
    }
    {
      name = "Files & Sync";
      icon = "mdi-folder-sync";
    }
    {
      name = "Downloads";
      icon = "mdi-download";
    }
    {
      name = "Automation";
      icon = "mdi-home-automation";
    }
    {
      name = "Surveillance";
      icon = "mdi-cctv";
    }
    {
      name = "Monitoring";
      icon = "mdi-chart-line";
    }
    {
      name = "Utilities";
      icon = "mdi-tools";
    }
  ];

  onDashboard = lib.attrValues (
    lib.filterAttrs (_: svc: svc.dashboard != null) config.lanbat.services
  );
  url = svc: "https://${svc.subdomain}.${domain}";

  widgetEntry =
    svc:
    {
      url = url svc;
    }
    // svc.dashboard.widget;

  knownGroups = map (g: g.name) groupIcons;
  groups =
    knownGroups
    ++ lib.sort lib.lessThan (
      lib.subtractLists knownGroups (lib.unique (map (svc: svc.dashboard.group) onDashboard))
    );
  inGroup =
    group:
    lib.sort (a: b: a.dashboard.name < b.dashboard.name) (
      lib.filter (svc: svc.dashboard.group == group) onDashboard
    );

  manifest = {
    groups = map (group: {
      name = group;
      entries = map (svc: {
        name = svc.dashboard.name;
        href = url svc;
        description = svc.dashboard.description;
        icon = svc.dashboard.icon;
        widget = if svc.dashboard.widget != null then widgetEntry svc else null;
      }) (inGroup group);
    }) (lib.filter (group: inGroup group != [ ]) groups);
  };

  manifestJson = pkgs.writeText "homepage-manifest.json" (builtins.toJSON manifest);

  settingsYaml = pkgs.writeText "homepage-settings.yaml" ''
    title: Homelab
    theme: dark
    color: slate
    headerStyle: boxed
    layout:
    ${lib.concatMapStrings (g: ''
      ${"  "}${g.name}:
          icon: ${g.icon}
    '') groupIcons}
  '';

  widgetsYaml = pkgs.writeText "homepage-widgets.yaml" ''
    - resources:
        cpu: true
        memory: true
        disk: /
    - datetime:
        text_size: l
        format:
          dateStyle: long
          timeStyle: short
  '';

  generateServicesYaml = pkgs.writeShellScript "homepage-generate-services-yaml" ''
    set -euo pipefail
    install -d -m 0750 -o homepage -g homepage ${homepageStateDir}
    install -m 0640 -o homepage -g homepage ${manifestJson} ${homepageManifest}
    ${homepageConfig.generateServicesYaml} ${homepageManifest} ${agenixDir} ${homepageServicesYaml}
    chmod 0640 ${homepageServicesYaml}
    chown homepage:homepage ${homepageServicesYaml}
  '';
in
{
  lanbat.services.homepage = {
    subdomain = "home";
    port = 3000;
    auth = "none";
    account = {
      uid = 961;
      container = true;
    };
    secrets.homepage-widgets-env = { };
  };

  systemd.tmpfiles.rules = [
    "d ${homepageStateDir} 0750 homepage homepage -"
  ];

  systemd.services.podman-homepage = {
    after = [ "agenix-mount.service" ];
    serviceConfig.ExecStartPre = lib.mkBefore [ "+${generateServicesYaml}" ];
  };

  virtualisation.oci-containers.containers."homepage" = {
    image = "ghcr.io/gethomepage/homepage:v2.7.0@sha256:19c5c09184458a41b7b5915e71463a39f36cd33540f41bdbed008791b8182d5f";

    volumes = [
      "${homepageServicesYaml}:/app/config/services.yaml:ro"
      "${settingsYaml}:/app/config/settings.yaml:ro"
      "${widgetsYaml}:/app/config/widgets.yaml:ro"
      # Persisted Caddy root CA, for widget TLS verification.
      "/etc/caddy/ca-root.crt:/caddy-ca/root.crt:ro"
    ];

    extraOptions = [ "--network=host" ];

    environment = {
      HOMEPAGE_ALLOWED_HOSTS = "home.${domain},localhost";
      NODE_EXTRA_CA_CERTS = "/caddy-ca/root.crt";
    };

    podman.user = "homepage";
    user = "0";
    autoStart = true;
  };

}
