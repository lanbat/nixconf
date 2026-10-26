# services/samba.nix
#
# Samba — SMB file server for Linux/Windows/macOS clients.
#
# Architecture
# ------------
# - Samba lives on the server only.
# - Shares are backed by NFS-mounted Pi storage (/srv/storage/<drive>).
# - Users authenticate via Authentik LDAP outpost.
#
# Shares
# ------
# The share layout is lanbat.services.samba.settings (options below): the
# workgroup and names, [homes] on the per-user storage, and shares.<name>,
# each on one Pi drive. The module defines a default layout matching the
# storage layout the other services use; a profile changes it from a module in
# the host's modules (docs/extensibility.md#service-settings):
#
#   lanbat.services.samba.settings.shares = {
#     private.enable = false;
#     scans = { drive = "a"; path = "scans"; readOnly = false; validUsers = [ "@media" ]; };
#   };
#
# smbd binds to the NFS mounts of exactly the drives the enabled shares use.
#
# Auth approach
# -------------
# Authentik exposes an LDAP outpost (port 3389) that speaks LDAPv3.
# Samba is configured with "passdb backend = ldapsam" pointing to the
# Authentik LDAP outpost.  User passwords are validated against Authentik.
#
# Caveats:
#   - Samba with ldapsam needs NT password hashes in LDAP.  Authentik
#     does NOT store NT hashes by default.
#   - Practical workaround: use "idmap" with winbind + Authentik LDAP
#     for user/group resolution, but keep password auth as local Samba
#     user DB that is manually synced.
#
# RECOMMENDED PRAGMATIC APPROACH:
#   Use local Samba users (smbpasswd) with the same usernames as Authentik.
#   Sync passwords manually when a user changes their Authentik password.
#   This is unsophisticated but reliable.  Full Kerberos/AD integration
#   is out of scope for a homelab.
#
# If you want better integration later, look at:
#   - Samba AD DC (heavyweight — not recommended here)
#   - sssd + Authentik LDAP (good for POSIX, not SMB passwords)
#
# NFS dependency: strong.
#   Shares are backed by the Pi's drives.  Stop Samba when Pi is gone.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  inherit (lib) mkOption types;

  cfg = config.lanbat.services.samba.settings;
  userStorage = config.lanbat.userStorage;

  # Where the server mounts a Pi storage drive (modules/wiring/nfs.nix).
  mountPoint = drive: "/srv/storage/${drive}";

  yesNo = b: if b then "yes" else "no";

  shareOptions = {
    options = {
      enable = mkOption {
        type = types.bool;
        default = true;
        description = "Whether to serve this share. Set false to drop one of the default shares.";
      };
      comment = mkOption {
        type = types.str;
        default = "";
        description = "Description clients show for the share.";
      };
      drive = mkOption {
        type = types.strMatching "[a-z0-9]+";
        example = "b";
        description = ''
          Pi storage drive the share lives on, by its key in the storage host's
          storage.drives. Samba's smbd binds to that drive's NFS mount.
        '';
      };
      path = mkOption {
        type = types.strMatching "[^/].*";
        example = "media";
        description = "Directory of the share, relative to the drive's mount (/srv/storage/<drive>).";
      };
      browseable = mkOption {
        type = types.bool;
        default = true;
        description = "Whether the share appears in share listings and network discovery.";
      };
      readOnly = mkOption {
        type = types.bool;
        default = true;
        description = "Whether clients may only read.";
      };
      guestOk = mkOption {
        type = types.bool;
        default = false;
        description = "Whether guests (no password) may connect.";
      };
      validUsers = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [ "@media" ];
        description = "Users, or @groups, allowed to connect (valid users). Empty allows every user.";
      };
      vetoFiles = mkOption {
        type = types.listOf types.str;
        default = [ ];
        example = [ "adult" ];
        description = "Names hidden from and refused to clients (veto files).";
      };
      createMask = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "0664";
        description = "Permission bits new files may have (create mask).";
      };
      directoryMask = mkOption {
        type = types.nullOr types.str;
        default = null;
        example = "0775";
        description = "Permission bits new directories may have (directory mask).";
      };
      forceGroup = mkOption {
        type = types.nullOr types.str;
        default = null;
        description = "Group every file created in the share belongs to (force group).";
      };
      createDirectory = mkOption {
        type = types.nullOr (
          types.submodule {
            options = {
              user = mkOption {
                type = types.str;
                default = "root";
                description = "Owner of the directory.";
              };
              group = mkOption {
                type = types.str;
                default = "root";
                description = "Group of the directory.";
              };
              mode = mkOption {
                type = types.strMatching "[0-7]{4}";
                default = "0755";
                description = "Mode of the directory.";
              };
            };
          }
        );
        default = null;
        description = ''
          Create the share's directory with this owner and mode (systemd-tmpfiles)
          when nothing else does. Null leaves the directory to whatever
          populates it.
        '';
      };
      extraConfig = mkOption {
        type = types.attrsOf (
          types.oneOf [
            types.bool
            types.int
            types.str
          ]
        );
        default = { };
        example = {
          "hide dot files" = "yes";
        };
        description = "Raw smb.conf keys for this share, merged last.";
      };
    };
  };

  sambaSettings = {
    options = {
      workgroup = mkOption {
        type = types.str;
        default = "WORKGROUP";
        description = "Windows workgroup the server joins.";
      };
      serverString = mkOption {
        type = types.str;
        default = "Homelab Server";
        description = "Server description clients show (server string).";
      };
      netbiosName = mkOption {
        type = types.str;
        default = "server";
        description = "NetBIOS name the server announces.";
      };
      homes = mkOption {
        type = types.bool;
        default = true;
        description = ''
          Serve each user's files/ directory under the per-user storage
          (lanbat.userStorage) as the [homes] share.
        '';
      };
      shares = mkOption {
        type = types.attrsOf (types.submodule shareOptions);
        default = { };
        description = ''
          The shares, by share name. The module defines a default layout for
          the repository's storage layout (media on both drives, a private
          share, a shared space); a profile adds shares, changes a field of a
          default one, or drops it with enable = false.
        '';
      };
      extraGlobal = mkOption {
        type = types.attrsOf (
          types.oneOf [
            types.bool
            types.int
            types.str
          ]
        );
        default = { };
        description = "Raw smb.conf keys for [global], merged last.";
      };
    };
  };

  # The default share layout: media split across both drives
  # (modules/storage/storage.nix), which qBittorrent saves into, and the private
  # media kept apart from it.
  defaultShares = {
    # ---- Shared media shares (read-only for all users) ----
    media = {
      comment = "Media: movies, TV, music videos";
      drive = "a";
      path = "media";
      validUsers = [ "@media" ];
    };

    media-b = {
      comment = "Media: music, documentaries, books, ROMs";
      drive = "b";
      path = "media";
      validUsers = [ "@media" ];
      # Adult video is only in the private share.
      vetoFiles = [ "adult" ];
    };

    # ---- Private media (restricted to "private" group) ----
    # Not browseable — does not appear in network discovery.
    # Only users explicitly added to the "private" group can access it.
    # Add users: usermod -aG private <username> && smbpasswd -a <username>
    private = {
      comment = "Private";
      drive = "b";
      path = "media/adult";
      browseable = false; # hidden from share listings
      validUsers = [ "@private" ];
      readOnly = false; # allow writes over SMB (the default is read-only)
      createMask = "0660"; # new files: user + private group rw, others none
      directoryMask = "0770"; # new dirs: user + private group rwx, others none
      forceGroup = "private"; # everything written stays in the private group
    };

    # ---- Shared space ----
    shared = {
      comment = "Shared";
      drive = "b";
      path = "shared";
      readOnly = false;
      validUsers = [ "@media" ];
      createMask = "0664";
      directoryMask = "0775";
      forceGroup = "media";
      createDirectory = {
        group = "media";
        mode = "0775";
      };
    };
  };

  shares = lib.filterAttrs (_: share: share.enable) cfg.shares;

  sharePath = share: "${mountPoint share.drive}/${share.path}";

  renderShare =
    share:
    {
      inherit (share) comment;
      path = sharePath share;
      browseable = yesNo share.browseable;
      "read only" = yesNo share.readOnly;
      "guest ok" = yesNo share.guestOk;
    }
    // lib.optionalAttrs (share.validUsers != [ ]) {
      "valid users" = lib.concatStringsSep " " share.validUsers;
    }
    // lib.optionalAttrs (share.vetoFiles != [ ]) {
      "veto files" = "/" + lib.concatMapStrings (name: "${name}/") share.vetoFiles;
    }
    // lib.optionalAttrs (share.createMask != null) { "create mask" = share.createMask; }
    // lib.optionalAttrs (share.directoryMask != null) { "directory mask" = share.directoryMask; }
    // lib.optionalAttrs (share.forceGroup != null) { "force group" = share.forceGroup; }
    // share.extraConfig;

  # The drives smbd needs: those of the shares, and the per-user storage's for
  # [homes].
  drives = lib.sort lib.lessThan (
    lib.unique (
      lib.mapAttrsToList (_: share: share.drive) shares ++ lib.optional cfg.homes userStorage.drive
    )
  );
