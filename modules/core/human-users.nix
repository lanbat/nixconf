# modules/core/human-users.nix
#
# Human user accounts and unified storage quotas.
#
# Each user gets a directory tree on Pi storage (lanbat.userStorage.drive, B by
# default):
#
#   /mnt/storage-b/users/<username>/
#     files/    — Samba home share
#     cloud/    — Nextcloud external storage
#     sync/     — Syncthing folder (personal, not shared/)
#     photos/   — Immich external library
#
# An XFS project quota on the user's root directory enforces a single limit
# across all services, regardless of which UID wrote the files.
#
# Authentik remains the identity provider; this module only declares the
# matching Linux accounts (same UID on server and Pi) and quota limits.
{
  config,
  lib,
  ...
}:

let
  # Aliased so the serviceOwners submodule below can still reach the host's
  # own configuration after its own `config` shadows the name.
  topConfig = config;

  inherit (lib) mkOption types;

  quotaType = types.submodule {
    options = {
      soft = mkOption {
        type = types.strMatching "[0-9]+[kmgtKMGT]?";
        example = "100G";
        description = "XFS soft block limit (e.g. 100G, 500M, 2T).";
      };
      hard = mkOption {
        type = types.strMatching "[0-9]+[kmgtKMGT]?";
        example = "110G";
        description = "XFS hard block limit. Must be ≥ soft.";
      };
    };
  };

  humanUserType = types.submodule {
    options = {
      uid = mkOption {
        type = types.int;
        example = 1002;
        description = "POSIX UID. Must be unique and ≥ 1000.";
      };

      groups = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Supplementary groups (media, private, wheel, …).";
      };

      quota = mkOption {
        type = types.nullOr quotaType;
        default = null;
        description = ''
          Per-user storage quota override. When null, lanbat.userStorage.defaultQuota
          applies to the entire directory tree under users/<username>/.
        '';
      };
    };
  };

  cfg = config.lanbat;
  humanUsers = cfg.humanUsers;
  userStorage = cfg.userStorage;

  sortedUsers = lib.sort (a: b: a < b) (lib.attrNames humanUsers);

  effectiveQuota =
    user:
    let
      override = humanUsers.${user}.quota;
    in
    if override != null then override else userStorage.defaultQuota;

  userBase = userStorage.mountOnServer;
  userDir = user: "${userBase}/${user}";
  userSubdir = user: sub: "${userDir user}/${sub}";

  subdirMode = sub: if sub == "files" then "0700" else "0770";
