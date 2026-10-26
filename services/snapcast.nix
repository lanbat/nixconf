# services/snapcast.nix
#
# Snapcast server — synchronised multi-room audio distribution.
#
# Design
# ------
# - Snapserver broadcasts audio in sync to all connected snapclients (Pi).
# - Music Assistant (services/music-assistant.nix) is the controller/source:
#   it connects to snapserver's control API (port 1705) and registers dynamic
#   TCP input streams per playback via stream_add_stream — it does not write
#   to a named pipe.  See music_assistant/providers/snapcast/player.py in the
#   nixpkgs music-assistant package (_get_or_create_stream).
# - Post-deploy, enable MA's Snapcast provider with "Use existing Snapserver"
#   (127.0.0.1:1705).  Do not let MA launch its built-in snapserver.
# - The web UI / control API (port 1780) is proxied by Caddy at
#   audio.<domain> and protected by Authentik forward auth.
# - Streaming port (1704) and control port (1705) admit only declared clients
#   (see Clients below); everyone else on the LAN is refused.
#
# Stream source
# -------------
# The static "default" stream is an idle TCP listener MA switches away from
# when playing.  MA creates additional streams named "Music Assistant - …"
# on random ports (4953+) via the control API; ffmpeg pipes PCM into them.
#
# Ports
# -----
#   1704 TCP  — streaming   (snapclient connects here)
#   1705 TCP  — control API (snapclient, MA, web UI)
#   1780 TCP  — HTTP API + web UI (proxied by Caddy, localhost-only)
#
# Clients
# -------
# Snapcast has no authentication, so the firewall decides who may play and
# control: 1704 and 1705 admit exactly
#   - the hosts of services that consume snapcast (snapclient on a Pi), from the
#     endpoint table;
#   - the androidDevices boxes with Snapdroid (de.badaix.snapcast) in packages;
#   - settings.clients, for anything else (a speaker, a phone app), by IPv4
#     address or by MAC. A MAC admits a device over IPv4 and IPv6 alike, which
#     an Android box needs: it connects over IPv6 from rotating privacy
#     addresses, so no IPv6 address rule would hold.
# The others are admitted over IPv4. Nothing else reaches the ports, on either
# family.
# Music Assistant talks to the snapserver over loopback and needs no entry.
#
# Discovery
# ---------
# Snapclients (Snapdroid on Android TV, snapclient on the Pi) find the server
# via mDNS (_snapcast._tcp / _snapcast-ctrl._tcp).  That requires mdns_enabled
# and publish in snapserver.conf, plus Avahi D-Bus access (DynamicUser blocks
# it by default — see the avahi-snapserver group below).
#
# Snapserver must listen on IPv6 (::) as well as IPv4.  Avahi publishes the
# host's IPv6 addresses in mDNS and Android clients prefer them; with a v4-only
# bind they get "connection refused".  Binding :: accepts both (Linux dual-stack).
# Avahi IPv6 is also disabled so mDNS prefers the LAN IPv4.
# http.host is the LAN IP (cover-art URLs) — same address snapclient uses on the Pi.
#
# Always-on: yes.  No NFS dependency.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.services.snapcast.settings;
  thisHost = config.lanbat.hostKey;
  ports = [
    1704 # streaming
    1705 # control
  ];

  snapcastSettings = {
    options.clients = mkOption {
      type = types.attrsOf (
        types.submodule {
          options = {
            mac = mkOption {
              type = types.nullOr types.str;
              default = null;
              example = "2c:d8:ae:00:00:01";
              description = ''
                Hardware address of the client. Admits it over IPv4 and IPv6, and
                keeps working when its addresses change (Android rotates its
                IPv6 privacy addresses). The client must be on the server's LAN
                segment, not behind a router.
              '';
            };
            host = mkOption {
              type = types.nullOr types.str;
              default = null;
              example = "192.168.1.70";
              description = "IPv4 address of the client. Admits it over IPv4 only.";
            };
          };
        }
      );
      default = { };
      example = lib.literalExpression ''{ bedroom-tv.mac = "2c:d8:ae:00:00:01"; kitchen-speaker.host = "192.168.1.60"; }'';
      description = ''
        Devices allowed to play from and control the snapserver besides the ones
        found automatically (snapclient hosts and androidDevices boxes with
        Snapdroid), keyed by a name of your choice. Each sets mac, host or both.
      '';
    };
  };

  isIPv4 = s: builtins.match "([0-9]{1,3}\\.){3}[0-9]{1,3}" s != null;
  isMac = s: builtins.match "([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}" s != null;

  # snapclient and anything else that consumes snapcast, on other hosts.
  consumerAddresses = lib.concatLists (
    lib.mapAttrsToList (
      _: entry:
      if lib.elem "snapcast" (entry.consumes or [ ]) then
        map (host: entry.addresses.${host}) (lib.filter (host: host != thisHost) entry.hosts)
      else
        [ ]
    ) config.lanbat.endpoints
  );
  snapdroidAddresses = lib.mapAttrsToList (_: device: device.host) (
    lib.filterAttrs (
      _: device: (device.enable or true) && lib.elem "de.badaix.snapcast" (device.packages or [ ])
    ) config.lanbat.deployment.androidDevices
  );
  present =
    field: lib.filter (v: v != null) (lib.mapAttrsToList (_: client: client.${field}) cfg.clients);

  addresses = lib.unique (consumerAddresses ++ snapdroidAddresses ++ present "host");
  macs = lib.unique (map lib.toLower (present "mac"));

  # Rule bodies without the -I/-D verb, so start and stop can't drift apart.
  byPort = match: map (port: "INPUT -p tcp --dport ${toString port} ${match} -j ACCEPT") ports;
  v4Specs = lib.concatMap (address: byPort "-s ${address}") addresses;
  macSpecs = lib.concatMap (mac: byPort "-m mac --mac-source ${mac}") macs;
  # IPv4 rules for addresses; MAC rules for both families.
  startCommands =
    map (spec: "iptables -I ${spec}") (v4Specs ++ macSpecs)
    ++ map (spec: "ip6tables -I ${spec}") macSpecs;
  stopCommands =
    map (spec: "iptables -D ${spec} 2>/dev/null || true") (v4Specs ++ macSpecs)
    ++ map (spec: "ip6tables -D ${spec} 2>/dev/null || true") macSpecs;

  # Two clients may not claim the same address or MAC.
  shared =
    field:
    lib.mapAttrsToList
      (value: names: {
        assertion = lib.length names == 1;
        message = "snapcast clients ${lib.concatStringsSep ", " names} share the ${field} ${value}.";
      })
      (
        lib.groupBy' (acc: name: acc ++ [ name ]) [ ] (name: lib.toLower cfg.clients.${name}.${field}) (
          lib.filter (name: cfg.clients.${name}.${field} != null) (lib.attrNames cfg.clients)
        )
      );
