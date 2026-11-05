#!/usr/bin/env bash
# deploy-host.sh — serialize deploy-rs deploys per host.
#
# Deploys mutate a live host, so two deploys to the *same* host must not overlap:
# they would both `switch` the generation and roll back against each other. This
# wrapper takes an exclusive flock keyed by the target, so a second deploy to the
# same host waits instead of racing, while different hosts stay parallel.
#
# Usage:
#   deploy-host.sh <target> [deploy-rs args...]
#
#   <target> is a deploy node name, e.g. homelab-server, homelab-pi-storage,
#   homelab-pi-voice (see "Host names" in docs/operations.md).
#
# Run from inside `nix develop` (the shell that provides `deploy`), or anywhere in
# the repo — it falls back to `nix develop --command deploy` when `deploy` is not
# already on PATH. Extra arguments after <target> are passed through to deploy-rs.

set -euo pipefail

target="${1:?usage: deploy-host.sh <target> [deploy-rs args...]}"
shift

# Deploy from the repo top level: deploy.nix and the profiles under deployments/
# are gitignored, so `path:.#<target>` must resolve against the checkout that has
# them (the main checkout), not a random subdirectory.
repo_top="$(git rev-parse --show-toplevel)"
cd "$repo_top"

# The lock lives in the shared git directory, which is the same path for the main
# checkout and every worktree (see ~/.config/opencode/skills/repo-ops), so agents
# in different worktrees still contend on the same lock.
git_dir="$(git rev-parse --git-common-dir)"
case "$git_dir" in
  /*) lock_dir="$git_dir" ;;
  *)  lock_dir="$repo_top/$git_dir" ;;
esac
lock_file="${lock_dir}/deploy-${target}.lock"

# Prefer the deploy-rs binary already on PATH (inside `nix develop`); otherwise
# launch the dev shell for it.
if command -v deploy >/dev/null 2>&1; then
  deploy_cmd=(deploy)
else
  deploy_cmd=(nix develop --command deploy)
fi

exec 9>"${lock_file}"
echo "deploy: waiting for lock on '${target}' (${lock_file})..." >&2
flock 9
echo "deploy: acquired lock on '${target}'" >&2

# Released automatically when the fd closes on exit; explicit for clarity.
trap 'flock -u 9' EXIT

exec "${deploy_cmd[@]}" --skip-checks "path:.#${target}" "$@"