in
{
  options.lanbat = {
    userStorage = {
      defaultQuota = mkOption {
        type = quotaType;
        default = {
          soft = "100G";
          hard = "110G";
        };
        description = "Default unified storage quota for every human user.";
      };

      subdirs = mkOption {
        type = types.listOf types.str;
        default = [
          "files"
          "cloud"
          "sync"
          "photos"
        ];
        description = "Subdirectories created under each user's storage root.";
      };

      projectIdBase = mkOption {
        type = types.int;
        default = 300;
        description = ''
          First XFS project ID assigned to human users. IDs are assigned in
          alphabetical username order (admin=300, alice=301, …). Keep below 900
          to avoid clashing with service project IDs in quota-setup.sh.
        '';
      };

      drive = mkOption {
        type = types.strMatching "[a-z0-9]+";
        default = "b";
        description = ''
          Pi storage drive, by its key in the storage host's storage.drives,
          that holds the per-user directories and their quotas.
        '';
      };

      mountOnPi = mkOption {
        type = types.str;
        default = "/mnt/storage-${config.lanbat.userStorage.drive}/users";
        defaultText = lib.literalExpression ''"/mnt/storage-''${drive}/users"'';
        description = "Per-user storage root on the Pi (XFS enforcement point).";
      };

      mountOnServer = mkOption {
        type = types.str;
        default = "/srv/storage/${config.lanbat.userStorage.drive}/users";
        defaultText = lib.literalExpression ''"/srv/storage/''${drive}/users"'';
        description = "NFS-mounted per-user storage root on the server.";
      };

      # Which service owns each kind of per-user subdirectory. The Pi sets the
      # ownership but does not run these services, so it takes their UIDs from
      # lanbat.endpoints rather than repeating them: they were pinned here as
      # literals and had no way to follow a change on the server.
      serviceOwners = mkOption {
        type = types.attrsOf (
          types.submodule (
            { config, ... }:
            {
              options.name = mkOption {
                type = types.str;
                description = "Service that owns this kind of subdirectory.";
              };
              options.uid = mkOption {
                type = types.int;
                defaultText = lib.literalExpression "the service's account uid";
                default =
                  let
                    # A host that runs the service knows its account directly; a
                    # host that only mounts its data reads the profile-wide
                    # table. Local first, so a host or a test assembled without
                    # the table still resolves.
                    local = topConfig.lanbat.services.${config.name} or null;
                    remote = topConfig.lanbat.endpoints.${config.name} or null;
                    account =
                      if local != null && local.account != null then
                        local.account
                      else if remote != null then
                        remote.account
                      else
                        null;
                  in
                  if account != null then
                    account.uid
                  else
                    throw (
                      "lanbat.userStorage.serviceOwners: '${config.name}' has no"
                      + " lanbat service account to take a uid from, so set its uid"
                      + " explicitly."
                    );
                description = ''
                  UID that owns the directory. Defaults to the account the named
                  service runs as, whether it runs on this host or elsewhere in
                  the profile. A service whose user the upstream NixOS module
                  creates has no lanbat account to take, so give it explicitly,
                  preferably as that module's entry in `ids.uids`.
                '';
              };
            }
          )
        );
        default = {
          cloud.name = "nextcloud";
          photos.name = "immich";
          # Syncthing's user comes from the upstream NixOS module rather than a
          # lanbat account, so take the uid that module assigns it. ids.uids is
          # part of every NixOS system, so the Pi reads the same number without
          # running Syncthing.
          sync = {
            name = "syncthing";
            uid = topConfig.ids.uids.syncthing;
          };
        };
      };
    };

    humanUsers = mkOption {
      type = types.attrsOf humanUserType;
      default = { };
      description = ''
        Human user accounts provisioned on both hosts with matching UIDs.
        Storage quotas are enforced on the Pi via XFS project quotas.
      '';
    };
  };

  config = {
    lanbat.humanUsers = lib.mkDefault {
      admin = {
        uid = 1001;
        groups = [
          "wheel"
          "media"
          "private"
        ];
      };
    };

    users.users = lib.mapAttrs (
      name: user:
      {
        isNormalUser = true;
        inherit (user) uid;
        group = name;
        extraGroups = user.groups;
      }
      // lib.optionalAttrs (name == "admin") {
        openssh.authorizedKeys.keys = [ cfg.deployment.adminSshKey ] ++ cfg.deployment.extraAdminSshKeys;
      }
    ) humanUsers;

    users.groups = lib.listToAttrs (
      map (name: {
        name = name;
        value = { };
      }) (lib.attrNames humanUsers)
    );

    systemd.tmpfiles.rules = [
      "d ${userBase} 0755 root root -"
    ]
    ++ lib.concatLists (
      lib.mapAttrsToList (
        user: _:
        map (
          sub:
          let
            owner = if sub == "files" then user else userStorage.serviceOwners.${sub}.name;
          in
          "d ${userSubdir user sub} ${subdirMode sub} ${owner} ${user} -"
        ) userStorage.subdirs
      ) humanUsers
    );

    assertions = lib.mapAttrsToList (user: u: {
      assertion = u.uid >= 1000;
      message = "lanbat: humanUsers.${user}.uid must be ≥ 1000 (got ${toString u.uid})";
    }) humanUsers;
  };
}
