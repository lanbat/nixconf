# Jackett — torrent indexer aggregator for qBittorrent searches.
#
# The management UI is only reachable through Caddy and Authentik, restricted
# to the Authentik admins: Jackett has one shared admin account and no roles,
# so everyone who gets in can change indexers and read their credentials.
# Jackett's own listener stays private on port 9117 (the module passes no
# --ListenPublic, and no firewall port is opened); qBittorrent reaches it over
# loopback.
#
# State: /var/lib/jackett holds the indexers' credentials, cookies and the API
# key, so it lives on the workload layer. The nixpkgs module's tmpfiles rule for
# its data directory only creates empty directories under the stub while the
# layer is locked, the same as Immich's; workloadDirs gives the mounted
# directories their owner.
#
# qBittorrent: its Jackett search plugin reads
# /var/lib/qbittorrent/qBittorrent/nova3/engines/jackett.json (/config/... in
# the container). jackett-qbittorrent-plugin writes it from the API key Jackett
# generated, before every qBittorrent start, so the key is never typed in or
# kept in the Nix store and a regenerated key is picked up on the next start.
# A Jackett restart leaves qBittorrent running; it learns a new key when it
# next starts.
#
# Freshness: indexer sites change domains and markup constantly, and the
# Jackett in nixpkgs (started with --NoUpdates, in a read-only store) lags
# upstream by weeks. The fixes are almost all in the indexer definitions, which
# Jackett loads from the app's own Definitions directory and also from
# $XDG_CONFIG_HOME/cardigann/definitions, and the second one wins for an
# indexer present in both. jackett-definitions (a timer, and once at every
# unlock before Jackett starts) copies upstream's definitions there and
# restarts Jackett only when they changed. The binary is pkgs/jackett, pinned
# to a recent release (update it with pkgs/jackett/update.sh), so the bundled
# definitions the sync overrides are recent too.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  port = config.lanbat.services.jackett.port;
  dataDir = config.services.jackett.dataDir;

  # The plugin unit and the ordering on qBittorrent exist only on a host that
  # runs qBittorrent; Jackett alone has no plugin to configure.
  withQbittorrent = config.virtualisation.oci-containers.containers ? qbittorrent;

  enginesDir = "/var/lib/qbittorrent/qBittorrent/nova3/engines";

  # Jackett reads custom definitions from $XDG_CONFIG_HOME/cardigann/definitions.
  # It sits in the jackett state directory, so it is workload data too, and is
  # replaced as a whole so an indexer upstream removed stops overriding the
  # bundled one.
  xdgDir = "/var/lib/jackett/xdg";
  definitionsDir = "${xdgDir}/cardigann/definitions";
  definitionsRepo = "Jackett/Jackett";
  definitionsPath = "src/Jackett.Common/Definitions";

  # Upstream publishes a release (and its definitions) about hourly.
  definitionsInterval = "*-*-* 00/6:15:00";
in

