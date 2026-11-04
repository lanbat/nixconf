# services/qbittorrent.nix
#
# qBittorrent — torrent client with web UI.
#
# Web UI: VueTorrent (pkgs.vuetorrent), which works on phones as well as
#   desktop browsers, unlike qBittorrent's own. qBittorrent serves it as its
#   alternative web UI from the package, mounted read-only into the container,
#   so it updates with nixpkgs and the API (Homepage's widget) is unchanged.
#
# Storage split
# -------------
# Server-local:
#   /var/lib/qbittorrent/   — qBittorrent config, fastresume files, session state
#
# Pi-backed via NFS, media split across both drives by folder:
#   /srv/storage/a/media/  → /media/a  (movies, TV, music videos)
#   /srv/storage/b/media/  → /media/b  (music, documentaries, ROMs, books, ...)
# Each category saves into its folder. The Pi creates the folders
# (modules/storage/storage.nix), owned by qbt, group media.
#
# Settings: qBittorrent's configuration is declared, not edited in the web UI.
#   lanbat.services.qbittorrent.settings holds
#     categories   — category name -> save path (a "/" nests a subcategory)
#     preferences  — qBittorrent.conf as section -> key -> value
#   and both are written to categories.json and qBittorrent.conf before every
#   start. qBittorrent reads them only at start, so a change made in the web UI
#   lasts until the next restart, then reverts. This module sets the keys the
#   deployment depends on (the loopback-only, Authentik-only web UI and
#   VueTorrent) and a few defaults; a profile sets the rest (see
#   docs/extensibility.md#service-settings).
#
# NFS dependency: strong.
#   If Pi storage disappears while a torrent is active, qBittorrent will
#   write I/O errors.  We stop it immediately and restart when NFS returns.
#
# Auth: Authentik, through Caddy's forward auth, is the only login, for the web
#   UI and the API alike. After it, everyone shares the one instance:
#   qBittorrent skips its own login for requests from the server's address,
#   which is where Caddy's requests come from. The port listens only on the
#   server's loopback, so nothing on the LAN reaches qBittorrent without
#   Authentik.
#
# Audit: caddy.auditLog records who did what, with the Authentik user on each
#   line of /var/log/caddy/access-torrent.<domain>.log: page loads and every
#   action (adding, pausing, deleting a torrent, changing a setting), which the
#   API takes as POST requests. See docs/operations.md.
#
# Homepage: its widget reads the torrent list from qBittorrent on the server's
#   loopback (Homepage uses host networking), which qBittorrent answers without
#   a login, so the widget needs no credentials and Caddy no exception.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  puid = config.lanbat.services.qbittorrent.account.uid;
  pgid = config.users.groups.media.gid;

  # Podman ID map flags sending container ID `id` to namespace ID 0 (the user
  # running Podman) and every other container ID 0..65535 to a sub-ID of its own.
  idMap = flag: id: [
    "${flag}=0:1:${toString id}"
    "${flag}=${toString id}:0:1"
    "${flag}=${toString (id + 1)}:${toString (id + 1)}:${toString (65535 - id)}"
  ];

  cfg = config.lanbat.services.qbittorrent.settings;

  # What a profile may declare. Nothing is a UI-only setting: whatever is not
  # here or in `preferences` goes back to qBittorrent's own default at the
  # next restart.
  qbittorrentSettings = {
    options = {
      categories = lib.mkOption {
        type = lib.types.attrsOf (lib.types.strMatching "(/.*)?");
        default = { };
        example = {
          "Music" = "";
          "Music/Albums" = "/media/b/music/albums";
        };
        description = ''
          Download categories by name, with the folder each saves into as the
          container sees it (/media/a, /media/b). A "/" in the name nests a
          subcategory under its parent, which must be declared too. An empty
          path is a grouping category: its torrents save to the default folder.
          None sets a share limit: all follow the global ones.
        '';
      };
      preferences = lib.mkOption {
        type = lib.types.attrsOf (
          lib.types.attrsOf (
            lib.types.oneOf [
              lib.types.bool
              lib.types.int
              lib.types.str
            ]
          )
        );
        default = { };
        example = {
          BitTorrent."Session\\MaxActiveDownloads" = 8;
          WebUI.Port = 8090;
        };
        description = ''
          qBittorrent.conf: section name, then key (a backslash is part of the
          key, as qBittorrent writes it), then value. Booleans render as
          true/false. The keys the web UI depends on are fixed by this module;
          setting one of them differently is a conflict.
        '';
      };
    };
  };

  # categories.json with the same fields qBittorrent writes, so a restart that
  # changes nothing leaves the categories exactly as they were.
  categoriesFile = pkgs.writeText "qbittorrent-categories.json" (
    builtins.toJSON (
      lib.mapAttrs (_: path: {
        download_path = null;
        inactive_seeding_time_limit = -2;
        ratio_limit = -2;
        save_path = path;
        seeding_time_limit = -2;
        share_limit_action = "Default";
      }) cfg.categories
    )
  );

  renderValue =
    v:
    if builtins.isBool v then
      lib.boolToString v
    else if builtins.isInt v then
      toString v
    else
      v;

  # qBittorrent.conf, less the [Meta] section, which is qBittorrent's own
  # bookkeeping (the settings migration it has run) and is kept as it is.
  confFile = pkgs.writeText "qBittorrent.conf" (
    lib.concatStringsSep "\n" (
      lib.mapAttrsToList (
        section: keys:
        lib.concatStringsSep "\n" (
          [ "[${section}]" ] ++ lib.mapAttrsToList (key: value: "${key}=${renderValue value}") keys
        )
        + "\n"
      ) cfg.preferences
    )
  );
