# modules/storage/storage.nix
#
# Raspberry Pi NVMe storage — directory initialisation and NFS dependency wiring.
#
# ─────────────────────────────────────────────────────────────────────────────
# PHYSICAL LAYOUT
# ─────────────────────────────────────────────────────────────────────────────
#
#  Drive A (/dev/disk/by-id/<piStorageDriveA>):
#    LUKS2 → XFS (pquota) → /mnt/storage-a
#    (LUKS may be on the whole disk or on a partition; clevis-unlock.nix
#     resolves whichever it is.)
#    Directories:
#      /mnt/storage-a/media/         — movies, TV, music videos (qBittorrent, Jellyfin)
#      /mnt/storage-a/photos/        — Immich originals
#      /mnt/storage-a/surveillance/  — Frigate recordings
#
#  Drive B (/dev/disk/by-id/<piStorageDriveB>):
#    LUKS2 → XFS (pquota) → /mnt/storage-b
#    Directories:
#      /mnt/storage-b/media/         — music, documentaries, ROMs, books and the rest
#      /mnt/storage-b/media/adult/   — private group only (Samba + NFS gated)
#      /mnt/storage-b/users/         — per-user SMB home dirs
#      /mnt/storage-b/shared/        — shared SMB space
#      /mnt/storage-b/backups/       — backup target (restic repositories)
#
# ─────────────────────────────────────────────────────────────────────────────
# UNLOCK MODEL
# ─────────────────────────────────────────────────────────────────────────────
#
#  LUKS unlock and mounting are handled by modules/storage/clevis-unlock.nix.
#  That module creates one storage-<drive>-unlock.service per key of
#  hosts.<key>.storage.drives, for example:
#    storage-a-unlock.service  — unlocks + mounts /mnt/storage-a
#    storage-b-unlock.service  — unlocks + mounts /mnt/storage-b
#
#  These services run after network-online.target, retry every 5 minutes if
#  Tang is unreachable, and do NOT block boot on failure.
#
#  This file only handles what comes AFTER successful unlock:
#    - creating the required directory tree (once per new filesystem)
#    - wiring the NFS server dependency
#
# ─────────────────────────────────────────────────────────────────────────────
# XFS PROJECT QUOTAS
# ─────────────────────────────────────────────────────────────────────────────
#
#  The drives are formatted with XFS + pquota (project quotas). Setup requires
#  a one-time manual step after first formatting:
#    mount -o pquota /dev/mapper/storage-a /mnt/storage-a
#    # ... then run quota setup script
#  See docs/storage-layout.md for the full quota plan.
#
{
  config,
  pkgs,
  lib,
  ...
}:

let
  privateGid = config.users.groups.private.gid;
  mediaGid = config.users.groups.media.gid;

  # The accounts that write here do so from another host over NFS, so this Pi
  # cannot look them up locally. lanbat.endpoints carries every service in the
  # profile with the account it runs as, which is where these UIDs now come
  # from; they used to be repeated here as literals and drifted silently when a
  # service's uid changed.
  runs = name: (config.lanbat.endpoints.${name} or { account = null; }).account != null;
  uidOf = name: toString config.lanbat.endpoints.${name}.account.uid;

  # A directory is only created when the service that owns it is part of the
  # profile, so a deployment without Immich or Frigate does not get a tree of
  # empty folders owned by accounts that exist nowhere.
  forService = name: text: lib.optionalString (runs name) text;

  drives = config.lanbat.hosts.${config.lanbat.hostKey}.storage.drives;

  # The directory tree each drive gets, keyed by drive name. The services put
  # their data on a named drive (Immich on a, Samba on b and so on, see the
  # layout above), so the tree belongs to the name rather than to the position.
  # A drive with no entry here is still unlocked, initialised and exported; it
  # just starts empty.
  trees = {
    a = lib.concatStringsSep "\n" [
      (forService "qbittorrent" ''
        # Media on drive A, written by qBittorrent, readable by group media.
        for dir in media media/movies media/tv media/music-videos; do
          install -d -m 2775 -o ${uidOf "qbittorrent"} -g ${toString mediaGid} "$base/$dir"
        done
      '')
      (forService "immich" ''
        install -d -m 0755 -o ${uidOf "immich"} -g nogroup "$base/photos"
      '')
      (forService "frigate" ''
        install -d -m 0755 -o ${uidOf "frigate"} -g nogroup "$base/surveillance"
        install -d -m 0755 -o ${uidOf "frigate"} -g nogroup "$base/surveillance/clips"
        install -d -m 0755 -o ${uidOf "frigate"} -g nogroup "$base/surveillance/exports"
      '')
    ];

    b = lib.concatStringsSep "\n" [
      (forService "qbittorrent" ''
        # General media on drive B — group media (Jellyfin, qBittorrent, Samba).
        for dir in media media/music media/documentaries media/documentary-series media/roms \
          media/audiobooks media/books media/gym media/games media/misc media/incomplete; do
          install -d -m 2775 -o ${uidOf "qbittorrent"} -g ${toString mediaGid} "$base/$dir"
        done
        # Private group only — not in Jellyfin, hidden from the Samba media shares.
        install -d -m 2770 -o ${uidOf "qbittorrent"} -g ${toString privateGid} "$base/media/adult"
        chgrp ${toString privateGid} "$base/media/adult" 2>/dev/null || true
        chmod 2770 "$base/media/adult" 2>/dev/null || true
        # Its contents too: something moved in from elsewhere in media keeps
        # group media, readable to every media user behind this folder's lock.
        chgrp -R ${toString privateGid} "$base/media/adult" 2>/dev/null || true
        chmod -R o-rwx "$base/media/adult" 2>/dev/null || true
      '')
      ''
        install -d -m 0755 -o root -g nogroup "$base/users"
        install -d -m 0775 -o root -g ${toString mediaGid} "$base/shared"
        install -d -m 0700 -o root -g root "$base/backups"
        # backup-server.sh (on the server) only writes into an existing
        # destination, so a missing directory reads as the NFS mount being down.
        install -d -m 0700 -o root -g root "$base/backups/server"''
    ];
  };
in
{
  # ── Drive initialisation, one unit per drive ───────────────────────────────
  # Runs after storage-<drive> is unlocked and mounted, creates the top-level
  # directory tree with correct permissions, then refreshes the NFS exports so
  # the drive is served (modules/storage/nfs-exports.nix exports it once mounted).
  systemd.services = lib.listToAttrs (
    map (
      drive:
      lib.nameValuePair "storage-${drive}-init" {
        description = "Initialise storage-${drive} directory tree after unlock";
        requires = [ "storage-${drive}-unlock.service" ];
        after = [ "storage-${drive}-unlock.service" ];
        before = [ "nfs-server.service" ];
        wantedBy = [ "nfs-server.service" ];

        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          ExecStartPost = "-${pkgs.nfs-utils}/bin/exportfs -ra";
          ExecStart = pkgs.writeShellScript "init-storage-${drive}" ''
            set -e
            base=/mnt/storage-${drive}
            ${trees.${drive} or ""}
            echo "storage-${drive} directory tree ready."
          '';
        };
      }
    ) (lib.attrNames drives)
  );

  environment.systemPackages = with pkgs; [
    xfsprogs
    cryptsetup
    clevis
  ];
}
