# services/techo5-bridge.nix
#
# Amazon Echo Shows running TECHO5 (lanbat.deployment.techo5Devices) as bodies
# of the assistant router, one per room: who is in front of them, a frame for
# the cameras to name and for "remember me", a greeting, the room's people's
# own screens, the sign-in code, the screen off in an empty room, and the
# household's phones in what their Bluetooth hears (pkgs/techo5-bridge,
# docs/techo5.md).
#
# The bridge is an ESPHome API client of each device, as Home Assistant is,
# with the device's encryption key (techo5-<name>-api-key), and a "screen"
# body on the router's socket on the loopback. It keeps the device's own
# settings as the profile has them, and hands the device Home Assistant's
# address and the voice satellites' token (ha-voice-token), which drawn
# dashboards, pictures and calendars need.
#
# Home Assistant adds each device (home-assistant-post-setup, with the same
# key), puts it and its Music Assistant player in its room, and generates its
# dashboard (pkgs/home-assistant-dashboards).
#
# The sign-in code is a QR code the device fetches over HTTP (port 8775, the
# devices' addresses only): the who page's link for the room, which proves
# nothing more than a robot's NFC tag does.
#
# The device checks Home Assistant's certificate: the bridge puts the profile's
# root CA (secrets.caCertificate) among the ones it trusts, over SSH as root
# after each of its boots (its root filesystem is read-only, TECHO5 has no
# setting for it: pkgs/techo5-bridge ca.py), with techo5-ssh-key. Caddy's
# intermediates and leaf certificates renew under that root by themselves; a
# new root reaches the device with the next deploy.
#
# Always-on: it is in the voice path, as the robots' bridges are. On disk only
# the devices' SSH host keys (/var/lib/techo5-bridge/known_hosts).
{
  config,
  lib,
  pkgs,
  ...
}:

let
  name = "techo5-bridge";
  bridge = pkgs.callPackage ../pkgs/techo5-bridge { };
  hostLib = import ../lib/host.nix { inherit lib; };

  devices = config.lanbat.deployment.techo5Devices;
  people = config.lanbat.deployment.people or { };
  domain = config.lanbat.deployment.domain;
  routerHere =
    hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null)
    && config.lanbat.hasService "assistant-router";
  hasHa = config.lanbat.hasService "home-assistant";
  enabled = devices != { } && routerHere;
  codePort = 8775;
  caCert = config.lanbat.deployment.secrets.caCertificate;
  trusting = lib.filterAttrs (_: d: d.installCa) devices;
  installsCa = enabled && trusting != { };
  serverIp = config.lanbat.hosts.${config.lanbat.hostKey}.networking.ip;
  bodyPort = config.lanbat.services.assistant-router.endpoint.port;

  keySecret = device: "techo5-${device}-api-key";
  # The voice satellites' token: this host's satellite's when it has one, the
  # assistant router's otherwise (services/assistant-router.nix, which runs
  # wherever the bridge does). The name is voice-satellite's on any host with a
  # satellite, whatever its backend (modules/core/voice-satellite.nix), and a
  # secret's name is one service's alone, enabled or not.
  satelliteDeclares = config.lanbat.voiceSatellite.enable;

  # Each device's own settings: lanbat's, then the profile's.
  settingsFor =
    device:
    {
      # What the bridge reads: who is in front, and the phones nearby.
      presence_detection = true;
      person_detection = true;
      bluetooth_proxy = true;
      # The bridge decides when the screen goes off (screenOffAfter), from the
      # whole room rather than the camera alone.
      screen_off_when_nobody_is_near = 0;
      # Music Assistant's player.
      sendspin = true;
      # Root's SSH, with the bridge's key and the admin's, only to put the CA on it.
      ssh = device.installCa;
      remote_api = false;
    }
    # Jellyfin and other DLNA apps play to it: the renderer, and video through it.
    // lib.optionalAttrs device.dlna {
      dlna = true;
      video = true;
      dlna_video = true;
    }
    // lib.optionalAttrs hasHa {
      # The results view is a web page inside a streamed dashboard, so the
      # screen streams: dashcast runs the browser (services/dashcast.nix).
      screen_dashboard = "Streamed";
      screen_dashboard_kiosk = true;
      insecure_tls = device.insecureTls;
    }
    // device.settings;

  profileJson = key: p: {
    view = if p.view == null then "" else p.view;
    inherit (p) glance calendars;
    immich_person = p.immichPerson;
    todo =
      if key == null then
        ""
      else if p.todo != null then
        p.todo
      else
        "todo.${key}_todo";
    reminders =
      if key == null then
        ""
      else if p.reminders != null then
        p.reminders
      else
        "todo.${key}_reminders";
  };

  configFile = pkgs.writeText "techo5-bridge.json" (
    builtins.toJSON {
      router_url = "ws://127.0.0.1:${toString bodyPort}/v1/body";
      people = lib.mapAttrs (_: p: p.name) people;
      # room_presence's beacons: UUID (32 lower-case hex) -> person.
      beacons = lib.mapAttrs' (
        key: p: lib.nameValuePair (lib.toLower (lib.replaceStrings [ "-" ] [ "" ] p.bleBeacon)) key
      ) (lib.filterAttrs (_: p: p.bleBeacon != null) people);
      code_base = "http://${serverIp}:${toString codePort}";
      code_port = codePort;
      code_host = serverIp;
      ca_file = lib.optionalString installsCa "${caCert}";
      ssh_key_file = lib.optionalString installsCa "/run/${name}/ssh-key";
      ssh_extra_keys = [ config.lanbat.deployment.adminSshKey ];
      known_hosts = "/var/lib/${name}/known_hosts";
      ssh = "${pkgs.openssh}/bin/ssh";
      devices = lib.mapAttrsToList (key: d: {
        name = key;
        inherit (d) address room;
        key_file = "/run/${name}/${keySecret key}";
        # Generated by pkgs/home-assistant-dashboards.
        dashboard = lib.optionalString hasHa (hostLib.screenDashboard d.room);
        settings = settingsFor d;
        screen_off_after = d.screenOffAfter * 60;
        ha_url = lib.optionalString hasHa "https://ha.${domain}";
        profiles = lib.mapAttrs (person: profileJson person) d.profiles;
        household = profileJson null d.household;
        # While the router shows a search's results, the screen streams this
        # dashcast page (services/dashcast.nix) rather than a dashboard view:
        # the page is the list, full screen, with nothing of Home Assistant's.
        results_page = "web:Results";
        install_ca = d.installCa;
        # The reminders it has rung, kept across restarts (StateDirectory).
        rung_file = "/var/lib/${name}/rung-${key}.json";
      }) devices;
    }
  );

  # Port 8775 for the devices alone (the snapcast.nix pattern).
  specs = map (d: "INPUT -p tcp --dport ${toString codePort} -s ${d.address} -j ACCEPT") (
    lib.attrValues devices
  );