in
{
  # The schema is merged into lanbat.services.samba.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.samba = sambaSettings;

  # Each default share is defined at normal priority with every field at
  # mkDefault, so a profile's definitions merge with it field by field.
  lanbat.services.samba.settings.shares =
    let
      defaults = v: if lib.isAttrs v then lib.mapAttrs (_: defaults) v else lib.mkDefault v;
    in
    defaults defaultShares;

  assertions = [
    {
      assertion = shares != { } || cfg.homes;
      message = "lanbat.services.samba: no share is enabled. Enable settings.homes or a share, or drop Samba from the host.";
    }
  ];

  lanbat.services.samba = {
    extraPorts = [
      139
      445
    ];
    tier = "workload";
    state = [ "samba" ];
    units = [
      "samba-smbd"
      "samba-nmbd"
      "samba-winbindd" # RequiresMountsFor=/var/lib/samba
    ];
    # winbindd requires private/ to exist before it starts.
    workloadDirs = lib.genAttrs [ "samba/private" "samba/usershares" ] (_: {
      user = "root";
      mode = "0755";
    });
    nfs = {
      inherit drives;
      units = [ "samba-smbd" ];
    };
  };

  services.samba = {
    enable = true;
    openFirewall = true; # opens 137,138,139,445

    settings = {
      global = {
        inherit (cfg) workgroup;
        "server string" = cfg.serverString;
        "netbios name" = cfg.netbiosName;
        security = "user";
        "map to guest" = "bad user";
        "log level" = "1";
        "max log size" = "10000";

        # Performance.
        "use sendfile" = "yes";
        "aio read size" = "16384";
        "aio write size" = "16384";
        "socket options" = "TCP_NODELAY IPTOS_THROUGHPUT SO_RCVBUF=131072 SO_SNDBUF=131072";

        # macOS compatibility.
        "vfs objects" = "catia fruit streams_xattr";
        "fruit:metadata" = "stream";
        "fruit:model" = "MacSamba";
        "fruit:posix_rename" = "yes";
        "fruit:veto_appledouble" = "no";
        "fruit:wipe_intentionally_left_blank_rfork" = "yes";
        "fruit:delete_empty_adfiles" = "yes";

        # LDAP backend — Authentik LDAP outpost.
        # Uncomment and configure if using LDAP for user resolution.
        # "passdb backend" = "ldapsam:ldap://127.0.0.1:3389";
        # "ldap admin dn"  = "cn=admin,dc=s,dc=10ctr,dc=vg,dc=cd";
        # "ldap ssl"       = "no";
      }
      // cfg.extraGlobal;
    }
    // lib.optionalAttrs cfg.homes {
      # ---- User home share ----
      homes = {
        comment = "Home Directories";
        browseable = "no";
        "read only" = "no";
        "create mask" = "0700";
        "directory mask" = "0700";
        "valid users" = "%S";
        path = "${userStorage.mountOnServer}/%S/files";
      };
    }
    // lib.mapAttrs (_: renderShare) shares;
  };

  # Samba avahi announcement for macOS autodiscovery. avahi-daemon stays always-on
  # (not workload-gated) so mDNS works before the workload layer is unlocked; Samba
  # shares only appear once samba-smbd is running.
  services.avahi = {
    enable = true;
    nssmdns4 = true;
    publish = {
      enable = true;
      userServices = true;
    };
  };

  systemd.services.samba-smbd = {
    serviceConfig = {
      Restart = "on-failure";
      RestartSec = "15s";
    };
  };

  # User home dirs are created by human-users.nix (files/ subdir per user).
  systemd.tmpfiles.rules = lib.mapAttrsToList (
    _: share:
    let
      d = share.createDirectory;
    in
    "d ${sharePath share} ${d.mode} ${d.user} ${d.group} -"
  ) (lib.filterAttrs (_: share: share.createDirectory != null) shares);

  # Note: add Samba users manually after deploying:
  #   smbpasswd -a <username>
  # or via a provisioning script.  Passwords must match what users know.
  # See docs/operations.md.
}