in

{
  # The schema is merged into lanbat.services.qbittorrent.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.qbittorrent = qbittorrentSettings;

  lanbat.services.qbittorrent.settings.preferences = {
    # Fixed: these make the Authentik forward auth the only login (see Auth
    # above). A profile that sets one differently gets a conflict, not a
    # quietly open web UI.
    # The [WebUI] section is qBittorrent 5.x's own: it no longer reads the
    # legacy WebUI\* keys under [Preferences] once the settings migration has
    # run (recorded in [Meta], which is kept as it is).
    WebUI = {
      Address = "127.0.0.1";
      AlternativeUIEnabled = true;
      RootFolder = "/vuetorrent";
      AuthSubnetWhitelistEnabled = true;
      # Host networking: Caddy connects via loopback (127.0.0.1). Bridge/pasta
      # used to rewrite the source to the server IP — keep both.
      AuthSubnetWhitelist = "127.0.0.1/32, ::1/128, ${config.lanbat.deployment.serverIp}/32, ::ffff:${config.lanbat.deployment.serverIp}/128";
      Port = config.lanbat.services.qbittorrent.port;
    };

    # Defaults a profile may change.
    AutoRun = {
      enabled = lib.mkDefault false;
      program = lib.mkDefault "";
    };
    LegalNotice.Accepted = lib.mkDefault true;
    BitTorrent = {
      "Session\\SubcategoriesEnabled" = lib.mkDefault true;
      "Session\\DefaultSavePath" = lib.mkDefault "/media/b/misc/";
      "Session\\TempPath" = lib.mkDefault "/media/b/incomplete/";
    };
    # No UPnP/NAT-PMP: it would open a port on the router unasked.
    Network.PortForwardingEnabled = lib.mkDefault false;
  };

  assertions = [
    {
      assertion = !(cfg.preferences ? Meta);
      message = ''
        lanbat.services.qbittorrent.settings.preferences.Meta is qBittorrent's
        own record of the settings migrations it has run, and is kept as it is.
      '';
    }
    {
      assertion = lib.all (
        name:
        lib.hasInfix "/" name
        -> cfg.categories ? ${lib.concatStringsSep "/" (lib.init (lib.splitString "/" name))}
      ) (lib.attrNames cfg.categories);
      message = "lanbat.services.qbittorrent.settings.categories: a subcategory needs its parent declared too (Music/Albums needs Music).";
    }
  ];

  lanbat.services.qbittorrent = {
    subdomain = "torrent";
    port = 8090;
    auth = "forward-auth";
    caddy.auditLog = true;
    tier = "workload";
    state = [ "qbittorrent" ];
    units = [ "podman-qbittorrent" ];
    workloadDirs."qbittorrent".user = "qbt";
    nfs.drives = [
      "a"
      "b"
    ];
    account = {
      name = "qbt";
      uid = 994;
      container = true;
      extraGroups = [ "media" ];
    };
    dashboard = {
      group = "Downloads";
      name = "qBittorrent";
      description = "Torrent client";
      widget = {
        type = "qbittorrent";
        # Straight to qBittorrent, which skips its login for loopback; through
        # Caddy the widget would need an Authentik session.
        url = "http://127.0.0.1:${toString config.lanbat.services.qbittorrent.port}";
        enableLeechProgress = true;
      };
    };
  };

  # Run qBittorrent as an OCI container to simplify volume mounts.
  virtualisation.oci-containers.containers."qbittorrent" = {
    image = "lscr.io/linuxserver/qbittorrent:5.3.0@sha256:05c1454f9a32967affc1f34f67f8cc0cc57dd805dca45489fe797095a6286556";

    environment = {
      # Mapped to the host qbt account below, so NFS media dirs (qbt:media,
      # mode 2775) are writable.
      PUID = toString puid;
      PGID = toString pgid;
      TZ = config.lanbat.deployment.timezone;
      WEBUI_PORT = "8090";
    };

    volumes = [
      "/var/lib/qbittorrent:/config"
      "/srv/storage/a/media:/media/a"
      "/srv/storage/b/media:/media/b"
      "${pkgs.vuetorrent}/share/vuetorrent:/vuetorrent:ro"
    ];

    # Host networking avoids pasta's IPv4 fragment drops, which break BitTorrent
    # peer connections in rootless Podman. The web UI stays on loopback via
    # WebUI.Address in the settings above.
    #
    # The image runs qBittorrent as PUID:PGID. Rootless Podman would put those
    # on sub-IDs of qbt's range, which own nothing on the media drives, so they
    # are mapped to namespace ID 0 instead: the host qbt account and its group.
    # The media folders are qbt:media with the setgid bit, so new files still
    # join group media. Every other ID keeps a sub-ID of its own.
    extraOptions = [ "--network=host" ] ++ idMap "--uidmap" puid ++ idMap "--gidmap" pgid;

    podman.user = "qbt";
    user = "0";
    autoStart = false; # started by workload-online.target
  };

  systemd.services."podman-qbittorrent".serviceConfig = {
    # First, as root (+), hand the state to qbt, which PUID maps to: state
    # written under an earlier mapping belongs to a sub-ID qBittorrent could no
    # longer write. Then write the settings (see Settings above) before every
    # start, so what the web UI changed does not outlive a restart. Both files
    # are written in place, so they keep qbt as their owner.
    ExecStartPre = lib.mkBefore [
      "+${pkgs.coreutils}/bin/chown -R qbt:qbt /var/lib/qbittorrent"
      "+${pkgs.writeShellScript "qbittorrent-settings" ''
        dir=/var/lib/qbittorrent/qBittorrent
        ${pkgs.coreutils}/bin/install -d -o qbt -g qbt -m 755 "$dir"
        ${pkgs.coreutils}/bin/install -o qbt -g qbt -m 644 ${categoriesFile} "$dir/categories.json"

        # The first start has no qBittorrent.conf, and without its [Meta]
        # section qBittorrent would run every settings migration over ours.
        # It writes one on that start, and the declared settings apply from
        # the next.
        conf="$dir/qBittorrent.conf"
        [ -f "$conf" ] || exit 0
        tmp=$(${pkgs.coreutils}/bin/mktemp)
        trap '${pkgs.coreutils}/bin/rm -f "$tmp"' EXIT
        ${pkgs.coreutils}/bin/cat ${confFile} > "$tmp"
        ${pkgs.gawk}/bin/awk '
          $0 == "[Meta]" { keep = 1; print ""; print; next }
          /^\[/ { keep = 0 }
          keep && NF
        ' "$conf" >> "$tmp"
        ${pkgs.coreutils}/bin/cat "$tmp" > "$conf"
      ''}"
    ];
    Restart = lib.mkForce "on-failure";
    RestartSec = "15s";
  };
}
