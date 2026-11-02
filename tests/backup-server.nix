# tests/backup-server.nix
#
# pkgs/scripts/backup-server.sh copies each service's state with copy_state,
# which skips a directory the host does not have instead of failing the whole
# backup: a profile need not run every service, and a service's state can move
# (Frigate's config is now rendered from the profile). Runs the script's own
# copy_state in bash against a scratch tree.
{ pkgs }:

pkgs.runCommand "backup-server-check" { nativeBuildInputs = [ pkgs.rsync ]; } ''
  set -euo pipefail
  # The function as the script defines it, from "copy_state() {" to its closing "}".
  sed -n '/^copy_state() {$/,/^}$/p' ${../pkgs/scripts/backup-server.sh} > copy_state.sh
  grep -q 'copy_state()' copy_state.sh || { echo "FAIL: backup-server.sh defines no copy_state"; exit 1; }
  source ./copy_state.sh

  DEST=$PWD/dest
  mkdir -p src/present "$DEST"
  echo data > src/present/file

  copy_state src/present present > out.txt
  [ "$(cat "$DEST/present/file")" = data ] || { echo "FAIL: a present directory is not copied"; exit 1; }

  copy_state src/absent absent > out.txt
  grep -q 'Skipping src/absent' out.txt || { echo "FAIL: an absent directory is not reported"; exit 1; }
  [ ! -e "$DEST/absent" ] || { echo "FAIL: an absent directory left a copy"; exit 1; }

  # Extra rsync options pass through.
  mkdir -p src/opts/index-x
  echo keep > src/opts/keep
  copy_state src/opts opts --exclude 'index-*' > out.txt
  [ -e "$DEST/opts/keep" ] && [ ! -e "$DEST/opts/index-x" ] || { echo "FAIL: rsync options are not passed through"; exit 1; }

  # Critical state that is missing is reported and fails the run at the end,
  # after everything else has been copied.
  sed -n '/^copy_required_state() {$/,/^}$/p' ${../pkgs/scripts/backup-server.sh} > required.sh
  grep -q 'copy_required_state()' required.sh || { echo "FAIL: backup-server.sh defines no copy_required_state"; exit 1; }
  missing_required=()
  source ./required.sh
  copy_required_state src/present required-present > out.txt
  [ -e "$DEST/required-present/file" ] || { echo "FAIL: present required state is not copied"; exit 1; }
  [ ''${#missing_required[@]} -eq 0 ] || { echo "FAIL: present required state is reported missing"; exit 1; }
  copy_required_state src/gone gone > out.txt
  grep -q 'ERROR: src/gone' out.txt || { echo "FAIL: missing required state is not reported"; exit 1; }
  [ ''${#missing_required[@]} -eq 1 ] || { echo "FAIL: missing required state is not recorded"; exit 1; }
  for d in /var/lib/caddy /var/lib/tang; do
    grep -qE "^\s*copy_required_state $d " ${../pkgs/scripts/backup-server.sh} \
      || { echo "FAIL: $d is not required"; exit 1; }
  done
  grep -q 'missing_required\[@\]} -gt 0' ${../pkgs/scripts/backup-server.sh} \
    || { echo "FAIL: the script does not fail when required state is missing"; exit 1; }
  grep -q 'ln -sfn "\$DEST" "\$BACKUP_DIR/LATEST"' ${../pkgs/scripts/backup-server.sh} \
    || { echo "FAIL: a complete backup does not update LATEST"; exit 1; }

  # Jackett's indexer credentials and API key are workload state: copied while
  # the layer is unlocked, not attempted (and not reported missing) while locked.
  sed -n '/^if workload_online; then$/,/^else$/p' ${../pkgs/scripts/backup-server.sh} \
    | grep -qE '^\s*copy_state /var/lib/jackett +jackett\s*$' \
    || { echo "FAIL: /var/lib/jackett is not backed up with the workload state"; exit 1; }

  # Frigate's config is rendered from the profile and its event database is
  # deliberately left out of the backup (clips and recordings are media), so
  # no Frigate path is archived by this script.
  if grep -qE '^\s*copy_state /var/lib/frigate' ${../pkgs/scripts/backup-server.sh}; then
    echo "FAIL: Frigate's state is no longer archived"; exit 1
  fi

  # A full pg_dumpall (roles plus every database) replaces the per-database
  # skip list; nothing is excluded by name any more.
  grep -q 'pg_dumpall $conn --clean --if-exists' ${../pkgs/scripts/backup-server.sh} \
    || { echo "FAIL: pg_dumpall does not dump everything with --clean --if-exists"; exit 1; }
  if grep -q 'SKIP_DATABASES' ${../pkgs/scripts/backup-server.sh}; then
    echo "FAIL: skipDatabases is gone"; exit 1
  fi

  # No state copy bypasses copy_state.
  if grep -nE '^\s*rsync ' ${../pkgs/scripts/backup-server.sh} | grep -v 'copy_state' | grep -vE '^[0-9]+:\s*rsync -a --delete "\$@"'; then
    echo "FAIL: a state copy calls rsync directly instead of copy_state"; exit 1
  fi
  touch $out
''