in
{
  # The schema is merged into lanbat.services.snapcast.settings; checks.nix
  # rejects any key it does not declare.
  lanbat.settingsSchema.snapcast = snapcastSettings;

  assertions =
    lib.concatLists (
      lib.mapAttrsToList (name: client: [
        {
          assertion = client.mac != null || client.host != null;
          message = "snapcast client ${name} sets neither mac nor host, so the firewall can't admit it.";
        }
        {
          assertion = client.host == null || isIPv4 client.host;
          message = "snapcast client ${name}: host ${toString client.host} is not an IPv4 address; the firewall needs an address.";
        }
        {
          assertion = client.mac == null || isMac client.mac;
          message = "snapcast client ${name}: mac ${toString client.mac} is not a MAC address (aa:bb:cc:dd:ee:ff).";
        }
      ]) cfg.clients
    )
    ++ shared "host"
    ++ shared "mac";

  lanbat.services.snapcast = {
    subdomain = "audio";
    port = 1780;
    extraPorts = ports;
    auth = "forward-auth";
    # snapweb immediately opens two WebSocket connections after the page loads:
    #   /jsonrpc  — JSON-RPC control (volume, groups, streams)
    #   /stream   — browser-side audio playback
    # Caddy's forward_auth cannot complete a WebSocket upgrade: the auth
    # sub-request succeeds but the redirect in its response is forwarded as the
    # WebSocket handshake reply, so the browser sees a failed upgrade and
    # snapweb shows "Connection lost, trying to reconnect".  The page itself
    # and all static assets still go through Authentik; only these two paths
    # bypass it.  (Same fix as Music Assistant's /ws — see music-assistant.nix.)
    caddy.authBypassPaths = [
      "/jsonrpc"
      "/stream"
    ];
    # Keep the backend connection alive for the duration of the WebSocket
    # session (same pattern as Home Assistant and Music Assistant).
    caddy.proxyOptions = ''
      transport http {
        keepalive 24h
      }
    '';
    dashboard = {
      group = "Utilities";
      name = "Snapcast";
      description = "Multi-room audio";
    };
  };

  services.snapserver = {
    enable = true;

    # Snapcast 0.35.0 segfaults in StreamManager::removeStream when Music
    # Assistant deletes a stream after a transfer (a coredump on every
    # follow-me move). 0.34.0 predates the re-added Stream.RemoveStream RPC
    # path, so MA's delete is a harmless no-op and idle streams expire via
    # idle_threshold. Pin it until a fixed upstream release exists.
    package = pkgs.snapcast.overrideAttrs (old: {
      version = "0.34.0";
      src = pkgs.fetchFromGitHub {
        owner = "badaix";
        repo = "snapcast";
        rev = "v0.34.0";
        hash = "sha256-BPsAGFLWUfONuyQ1pzsJzGV/Jlxv+4TkVT1KG7j8H0s=";
      };
    });

    settings = {
      server = {
        mdns_enabled = true;
      };

      tcp-streaming = {
        enabled = true;
        port = 1704;
        bind_to_address = "::";
        publish = true;
      };

      tcp-control = {
        enabled = true;
        port = 1705;
        bind_to_address = "::";
        publish = true;
      };

      http = {
        enabled = true;
        port = 1780;
        bind_to_address = "127.0.0.1";
        host = config.lanbat.deployment.serverIp;
      };

      # Idle "default" stream — MA sets groups back here when playback stops.
      # Port 4952 is below MA's dynamic range (4953–5153).
      stream.source = "tcp://127.0.0.1:4952?name=default&mode=server&sampleformat=48000:16:2&codec=flac&idle_threshold=60000";
    };
  };

  # Admitted before the nixos-fw chain, which refuses everyone else (see Clients).
  networking.firewall.extraCommands = lib.concatLines startCommands;
  networking.firewall.extraStopCommands = lib.concatLines stopCommands;

  # IPv4-only mDNS — see Discovery above.  Merged into avahi-daemon.conf from
  # services/samba.nix.
  services.avahi.ipv6 = false;

  # Let snapserver register _snapcast._tcp with avahi-daemon (already enabled
  # for Samba in services/samba.nix).  Upstream fix: nixpkgs#548066.
  users.groups.avahi-snapserver = { };

  systemd.services.snapserver = {
    after = [ "avahi-daemon.service" ];
    serviceConfig.SupplementaryGroups = [ "avahi-snapserver" ];
  };

  services.dbus.packages = [
    (pkgs.writeTextDir "share/dbus-1/system.d/snapserver-avahi.conf" ''
      <!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
      <busconfig>
        <policy group="avahi-snapserver">
          <allow send_destination="org.freedesktop.Avahi"/>
          <allow receive_sender="org.freedesktop.Avahi"/>
        </policy>
      </busconfig>
    '')
  ];
}