{
  lanbat.services.jackett = {
    subdomain = "jackett";
    port = 9117;
    auth = "forward-auth";
    access.groups = lib.mkDefault [ "authentik Admins" ];
    tier = "workload";
    state = [ "jackett" ];
    units = [
      "jackett"
      "jackett-definitions"
    ]
    ++ lib.optional withQbittorrent "jackett-qbittorrent-plugin";
    workloadDirs =
      lib.genAttrs
        [
          "jackett"
          "jackett/.config"
          "jackett/.config/Jackett"
        ]
        (_: {
          user = "jackett";
          mode = "0700";
        });
    dashboard = {
      group = "Downloads";
      name = "Jackett";
      description = "Torrent indexer aggregator";
    };
  };

  services.jackett = {
    enable = true;
    inherit port;
    # Newer than the locked nixpkgs': the definitions bundled in a build go
    # stale as the sites change, and the sync above only replaces them between
    # bumps. See pkgs/jackett.
    package = pkgs.callPackage ../pkgs/jackett { };
    openFirewall = false;
    dataDir = "/var/lib/jackett/.config/Jackett";
  };

  # The module's command line plus --ListenPrivate, so Jackett binds loopback
  # whatever AllowExternal says in its ServerConfig.json (on Linux Jackett
  # otherwise defaults to listening on every interface). The firewall already
  # keeps 9117 closed; this keeps the UI and API off the LAN even if it opens.
  systemd.services = {
    jackett = {
      environment.XDG_CONFIG_HOME = xdgDir;
      serviceConfig.ExecStart = lib.mkForce (
        "${config.services.jackett.package}/bin/Jackett --NoUpdates --ListenPrivate"
        + " --Port ${toString port} --DataFolder '${dataDir}'"
      );
    };

    jackett-definitions = {
      description = "Update Jackett's indexer definitions from upstream";
      # At an unlock this runs before Jackett starts, so the first start
      # already has them; Jackett is not required, so a failed download
      # (no network, GitHub down) leaves it starting with what it has.
      before = [ "jackett.service" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" ];
      path = [
        pkgs.coreutils
        pkgs.curl
        pkgs.gnutar
        pkgs.gzip
        pkgs.jq
      ];
      serviceConfig = {
        Type = "oneshot";
        ProtectSystem = "strict";
        ReadWritePaths = [ "/var/lib/jackett" ];
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
      script = ''
        set -euo pipefail

        # The newest commit that touched the definitions: unchanged since the
        # last run means nothing to download and Jackett is left alone.
        sha=$(curl -fsS --retry 3 --max-time 30 \
          -H 'Accept: application/vnd.github+json' \
          'https://api.github.com/repos/${definitionsRepo}/commits?path=${definitionsPath}&per_page=1&sha=master' \
          | jq -r '.[0].sha // empty')
        if ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
          echo "Could not read the latest definitions commit from GitHub" >&2
          exit 1
        fi

        stamp=${xdgDir}/cardigann/.commit
        if [ -d ${definitionsDir} ] && [ "$(cat "$stamp" 2>/dev/null)" = "$sha" ]; then
          echo "Definitions are already at $sha"
          exit 0
        fi

        install -d -m 0700 -o jackett -g jackett ${xdgDir} ${xdgDir}/cardigann
        new=$(mktemp -d ${xdgDir}/cardigann/.definitions.XXXXXX)
        trap 'rm -rf "$new"' EXIT

        curl -fsS --retry 3 --max-time 300 \
          "https://codeload.github.com/${definitionsRepo}/tar.gz/$sha" \
          | tar -xz -C "$new" --strip-components=4 --wildcards \
            '*/${definitionsPath}/*.yml'

        # A truncated or empty download must not replace working definitions.
        count=$(find "$new" -maxdepth 1 -name '*.yml' | wc -l)
        if [ "$count" -lt 100 ]; then
          echo "Only $count definitions in the download; keeping the current ones" >&2
          exit 1
        fi

        chown -R jackett:jackett "$new"
        chmod 0700 "$new"
        rm -rf ${definitionsDir}.old
        [ ! -e ${definitionsDir} ] || mv ${definitionsDir} ${definitionsDir}.old
        mv "$new" ${definitionsDir}
        rm -rf ${definitionsDir}.old
        echo "$sha" > "$stamp"
        echo "Installed $count definitions from $sha"

        # Definitions are read at startup. At an unlock Jackett is not up yet,
        # and --no-block keeps this from waiting on a start that waits on it.
        if systemctl is-active --quiet jackett.service; then
          systemctl restart --no-block jackett.service
        fi
      '';
    };

    jackett-qbittorrent-plugin = lib.mkIf withQbittorrent {
      description = "Point qBittorrent's Jackett search plugin at Jackett";
      requires = [ "jackett.service" ];
      after = [ "jackett.service" ];
      path = [
        pkgs.coreutils
        pkgs.jq
      ];
      serviceConfig = {
        # Not RemainAfterExit: the unit is inactive between runs, so starting
        # qBittorrent (which Requires it) runs it again, every time. It is
        # also not restarted when Jackett restarts, so neither is qBittorrent.
        Type = "oneshot";
        # Root writes into qBittorrent's state and reads Jackett's private
        # data directory; nothing else is writable.
        ProtectSystem = "strict";
        ReadWritePaths = [ "/var/lib/qbittorrent" ];
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
      script = ''
        config=${dataDir}/ServerConfig.json

        # Jackett writes ServerConfig.json, and with it the key, a moment
        # after it starts.
        key=
        for _ in $(seq 1 60); do
          if [ -s "$config" ]; then
            key=$(jq -r '.APIKey // empty' "$config" 2>/dev/null || true)
            [ -n "$key" ] && break
          fi
          sleep 1
        done
        if [ -z "$key" ] || [ "$key" = YOUR_API_KEY_HERE ]; then
          echo "Jackett has not generated an API key in $config" >&2
          exit 1
        fi

        # Each level belongs to qbt: qBittorrent creates them on its first
        # start, and a root-owned one would be unwritable to it.
        dir=/var/lib/qbittorrent
        for part in qBittorrent nova3 engines; do
          dir=$dir/$part
          install -d -m 0755 -o qbt -g qbt "$dir"
        done

        # Written beside the target and renamed over it, so the plugin never
        # reads half a file. The key goes through jq, not the shell.
        tmp=$(mktemp ${enginesDir}/.jackett.json.XXXXXX)
        trap 'rm -f "$tmp"' EXIT
        jq -n --arg key "$key" \
          '{api_key: $key, url: "http://127.0.0.1:${toString port}", tracker_first: false, thread_count: 20}' \
          > "$tmp"
        chown qbt:qbt "$tmp"
        chmod 0600 "$tmp"
        mv -f "$tmp" ${enginesDir}/jackett.json
      '';
    };

    # Never start with the plugin unconfigured: the plugin writes the
    # placeholder key and fails every search with "api key error".
    podman-qbittorrent = lib.mkIf withQbittorrent {
      requires = [ "jackett-qbittorrent-plugin.service" ];
      after = [ "jackett-qbittorrent-plugin.service" ];
    };
  };

  # Only while the workload layer is unlocked, like the service it starts.
  systemd.timers.jackett-definitions = {
    wantedBy = [ "workload-online.target" ];
    partOf = [ "workload-online.target" ];
    timerConfig = {
      OnCalendar = definitionsInterval;
      RandomizedDelaySec = "15min";
    };
  };
}
