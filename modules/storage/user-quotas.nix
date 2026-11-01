# modules/storage/user-quotas.nix
#
# Apply per-user XFS project quotas on the user storage drive
# (lanbat.userStorage.drive, B by default) after it is unlocked.
# Each human user's entire directory tree (files, sync, photos) shares one
# project quota — the single enforcement point for unified storage limits.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  cfg = config.lanbat;
  userStorage = cfg.userStorage;
  drive = userStorage.drive;
  hasDrive = (cfg.hosts.${cfg.hostKey}.storage.drives or { }) ? ${drive};
  humanUsers = cfg.humanUsers;
  sortedUsers = lib.sort (a: b: a < b) (lib.attrNames humanUsers);

  userProjectIds = lib.listToAttrs (
    lib.imap1 (i: name: {
      name = name;
      value = userStorage.projectIdBase + i - 1;
    }) sortedUsers
  );

  effectiveQuota =
    user:
    let
      override = humanUsers.${user}.quota;
    in
    if override != null then override else userStorage.defaultQuota;

  applyScript = pkgs.writeShellScript "apply-user-quotas" (
    ''
      set -euo pipefail

      STORAGE_B=${userStorage.mountOnPi}
      BASE="$STORAGE_B"
      PROJ_FILE=/etc/projects
      PROJID_FILE=/etc/projid

      if ! mountpoint -q "$STORAGE_B"; then
        echo "storage-${drive} not mounted; skipping user quota setup."
        exit 0
      fi

      fstype=$(findmnt -n -o FSTYPE "$STORAGE_B")
      if [[ "$fstype" != "xfs" ]]; then
        echo "storage-${drive} is $fstype, expected xfs; skipping."
        exit 0
      fi

      # Preserve static project entries from quota-setup.sh, append user entries.
      touch "$PROJ_FILE" "$PROJID_FILE"
      grep -v '# lanbat-user-quota' "$PROJ_FILE" > "$PROJ_FILE.tmp" || true
      grep -v '# lanbat-user-quota' "$PROJID_FILE" > "$PROJID_FILE.tmp" || true
      mv "$PROJ_FILE.tmp" "$PROJ_FILE"
      mv "$PROJID_FILE.tmp" "$PROJID_FILE"
    ''
    + lib.concatStrings (
      lib.mapAttrsToList (
        user: _:
        let
          id = userProjectIds.${user};
          path = "${userStorage.mountOnPi}/${user}";
          quota = effectiveQuota user;
          pname = "user-${user}";
        in
        ''
          echo "${toString id}:${path}" >> "$PROJ_FILE"  # lanbat-user-quota
          echo "${pname}:${toString id}" >> "$PROJID_FILE"  # lanbat-user-quota

          install -d -m 0755 "${path}"
          install -d -m 0700 -o ${toString humanUsers.${user}.uid} -g ${
            toString humanUsers.${user}.uid
          } "${path}/files"
          install -d -m 0770 -o ${toString userStorage.serviceOwners.sync.uid} -g ${
            toString humanUsers.${user}.uid
          } "${path}/sync"
          install -d -m 0770 -o ${toString userStorage.serviceOwners.photos.uid} -g ${
            toString humanUsers.${user}.uid
          } "${path}/photos"

          echo "Applying project quota for ${user} (ID ${toString id}) at ${path}..."
          xfs_quota -x -c "project -s -p ${path} ${toString id}" "$STORAGE_B"
          xfs_quota -x -c "limit -p bsoft=${quota.soft} bhard=${quota.hard} ${pname}" "$STORAGE_B"
        ''
      ) humanUsers
    )
    + ''
      echo "User storage quotas applied."
    ''
  );
in
{
  # Only on the host that has the drive: elsewhere storage-<drive>-init does not
  # exist and this would require a unit nothing defines.
  systemd.services.user-storage-quotas = lib.mkIf hasDrive {
    description = "Apply per-user XFS project quotas on storage-${drive}";
    requires = [ "storage-${drive}-init.service" ];
    after = [ "storage-${drive}-init.service" ];
    wantedBy = [ "storage-${drive}-init.service" ];

    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = applyScript;
    };
  };

  environment.systemPackages = [
    (pkgs.writeShellScriptBin "quota-report-users" (''
      set -euo pipefail
      mount=${userStorage.mountOnPi}
      if mountpoint -q "$mount"; then
        echo "===== Per-user quotas: $mount ====="
        xfs_quota -x -c "report -p -b -h" "$mount" | grep -E '^user-' || true
      else
        echo "$mount is not mounted."
      fi
    ''))
  ];
}
