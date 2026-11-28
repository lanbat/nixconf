# services/stash.nix
#
# Stash — media organiser for a personal video library — with Stash-VR, the
# feed that VR players (DeoVR, HereSphere) read the library through.
#
# On-demand: yes (modules/wiring/on-demand.nix). Caddy routes to the
# activator, which starts Stash on the first request; it stops after 30
# minutes idle. Stash-VR starts and stops with it.
#
# Storage:
#   /var/lib/stash/config/    — config.yml and the SQLite database
#   /var/lib/stash/metadata/  — scene exports and scrape caches
#   /var/lib/stash/blobs/     — cover images
#   /var/lib/stash/cache/     — transcode cache
#   /var/lib/stash/generated/ — sprites, previews and thumbnails
#   /var/lib/stash/vr/        — Stash-VR's config
#   /srv/storage/<drive>/<libraryPath>
#                             — the video library on the Pi, mounted read-only
#
# Settings (lanbat.services.stash.settings): drive and libraryPath place the
# library on Pi storage; the defaults are drive b, media/adult, the folder that
# is for the private group alone (services/samba.nix, modules/storage/storage.nix).
#
# Auth: forward auth in front of Stash's UI, open to the Authentik group
# "private" alone (access.groups). Stash itself runs without a login, reachable
# only on the server's loopback and through Caddy. The Authentik group is not
# the Unix group: put the same people in it (deployment.authentik.users) that
# you add to the private Unix group for the Samba share. An empty or missing
# group locks everyone out, never in. Stash-VR
# reads Stash over loopback and listens on 9666, which the firewall keeps
# closed: a headset can't reach it yet (see docs/operations.md).
#
# Hardware transcoding: the container gets /dev/dri (VAAPI); the account is in
# the render group for it.
{
  config,
  lib,
  ...
}:

let
  cfg = config.lanbat.services.stash.settings;
  library = "/srv/storage/${cfg.drive}/${cfg.libraryPath}";
  # Caddy holds 9999 on this host; Stash's own default.
  stashPort = 9990;
  vrPort = 9666;
in
{
  lanbat.settingsSchema.stash = {
    options = {
      drive = lib.mkOption {
        type = lib.types.str;
        default = "b";
        description = ''
          Pi storage drive holding the video library, by its key in the storage
          host's storage.drives. Stash binds to that drive's NFS mount.
        '';
      };
      libraryPath = lib.mkOption {
        type = lib.types.str;
        default = "media/adult";
        description = "The video library, relative to the drive's mount (/srv/storage/<drive>).";
      };
    };
  };

  lanbat.services.stash = {
    subdomain = "stash";
    port = stashPort;
    extraPorts = [ vrPort ];
    auth = "forward-auth";
    # Same audience as the private Samba share and media/adult.
    access.groups = [ "private" ];
    tier = "workload";
    state = [ "stash" ];
    units = [
      "podman-stash"
      "podman-stash-vr"
    ];
    workloadDirs =
      lib.genAttrs
        [
          "stash"
          "stash/config"
          "stash/metadata"
          "stash/blobs"
          "stash/cache"
          "stash/generated"
          "stash/vr"
        ]
        (_: {
          user = "stash";
        });
    nfs = {
      drives = [ cfg.drive ];
      units = [ "podman-stash" ];
    };
    onDemand = {
      activatorPort = 9989;
      unit = "podman-stash.service";
      idleMinutes = 30;
    };
    account = {
      uid = 968;
      container = true;
      # private: media/adult is 2770 for that group and nobody else, so the
      # media group would see nothing there (modules/storage/storage.nix).
      # render: /dev/dri for VAAPI transcoding.
      extraGroups = [
        "private"
        "render"
      ];
    };
    dashboard = {
      group = "Media";
      name = "Stash";
      description = "Video library organiser (on-demand)";
    };
  };

  virtualisation.oci-containers.containers.stash = {
    image = "docker.io/stashapp/stash:latest@sha256:1099ffd7cc53f165916a07644874448011308a46eadbcd4edd328786e59b5eb9"; # v0.32.0

    environment = {
      STASH_STASH = "/data/";
      STASH_GENERATED = "/generated/";
      STASH_METADATA = "/metadata/";
      STASH_CACHE = "/cache/";
      STASH_PORT = toString stashPort;
      TZ = config.lanbat.deployment.timezone;
    };

    volumes = [
      "${library}:/data:ro"
      "/var/lib/stash/config:/root/.stash"
      "/var/lib/stash/metadata:/metadata"
      "/var/lib/stash/cache:/cache"
      "/var/lib/stash/blobs:/blobs"
      "/var/lib/stash/generated:/generated"
    ];

    extraOptions = [
      # Stash-VR reaches it on the loopback, and Caddy proxies to it there.
      "--network=host"
      # VAAPI transcoding; keep-groups carries the private and render groups.
      "--device=/dev/dri"
      "--group-add=keep-groups"
    ];

    podman.user = "stash";
    user = "0";
    # The on-demand activator starts it.
    autoStart = false;
  };

  virtualisation.oci-containers.containers.stash-vr = {
    image = "docker.io/ofl0w/stash-vr:latest@sha256:b5b4da30fbb668401c3e9cad285530845b9139c9c57c3f87e8e2d3268b42ed9a";

    environment = {
      STASH_GRAPHQL_URL = "http://127.0.0.1:${toString stashPort}/graphql";
      CONFIG_PATH = "/config";
      LOG_LEVEL = "info";
    };

    volumes = [ "/var/lib/stash/vr:/config" ];

    extraOptions = [ "--network=host" ];

    podman.user = "stash";
    user = "0";
    # Started and stopped with Stash (below).
    autoStart = false;
  };

  systemd.services.podman-stash = {
    after = [ "user@968.service" ];
    wants = [
      "user@968.service"
      "podman-stash-vr.service"
    ];
    serviceConfig = {
      Restart = lib.mkForce "on-failure";
      RestartSec = "10s";
    };
  };

  systemd.services.podman-stash-vr = {
    after = [ "podman-stash.service" ];
    # Stops when Stash stops (idle timer or unlock-workload teardown).
    partOf = [ "podman-stash.service" ];
    serviceConfig = {
      Restart = lib.mkForce "on-failure";
      RestartSec = "10s";
    };
  };

  # /var/lib/stash is workload state: the stub, bind mount and ownership come
  # from modules/wiring/workload-gate.nix. Don't add a tmpfiles rule here.
}
