#!/usr/bin/env bash
# quota-setup.sh
#
# One-time XFS project quota setup for the Pi storage drives.
#
# Run this on the Pi AFTER the drives are formatted and mounted.
# Must be run as root.
#
# XFS project quotas assign a "project ID" to each directory tree.
# Once assigned, xfs_quota can set and report per-project limits.
#
# USAGE:
#   sudo bash quota-setup.sh
#
# AFTER running: use `xfs_quota` to set limits (see below).

set -euo pipefail

STORAGE_A=/mnt/storage-a
STORAGE_B=/mnt/storage-b

# Project ID map — each top-level quota tree gets a unique ID.
# IDs are arbitrary but must be consistent.
declare -A PROJECTS=(
  # Drive A
  [media]=100
  [downloads]=101
  [photos]=102
  [surveillance]=103
  # Drive B
  [shared]=202
  [backups]=203
)

declare -A PATHS=(
  [media]=$STORAGE_A/media
  [downloads]=$STORAGE_A/downloads
  [photos]=$STORAGE_A/photos
  [surveillance]=$STORAGE_A/surveillance
  [shared]=$STORAGE_B/shared
  [backups]=$STORAGE_B/backups
)

# ---------------------------------------------------------------------------
# Verify mounts are XFS with pquota.
# ---------------------------------------------------------------------------
for mount in $STORAGE_A $STORAGE_B; do
  if ! mountpoint -q "$mount"; then
    echo "ERROR: $mount is not mounted. Mount it first." >&2
    exit 1
  fi
  fstype=$(findmnt -n -o FSTYPE "$mount")
  if [[ "$fstype" != "xfs" ]]; then
    echo "ERROR: $mount is $fstype, expected xfs." >&2
    exit 1
  fi
  opts=$(findmnt -n -o OPTIONS "$mount")
  if ! echo "$opts" | grep -q "pquota\|prjquota"; then
    echo "WARNING: $mount is not mounted with pquota. Remount with pquota first." >&2
    echo "  Edit /etc/fstab or NixOS fileSystems to add 'pquota' option." >&2
    exit 1
  fi
done

# ---------------------------------------------------------------------------
# Write /etc/projects and /etc/projid.
# ---------------------------------------------------------------------------
PROJ_FILE=/etc/projects
PROJID_FILE=/etc/projid

echo "# XFS project quotas — managed by quota-setup.sh" > "$PROJ_FILE"
echo "# XFS project IDs — managed by quota-setup.sh"    > "$PROJID_FILE"

for name in "${!PROJECTS[@]}"; do
  id=${PROJECTS[$name]}
  path=${PATHS[$name]}
  echo "${id}:${path}" >> "$PROJ_FILE"
  echo "${name}:${id}" >> "$PROJID_FILE"
done

echo "Wrote $PROJ_FILE and $PROJID_FILE."

# ---------------------------------------------------------------------------
# Initialize project quotas on the filesystems.
# ---------------------------------------------------------------------------
for name in "${!PROJECTS[@]}"; do
  id=${PROJECTS[$name]}
  path=${PATHS[$name]}
  fs=$(df --output=target "$path" | tail -1)
  echo "Initializing project $name (ID $id) at $path on $fs..."
  xfs_quota -x -c "project -s -p $path $id" "$fs" || true
done

echo ""
echo "Project quotas initialized."
echo ""
echo "Set limits with xfs_quota, e.g.:"
echo "  xfs_quota -x -c 'limit -p bsoft=500g bhard=550g surveillance' $STORAGE_A"
echo "  xfs_quota -x -c 'limit -p bsoft=1t bhard=1100g downloads' $STORAGE_A"
echo "  xfs_quota -x -c 'report -pb' $STORAGE_A"
echo ""
echo "See docs/storage-layout.md for recommended limits."
