# services/slskd.nix
#
# slskd — Soulseek client with a web UI, for the household to search and
# download music from the Soulseek network.
#
# Why the NixOS module?
#   `services.slskd` runs the upstream server as its own user and serves the
#   web client itself; there is no reason to containerize.
#
# Auth: Authentik, through Caddy's forward auth, is the only login. slskd's own
# login is switched off, and the port is not opened in the firewall, so
# nothing on the LAN reaches the UI without Authentik. Everyone Authentik lets
# in shares the one Soulseek account and queue. caddy.auditLog records who
# searched and downloaded what in /var/log/caddy/access-<host>.log.
#
# Storage:
#   /var/lib/slskd/                      — configuration, share index and
#                                          incomplete downloads (workload layer)
#   /srv/storage/<drive>/<downloadsPath> — finished downloads, on the Pi. By
#                                          default the aMule folder of the music
#                                          library, which Syncthing syncs, and
#                                          which slskd also shares back to the
#                                          network (read-only).
#
# Settings (lanbat.services.slskd.settings): drive and downloadsPath place the
# downloads folder; sharePaths adds folders to share besides it.
#
# Peers: Soulseek works without inbound connections, with fewer sources. Port
# 50300 is open on the server's firewall; forward it on the router for the
# rest.
#
# Secrets
# -------
# slskd-env.age — KEY=value lines:
#   SLSKD_SLSK_USERNAME=<Soulseek account name>
#   SLSKD_SLSK_PASSWORD=<its password>
#   (an account is made by logging in with any unused name and password)
{ config, lib, ... }:

let
  port = 5030;
  listenPort = 50300;
  cfg = config.lanbat.services.slskd.settings;

  # Where the server mounts a Pi storage drive (modules/wiring/nfs.nix).
  downloads = "/srv/storage/${cfg.drive}/${cfg.downloadsPath}";

  slskdSettings = {
    options = {
      drive = lib.mkOption {
        type = lib.types.str;
        default = "b";
        description = ''
          Pi storage drive holding the downloads folder, by its key in the
          storage host's storage.drives. slskd binds to that drive's NFS mount.
        '';
      };
      downloadsPath = lib.mkOption {
        type = lib.types.str;
        default = "media/music/aMule";
        description = ''
          Folder finished downloads go to, relative to the drive's mount
          (/srv/storage/<drive>). It must exist on the Pi, owned by group
          media and group-writable. slskd shares it back to the network.
        '';
      };
      sharePaths = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        example = [ "media/music/albums" ];
        description = ''
          Further folders to share on the Soulseek network (read-only),
          relative to the drive's mount.
        '';
      };
    };
  };
in
{
  # The schema is merged into lanbat.services.slskd.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.slskd = slskdSettings;

  lanbat.services.slskd = {
    subdomain = "soulseek";
    inherit port;
    extraPorts = [ listenPort ];
    auth = "forward-auth";
    caddy.auditLog = true;
    tier = "workload";
    state = [ "slskd" ];
    units = [ "slskd" ];
    workloadDirs."slskd".user = "slskd";
    nfs.drives = [ cfg.drive ];
    account = {
      uid = 969;
      # The music library on the Pi belongs to qbt, group media (modules/storage/storage.nix).
      extraGroups = [ "media" ];
    };
    secrets.slskd-env = { };
    dashboard = {
      group = "Downloads";
      name = "Soulseek";
      description = "Soulseek client (slskd)";
    };
  };

  services.slskd = {
    enable = true;
    environmentFile = config.lanbat.secrets.slskd-env.path;
    settings = {
      web = {
        inherit port;
        # Authentik is the login.
        authentication.disabled = true;
      };
      soulseek.listen_port = listenPort;
      directories.downloads = downloads;
      shares.directories = [ downloads ] ++ map (p: "/srv/storage/${cfg.drive}/${p}") cfg.sharePaths;
    };
  };

  networking.firewall.allowedTCPPorts = [ listenPort ];

  systemd.services.slskd.serviceConfig = {
    # The module's private user namespace hides the media group, which the
    # downloads folder on the Pi needs.
    PrivateUsers = lib.mkForce false;
    # Group-writable, for Syncthing and Jellyfin.
    UMask = "0002";
    RestartSec = "15s";
  };
}