in
{
  lanbat.services.${name} = {
    tier = "always-on";
    consumes = lib.optional enabled "assistant-router";
    extraPorts = lib.optional enabled codePort;
    units = lib.optional enabled name;
    account = if enabled then { uid = 967; } else null;
    secrets =
      lib.listToAttrs (
        # Read by the bridge and by Home Assistant's post-setup, which adds the
        # devices with them, router or not.
        map (
          key:
          lib.nameValuePair (keySecret key) {
            enable = enabled || hasHa;
            owner = "root";
          }
        ) (lib.attrNames devices)
      )
      // {
        # The bridge's SSH key for the devices (ssh-keygen -t ed25519 -N "").
        techo5-ssh-key = {
          enable = installsCa;
          owner = "root";
        };
      };
    readsSecrets = lib.optional (enabled && hasHa) "ha-voice-token";
  };

  # Where the satellite owns the token, the bridge needs its requirement on,
  # even for an LVA satellite (which has no use of its own for it).
  lanbat.voiceSatellite.haTokenWanted = lib.mkIf (enabled && hasHa && satelliteDeclares) true;

  networking.firewall.extraCommands = lib.mkIf enabled (
    lib.concatLines (map (spec: "iptables -I ${spec}") specs)
  );
  networking.firewall.extraStopCommands = lib.mkIf enabled (
    lib.concatLines (map (spec: "iptables -D ${spec} 2>/dev/null || true") specs)
  );

  systemd.services.${name} = lib.mkIf enabled {
    description = "Echo Shows (TECHO5) as the assistant router's bodies";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "assistant-router.service"
    ];
    wants = [ "network-online.target" ];
    environment = {
      CONFIG_FILE = configFile;
      # ssh reads and writes nothing in a home of its own but this.
      HOME = "/var/lib/${name}";
    }
    // lib.optionalAttrs hasHa { HA_TOKEN_FILE = "/run/${name}/ha-token"; };
    serviceConfig = {
      User = name;
      Group = name;
      # The keys and the token are root's: root copies them into the runtime
      # directory for the bridge (the person-mapper pattern).
      ExecStartPre = [
        "+${
          pkgs.writeShellScript "techo5-bridge-secrets" (
            lib.concatLines (
              map (
                key:
                "install -m 0400 -o ${name} -g ${name} ${
                  config.lanbat.secrets.${keySecret key}.path
                } /run/${name}/${keySecret key}"
              ) (lib.attrNames devices)
              ++ lib.optional hasHa "install -m 0400 -o ${name} -g ${name} ${config.lanbat.secrets.ha-voice-token.path} /run/${name}/ha-token"
              ++ lib.optional installsCa "install -m 0400 -o ${name} -g ${name} ${config.lanbat.secrets.techo5-ssh-key.path} /run/${name}/ssh-key"
            )
          )
        }"
      ];
      ExecStart = lib.getExe bridge;
      Restart = "always";
      RestartSec = "5s";
      RuntimeDirectory = name;
      StateDirectory = name;
      RuntimeDirectoryMode = "0700";
      MemoryMax = "256M";
      NoNewPrivileges = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateTmp = true;
      PrivateDevices = true;
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
      ];
    };
  };
}
