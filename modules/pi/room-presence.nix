# modules/pi/room-presence.nix
#
# Whose phone is in the room (lanbat-room-presence plugin): a Bluetooth LE scan
# on a satellite Pi recognises the phones of the profile's people and tells
# the assistant router who is near, as a room sensor (pkgs/room-presence,
# docs/people.md).
#
# Phones are known by their Bluetooth key (IRK): the router hands every room
# sensor the keys of the phones paired once (assistant-router irks.py), and the
# room-presence-irks secret adds more when `irks` is set. Android phones are
# also known by the iBeacon the Home Assistant app transmits
# (people.<key>.bleBeacon). On a host that serves Wi-Fi
# (lanbat.wifi.accessPoint) it also reports the phones among the access
# point's clients, by their Wi-Fi address (people.<key>.wifiMacs, and those the
# router learned from taps). Only person keys and signal strengths leave the
# host, and only to the router. A nearby phone is a hint, never authority.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.roomPresence;
  satellite = config.lanbat.voiceSatellite;
  people = config.lanbat.deployment.people or { };
  scanner = pkgs.callPackage ../../pkgs/room-presence { };
  endpointLib = import ../../lib/endpoints.nix { inherit lib; };
  hostLib = import ../../lib/host.nix { inherit lib; };

  # From deploy data, not the endpoint table: this decides `consumes`, which
  # that table is built from (as in modules/pi/stackchan.nix).
  room = satellite.room or null;
  routerHere = room != null && hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null);
  # People come from the router (its keys) as well as the profile: none listed is fine.
  enabled = cfg.enable && routerHere;
  routerHost = endpointLib.soleHost {
    endpoints = config.lanbat.endpoints;
    name = "assistant-router";
    consumer = "room-presence on ${config.lanbat.hostKey}";
  };
  routerAddress = config.lanbat.endpointHost "assistant-router" routerHost;
  routerUrl = "ws://${routerAddress}:${toString config.lanbat.endpoints.assistant-router.endpoint.port}/v1/body";

  # The phones among an access point's clients, when this host serves Wi-Fi.
  ap = config.lanbat.wifi.accessPoint or { };
  apHere = ap.enable or false;
  wifiPhones = lib.filterAttrs (_: macs: macs != [ ]) (lib.mapAttrs (_: p: p.wifiMacs or [ ]) people);

  beacons = lib.concatStringsSep "," (
    lib.mapAttrsToList (key: p: "${key}=${p.bleBeacon}") (
      lib.filterAttrs (_: p: (p.bleBeacon or null) != null) people
    )
  );
in
{
  options.lanbat.roomPresence = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Scan for people's phones and tell the assistant router who is near (on
        by default where the plugin is enabled). It does nothing without a
        satellite room or the router.
      '';
    };
    irks = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Recognise iPhones (and other phones without the beacon) by their
        Identity Resolving Key, from the room-presence-irks secret: lines of
        "<person key> <32 hex digits>". `room-presence --paired` prints the
        IRKs of phones paired with this Pi (docs/people.md).
      '';
    };
  };

  config = lib.mkIf enabled {
    # The Pi's controller: on for every voice Pi already (modules/pi/speakers.nix).
    hardware.bluetooth.enable = true;

    environment.systemPackages = [ scanner ];

    lanbat.services.room-presence = {
      consumes = [ "assistant-router" ];
      secrets.room-presence-irks = {
        enable = cfg.irks;
        owner = "root";
      };
    };

    systemd.services.room-presence = {
      description = "Whose phone is in the room, for the assistant router";
      wantedBy = [ "multi-user.target" ];
      after = [
        "bluetooth.service"
        "network-online.target"
      ];
      wants = [
        "bluetooth.service"
        "network-online.target"
      ];
      environment = {
        ROUTER_BODY_URL = routerUrl;
        ROOM = room;
        PEOPLE = lib.concatStringsSep "," (lib.attrNames people);
        BEACONS = beacons;
      }
      // lib.optionalAttrs cfg.irks {
        IRK_FILE = config.lanbat.secrets.room-presence-irks.path;
      }
      // lib.optionalAttrs apHere {
        AP_INTERFACE = ap.interface;
        WIFI_PHONES = builtins.toJSON wifiPhones;
      };
      # `iw` reads the access point's clients.
      path = lib.optional apHere pkgs.iw;
      serviceConfig = {
        ExecStart = lib.getExe scanner;
        # BlueZ lets root scan over D-Bus; the rest is locked down.
        Restart = "always";
        RestartSec = "10s";
        Nice = 10;
        MemoryMax = "128M";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        IPAddressDeny = "any";
        IPAddressAllow = [
          "localhost"
          routerAddress
        ];
        RestrictAddressFamilies = [
          "AF_UNIX"
          "AF_INET"
          "AF_INET6"
        ]
        # nl80211, for the access point's clients.
        ++ lib.optional apHere "AF_NETLINK";
      };
    };
  };
}
