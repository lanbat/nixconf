# modules/storage/jellyfin-watch.nix
#
# Jellyfin media watcher — Pi (storage) side.
#
# Jellyfin's real-time library monitoring uses inotify, which does not fire on
# the NFS *client* (the server host that runs Jellyfin). The media actually
# lives on this storage Pi, so this service watches /mnt/storage-*/media here
# and tells Jellyfin to rescan when files appear, change or disappear, over
# https://media.<domain> with an API key (secrets/jellyfin-watch-env.age).
#
# Enable it per host with lanbat.storagePi.jellyfinWatch.enable = true. Off by
# default, so a storage Pi that serves no Jellyfin needs no secret.
#
# The API key is minted once in Jellyfin (Dashboard → API Keys) and stored in
# the agenix secret, following the same flow as the Kodi add-on secrets.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  inherit (lib) mkEnableOption mkOption types;

  cfg = config.lanbat.storagePi.jellyfinWatch;
  domain = config.lanbat.deployment.domain;
  host = config.lanbat.hosts.${config.lanbat.hostKey};

  # The media tree each drive exports (see modules/storage/storage.nix). Drive
  # "adult" and the "incomplete" download staging dir are never Jellyfin
  # libraries, so the watcher skips them.
  driveNames = lib.attrNames (host.storage.drives or { });
  watchDirs = map (d: "/mnt/storage-${d}/media") driveNames;
  unlockUnits = map (d: "storage-${d}-unlock.service") driveNames;

  caCert = config.lanbat.deployment.secrets.caCertificate;

  path = lib.makeBinPath [
    pkgs.inotify-tools # inotifywait
    pkgs.curl # the refresh POST
    pkgs.coreutils # sleep, echo, stdbuf
    pkgs.gnugrep # the exclude filter
  ];

  # Watches every media directory recursively and, after the tree has been
  # quiet for JELLYFIN_WATCH_DEBOUNCE seconds, POSTs one /Library/Refresh.
  #
  # inotifywait's monitor output is line-buffered explicitly (stdbuf) so the
  # read loop sees events promptly over the pipe; grep drops the adult and
  # incomplete subtrees and keeps line buffering.
  watcher = pkgs.writeShellScript "jellyfin-watch" ''
    set -euo pipefail

    key_file="$JELLYFIN_WATCH_KEY_FILE"
    url="$JELLYFIN_WATCH_URL"
    debounce="$JELLYFIN_WATCH_DEBOUNCE"

    if [ -z "$key_file" ] || [ -z "$url" ]; then
      echo "jellyfin-watch: JELLYFIN_WATCH_KEY_FILE and JELLYFIN_WATCH_URL must be set" >&2
      exit 1
    fi
    case "$debounce" in
      *[!0-9]*) debounce=60 ;;
    esac

    # shellcheck disable=SC1090
    . "$key_file"
    if [ -z "''${JELLYFIN_API_KEY:-}" ]; then
      echo "jellyfin-watch: no JELLYFIN_API_KEY in $key_file" >&2
      exit 1
    fi

    fire() {
      if curl -fsS -X POST "$url/Library/Refresh" \
        -H "Authorization: MediaBrowser Token=\"$JELLYFIN_API_KEY\"" \
        -o /dev/null 2>/dev/null; then
        echo "jellyfin-watch: triggered a Jellyfin library refresh"
      else
        echo "jellyfin-watch: refresh request failed (may already be scanning)" >&2
      fi
    }

    while true; do
      # Block until the first change after a fire (or an inotifywait restart).
      if read -r _; then
        pending=1
      else
        # inotifywait exits when a watched directory disappears (a drive
        # re-locked) or on a watch error; retry rather than die.
        echo "jellyfin-watch: inotifywait ended; retrying in 10s" >&2
        sleep 10
        continue
      fi

      # Debounce: keep consuming events until the tree is quiet for $debounce
      # seconds, then fire one refresh.
      while [ "$pending" -eq 1 ]; do
        if read -r -t "$debounce" _; then
          :
        else
          fire
          pending=0
        fi
      done
    done < <(stdbuf -oL inotifywait -m -r -q \
      -e create -e close_write -e moved_to -e delete -e moved_from \
      --format '%w%f' \
      "$@" | grep --line-buffered -Ev '/(adult|incomplete)/')
  '';
in
{
  options.lanbat.storagePi.jellyfinWatch = {
    enable = mkEnableOption ''
      watch the Pi's media directories and trigger a Jellyfin rescan when files
      change, so a new file shows up without waiting for the next scheduled scan
    '';
    debounceSeconds = mkOption {
      type = types.ints.positive;
      default = 60;
      description = ''
        How many seconds of quiet after the last change before one Jellyfin
        refresh is fired. A burst of changes (a torrent finishing) collapses
        into a single scan.
      '';
    };
    url = mkOption {
      type = types.str;
      default = "https://media.${domain}";
      defaultText = lib.literalExpression ''"https://media.''${config.lanbat.deployment.domain}"'';
      description = "Jellyfin base URL; the watcher POSTs to <url>/Library/Refresh.";
    };
  };

  config = lib.mkIf cfg.enable {
    # The Jellyfin API key, owned by root like the Kodi add-on secrets.
    lanbat.hostSecrets.jellyfin-watch-env = {
      owner = "root";
    };

    # Trust the internal CA Caddy serves https://media.<domain> from.
    security.pki.certificateFiles = [ caCert ];

    # Recursively watching a large media tree needs far more watches than the
    # kernel default of 8192.
    boot.kernel.sysctl = {
      "fs.inotify.max_user_watches" = 524288;
      "fs.inotify.max_user_instances" = 1024;
    };

    systemd.services.jellyfin-watch = {
      description = "Watch media directories and trigger Jellyfin rescans";
      wantedBy = [ "multi-user.target" ];
      # Start once the drives are mounted (a locked drive must not stop the
      # watcher: it retries inside the script), and only when the network is up.
      after = unlockUnits ++ [ "network-online.target" ];
      wants = [ "network-online.target" ];

      serviceConfig = {
        Type = "simple";
        Restart = "always";
        RestartSec = "15s";
        ExecStart = "${watcher} ${lib.concatStringsSep " " watchDirs}";
        Environment = [
          "JELLYFIN_WATCH_KEY_FILE=${config.lanbat.secrets.jellyfin-watch-env.path}"
          "JELLYFIN_WATCH_URL=${cfg.url}"
          "JELLYFIN_WATCH_DEBOUNCE=${toString cfg.debounceSeconds}"
          "SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt"
          "PATH=${path}"
        ];
        ProtectSystem = "full";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
        RestrictAddressFamilies = [
          "AF_INET"
          "AF_INET6"
          "AF_UNIX"
        ];
      };
    };
  };
}
