# services/sonarr.nix
#
# Sonarr — TV show downloader and manager.
#
# Why Sonarr?
#   Like Radarr for movies, Sonarr renames and organizes TV episodes to
#   `<Show>/Season <NN>/<Show> - SxxEyy` and matches them to TheTVDB, so
#   Jellyfin recognizes every show (a single folder dumped in tv/ is otherwise
#   taken for one series). Its "Library Import" does this for the shows already
#   on disk, and future downloads are handled the same way on import.
#
# Why the NixOS module?
#   `services.sonarr` runs the upstream server as its own user; no reason to
#   containerize. Like Radarr, it has no OIDC, so it sits behind Caddy's
#   Authentik forward auth and is set to "External" authentication.
#
# Storage:
#   /var/lib/sonarr/.config/Sonarr/ — config, database (workload layer)
#   /srv/storage/a/media/tv          — the TV library, renamed in place
#   /srv/storage/b/media/documentary-series — documentary series too
#
# The media folders belong to qbt:media (mode 2775), so Sonarr joins the media
# group to read and rename the files; new files inherit the group via setgid.
#
# Root folders and the qBittorrent download client are configured by
# sonarr-bootstrap on first start. Indexers (Sonarr rejects Jackett's /all
# endpoint) and the library import that renames the shows already on disk are
# done in the web UI.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  port = 8989;
  bootstrap = pkgs.callPackage ../pkgs/servarr-bootstrap { };
  # The folders Sonarr manages; its Library Import matches and renames the
  # episodes already in them.
  rootFolders = "/srv/storage/a/media/tv /srv/storage/b/media/documentary-series";
  withJackett = config.lanbat.hasService "jackett";
in
{
  lanbat.services.sonarr = {
    subdomain = "sonarr";
    inherit port;
    auth = "forward-auth";
    tier = "workload";
    state = [ "sonarr" ];
    units = [
      "sonarr"
      "sonarr-bootstrap"
    ];
    workloadDirs =
      lib.genAttrs
        [
          "sonarr"
          "sonarr/.config"
          "sonarr/.config/Sonarr"
        ]
        (_: {
          user = "sonarr";
          mode = "0700";
        });
    nfs.drives = [
      "a"
      "b"
    ];
    dashboard = {
      group = "Downloads";
      name = "Sonarr";
      description = "TV show downloader and manager";
    };
  };

  services.sonarr = {
    enable = true;
    openFirewall = false;
    dataDir = "/var/lib/sonarr/.config/Sonarr";
  };

  # Read and rename the shows on the Pi (qbt:media, mode 2775). NFS uses
  # numeric IDs, so the group must be the shared media group (gid 988).
  users.users.sonarr.extraGroups = [ "media" ];

  # Bind loopback (Caddy proxies; the firewall keeps the port closed anyway,
  # as Jackett's --ListenPrivate does) and let Authentik sign users in.
  systemd.services.sonarr.environment = {
    SONARR__SERVER__BINDADDRESS = "127.0.0.1";
    SONARR__AUTH__METHOD = "External";
  };

  # Configure Sonarr once it is up: root folders, the qBittorrent download
  # client and the Jackett indexer. Idempotent, and it retries until Sonarr
  # has written its API key (and, with Jackett, until Jackett's is there too).
  systemd.services.sonarr-bootstrap = {
    description = "Configure Sonarr (root folders, qBittorrent, Jackett)";
    wantedBy = [ "multi-user.target" ];
    after = [ "sonarr.service" ] ++ lib.optional withJackett "jackett.service";
    wants = [ "sonarr.service" ] ++ lib.optional withJackett "jackett.service";
    path = [ bootstrap ];
    environment = {
      APP = "sonarr";
      SERVARR_URL = "http://127.0.0.1:${toString port}";
      CONFIG_XML = "/var/lib/sonarr/.config/Sonarr/config.xml";
      ROOT_FOLDERS = rootFolders;
      JACKETT_CONFIG = lib.optionalString withJackett "/var/lib/jackett/.config/Jackett/ServerConfig.json";
    };
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      User = "root";
      Restart = "on-failure";
      RestartSec = "30s";
    };
    script = "exec servarr-bootstrap";
  };
}
