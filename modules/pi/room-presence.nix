# modules/pi/room-presence.nix
#
# Whose phone is in the room (lanbat-room-presence plugin): a Bluetooth LE scan
# on a satellite Pi recognises the phones of the profile's people and tells
# the assistant router who is near, as a room sensor (pkgs/room-presence,
# docs/people.md).
#
# Android phones are known by the iBeacon the Home Assistant app transmits
# (people.<key>.bleBeacon); iPhones by their IRK, from the room-presence-irks
# secret when `irks` is set. Only person keys and signal strengths leave the
# Pi, and only to the router. A nearby phone is a hint, never authority.
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
  enabled = cfg.enable && routerHere && people != { };
  routerHost = endpointLib.soleHost {
    endpoints = config.lanbat.endpoints;
    name = "assistant-router";
    consumer = "room-presence on ${config.lanbat.hostKey}";
  };
  routerAddress = config.lanbat.endpointHost "assistant-router" routerHost;
  routerUrl = "ws://${routerAddress}:${toString config.lanbat.endpoints.assistant-router.endpoint.port}/v1/body";

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
        Scan for the phones of lanbat.deployment.people and tell the assistant
        router who is near (on by default where the plugin is enabled). It does
        nothing without people, a satellite room or the router.
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
      };
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
        ];
      };
    };
  };
}
