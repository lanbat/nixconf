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
# Dashboards are streamed by default: dashcast (pkgs/techo5-dashcast), from
# TECHO5, draws them in a headless Chrome here, signed in to Home Assistant on
# the loopback as the voice satellites' non-admin user, and sends them to the
# devices as pictures over a Noise link keyed with techo5-dashcast-key (port
# 9555, the devices' addresses only). A drawn dashboard would need a
# certificate the device trusts: TECHO5 checks Home Assistant's websocket even
# with "Skip certificate checks", and lanbat's CA is its own.
#
# Always-on: it is in the voice path, as the robots' bridges are. Nothing on
# disk.
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
  dashcastPort = 9555;
  dashcast = pkgs.callPackage ../pkgs/techo5-dashcast { };
  streamed = lib.filterAttrs (_: d: d.dashboard == "streamed") devices;
  # dashcast signs in to Home Assistant with the voice satellites' token.
  dashcastOn = enabled && hasHa && streamed != { };
  haPort = config.lanbat.services.home-assistant.port;
  # Chrome's text: dashboards hold words in any script, and emoji.
  fonts = pkgs.makeFontsConf {
    fontDirectories = [
      pkgs.noto-fonts
      pkgs.noto-fonts-color-emoji
    ];
  };
  serverIp = config.lanbat.hosts.${config.lanbat.hostKey}.networking.ip;
  bodyPort = config.lanbat.services.assistant-router.endpoint.port;

  keySecret = device: "techo5-${device}-api-key";
  # The voice satellites' token: this host's satellite's when it has one,
  # declared here otherwise.
  satellite = config.lanbat.voiceSatellite;
  # The name is voice-satellite's on any host with a satellite, whatever its
  # backend (modules/core/voice-satellite.nix), and a secret's name is one
  # service's alone, enabled or not.
  satelliteDeclares = satellite.enable;

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
      # Nothing to log in to on the device.
      ssh = false;
      remote_api = false;
    }
    // lib.optionalAttrs hasHa {
      screen_dashboard = if device.dashboard == "streamed" then "Streamed" else "Drawn on the device";
      screen_dashboard_kiosk = true;
      insecure_tls = device.insecureTls;
    }
    // device.settings;

  profileJson = p: {
    view = if p.view == null then "" else p.view;
    inherit (p) glance calendars;
    immich_person = p.immichPerson;
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
      devices = lib.mapAttrsToList (key: d: {
        name = key;
        inherit (d) address room;
        key_file = "/run/${name}/${keySecret key}";
        # Generated by pkgs/home-assistant-dashboards.
        dashboard = lib.optionalString hasHa (hostLib.screenDashboard d.room);
        settings = settingsFor d;
        screen_off_after = d.screenOffAfter * 60;
        ha_url = lib.optionalString hasHa "https://ha.${domain}";
        profiles = lib.mapAttrs (_: profileJson) d.profiles;
        household = profileJson d.household;
        dashcast = lib.optionalString (
          dashcastOn && d.dashboard == "streamed"
        ) "${serverIp}:${toString dashcastPort}";
      }) devices;
      dashcast_key_file = lib.optionalString dashcastOn "/run/${name}/dashcast-key";
    }
  );

  # Port 8775 (and dashcast's) for the devices alone (the snapcast.nix pattern).
  specs =
    map (d: "INPUT -p tcp --dport ${toString codePort} -s ${d.address} -j ACCEPT") (
      lib.attrValues devices
    )
    ++ lib.optionals dashcastOn (
      map (d: "INPUT -p tcp --dport ${toString dashcastPort} -s ${d.address} -j ACCEPT") (
        lib.attrValues streamed
      )
    );
in
{
  lanbat.services.${name} = {
    tier = "always-on";
    consumes = lib.optional enabled "assistant-router";
    extraPorts = lib.optional enabled codePort ++ lib.optional dashcastOn dashcastPort;
    units = lib.optional enabled name ++ lib.optional dashcastOn "techo5-dashcast";
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
        # dashcast's key: a device must present it (openssl rand -base64 24).
        techo5-dashcast-key = {
          enable = dashcastOn;
          owner = "root";
        };
      }
      // lib.optionalAttrs (!satelliteDeclares) {
        ha-voice-token = {
          enable = enabled && hasHa;
          owner = "root";
        };
      };
    readsSecrets = lib.optional (enabled && hasHa && satelliteDeclares) "ha-voice-token";
  };

  # Where the satellite owns the token, the bridge needs its requirement on,
  # even for an LVA satellite (which has no use of its own for it).
  lanbat.voiceSatellite.haTokenWanted = enabled && hasHa && satelliteDeclares;

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
              ++ lib.optional dashcastOn "install -m 0400 -o ${name} -g ${name} ${config.lanbat.secrets.techo5-dashcast-key.path} /run/${name}/dashcast-key"
            )
          )
        }"
      ];
      ExecStart = lib.getExe bridge;
      Restart = "always";
      RestartSec = "5s";
      RuntimeDirectory = name;
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

  # dashcast: Chrome runs without its own sandbox (as in TECHO5's container),
  # so this unit is the wall: a user of its own, nothing to write but /tmp,
  # and the network to Home Assistant on the loopback and the devices alone.
  systemd.services.techo5-dashcast = lib.mkIf dashcastOn {
    description = "Home Assistant dashboards streamed to the Echo Shows (TECHO5 dashcast)";
    wantedBy = [ "multi-user.target" ];
    after = [
      "network-online.target"
      "home-assistant.service"
    ];
    wants = [ "network-online.target" ];
    environment = {
      HA_URL = "http://127.0.0.1:${toString haPort}";
      LISTEN = "${serverIp}:${toString dashcastPort}";
      CHROME = "${pkgs.chromium}/bin/chromium";
      HOME = "/tmp";
      FONTCONFIG_FILE = "${fonts}";
    };
    script = ''
      HA_TOKEN="$(< "$CREDENTIALS_DIRECTORY/ha-token")" \
      DASHCAST_KEY="$(< "$CREDENTIALS_DIRECTORY/dashcast-key")" \
        exec ${lib.getExe dashcast}
    '';
    serviceConfig = {
      DynamicUser = true;
      LoadCredential = [
        "ha-token:${config.lanbat.secrets.ha-voice-token.path}"
        "dashcast-key:${config.lanbat.secrets.techo5-dashcast-key.path}"
      ];
      Restart = "always";
      RestartSec = "10s";
      # About 200 MB per screen showing a dashboard, up to 8 screens.
      MemoryMax = "2G";
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      PrivateDevices = true;
      NoNewPrivileges = true;
      IPAddressDeny = "any";
      IPAddressAllow = [ "localhost" ] ++ map (d: d.address) (lib.attrValues streamed);
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
        "AF_UNIX"
        "AF_NETLINK"
      ];
    };
  };

}
