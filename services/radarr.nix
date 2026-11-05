# services/radarr.nix
#
# Radarr — movie downloader and manager.
#
# Why Radarr?
#   qBittorrent saves torrents by category into the movies folder with their
#   release filenames, which Jellyfin often cannot match to TMDb (foreign and
#   transliterated titles, missing years, dots). Radarr renames and organizes
#   the movies to `<Title> (<Year>)` and matches them to TMDb, so Jellyfin
#   recognizes them. Its "Library Import" does this for the movies already on
#   disk, and every future download is handled the same way on import.
#
# Why the NixOS module?
#   `services.radarr` runs the upstream server as its own user; no reason to
#   containerize. Radarr has its own web UI and API but no OIDC, so it sits
#   behind Caddy's Authentik forward auth (like Jackett and qBittorrent) and is
#   set to "External" authentication: Authentik signs the browser in, Radarr
#   trusts it and keeps its API key for programmatic access.
#
# Storage:
#   /var/lib/radarr/.config/Radarr/ — config, database (workload layer)
#   /srv/storage/a/media/movies    — the movie library, renamed in place
#
# The media folders belong to qbt:media (mode 2775), so Radarr joins the media
# group to read and rename the files; new files inherit the group via setgid.
#
# Root folders and the qBittorrent download client are configured by
# radarr-bootstrap on first start. Indexers (Radarr rejects Jackett's /all
# endpoint) and the library import that renames the movies already on disk are
# done in the web UI.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  port = 7878;
  bootstrap = pkgs.callPackage ../pkgs/servarr-bootstrap { };
  # The folders Radarr manages; its Library Import matches and renames the
  # files already in them.
  rootFolders = "/srv/storage/a/media/movies /srv/storage/b/media/documentaries";
  withJackett = config.lanbat.hasService "jackett";
in
{
  lanbat.services.radarr = {
    subdomain = "radarr";
    inherit port;
    auth = "forward-auth";
    tier = "workload";
    state = [ "radarr" ];
    units = [
      "radarr"
      "radarr-bootstrap"
    ];
    workloadDirs =
      lib.genAttrs
        [
          "radarr"
          "radarr/.config"
          "radarr/.config/Radarr"
        ]
        (_: {
          user = "radarr";
          mode = "0700";
        });
    nfs.drives = [
      "a"
      "b"
    ];
    dashboard = {
      group = "Downloads";
      name = "Radarr";
      description = "Movie downloader and manager";
    };
  };

  services.radarr = {
    enable = true;
    openFirewall = false;
    dataDir = "/var/lib/radarr/.config/Radarr";
  };

  # Read and rename the movies on the Pi (qbt:media, mode 2775). NFS uses
  # numeric IDs, so the group must be the shared media group (gid 988).
  users.users.radarr.extraGroups = [ "media" ];

  # Bind loopback (Caddy proxies; the firewall keeps the port closed anyway,
  # as Jackett's --ListenPrivate does) and let Authentik sign users in.
  systemd.services.radarr.environment = {
    RADARR__SERVER__BINDADDRESS = "127.0.0.1";
    RADARR__AUTH__METHOD = "External";
  };

  # Configure Radarr once it is up: root folders, the qBittorrent download
  # client and the Jackett indexer. Idempotent, and it retries until Radarr
  # has written its API key (and, with Jackett, until Jackett's is there too).
  systemd.services.radarr-bootstrap = {
    description = "Configure Radarr (root folders, qBittorrent, Jackett)";
    wantedBy = [ "multi-user.target" ];
    after = [ "radarr.service" ] ++ lib.optional withJackett "jackett.service";
    wants = [ "radarr.service" ] ++ lib.optional withJackett "jackett.service";
    path = [ bootstrap ];
    environment = {
      APP = "radarr";
      SERVARR_URL = "http://127.0.0.1:${toString port}";
      CONFIG_XML = "/var/lib/radarr/.config/Radarr/config.xml";
      ROOT_FOLDERS = rootFolders;
      JACKETT_CONFIG = lib.optionalString withJackett "/var/lib/jackett/.config/Jackett/ServerConfig.json";
      JACKETT_URL = lib.optionalString withJackett "http://127.0.0.1:${toString config.lanbat.services.jackett.port}";
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
