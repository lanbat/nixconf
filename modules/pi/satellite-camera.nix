# modules/pi/satellite-camera.nix
#
# A satellite's USB camera (the PlayStation Eye's, 1415:2000, which is also the
# satellite's microphone array) as a face source for the assistant router
# (lanbat-satellite-camera plugin, pkgs/satellite-camera, docs/people.md).
#
# It looks at the camera every few seconds for movement and sends a small frame
# to the router only then, spaced out; the router recognises it against
# Frigate's face library, names it, and (with unknownPeople) gives a stranger a
# name of their own. It is a router client of its own kind, not a body, so a
# Stack-chan or screen in the same room is untouched. It also answers the
# router's request for a face when someone says "remember me" or taps a phone.
# Frames go to the router only, never anywhere else, and none is kept here.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;
  cfg = config.lanbat.satelliteCamera;
  satellite = config.lanbat.voiceSatellite;
  camera = pkgs.callPackage ../../pkgs/satellite-camera { };
  endpointLib = import ../../lib/endpoints.nix { inherit lib; };
  hostLib = import ../../lib/host.nix { inherit lib; };

  # From deploy data, not the endpoint table: this decides `consumes`, which
  # that table is built from (as in modules/pi/room-presence.nix).
  room = satellite.room or null;
  routerHere = room != null && hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null);
  enabled = cfg.enable && routerHere;
  routerHost = endpointLib.soleHost {
    endpoints = config.lanbat.endpoints;
    name = "assistant-router";
    consumer = "satellite-camera on ${config.lanbat.hostKey}";
  };
  routerAddress = config.lanbat.endpointHost "assistant-router" routerHost;
  routerUrl = "ws://${routerAddress}:${toString config.lanbat.endpoints.assistant-router.endpoint.port}/v1/body";

  usbParts = lib.splitString ":" cfg.usbId;
  link = "satellite-camera";
in
{
  options.lanbat.satelliteCamera = {
    enable = mkOption {
      type = types.bool;
      default = true;
      description = ''
        Send faces the satellite's camera sees to the assistant router (on by
        default where the plugin is enabled). It does nothing without a
        satellite room or the router, and while no camera is plugged in.
      '';
    };
    usbId = mkOption {
      type = types.strMatching "[0-9a-f]{4}:[0-9a-f]{4}";
      default = "1415:2000";
      example = "046d:0825";
      description = ''
        USB vendor:product ID of the camera. The default is the PlayStation
        Eye, which is also the satellite's microphone array. A camera with
        several video nodes is read from its first (index 0).
      '';
    };
    motion = mkOption {
      type = types.numbers.between 1 100;
      default = 6.0;
      description = ''
        How much the picture must change between two looks (mean change per
        pixel, 0 to 255) to count as movement. Lower in a dim room, higher
        where a screen or the light flickers.
      '';
    };
    sampleSeconds = mkOption {
      type = types.numbers.between 1 60;
      default = 3.0;
      description = "How often the camera is looked at, for movement (the camera is opened for one frame and closed).";
    };
    gapSeconds = mkOption {
      type = types.numbers.between 5 600;
      default = 20.0;
      description = "At least this long between two frames sent while the router names nobody.";
    };
    againSeconds = mkOption {
      type = types.numbers.between 10 3600;
      default = 60.0;
      description = "At least this long between two frames once the router named someone (and after a few unnamed ones).";
    };
  };

  config = lib.mkIf enabled {
    lanbat.services.satellite-camera.consumes = [ "assistant-router" ];

    environment.systemPackages = [ camera ];

    # The camera's first video node, readable by the video group, and the unit
    # started when it is plugged in (and stopped when it is unplugged).
    services.udev.extraRules = ''
      SUBSYSTEM=="video4linux", ATTRS{idVendor}=="${lib.elemAt usbParts 0}", ATTRS{idProduct}=="${lib.elemAt usbParts 1}", ATTR{index}=="0", SYMLINK+="${link}", GROUP="video", MODE="0660", TAG+="systemd", ENV{SYSTEMD_WANTS}+="satellite-camera.service"
    '';

    systemd.services.satellite-camera = {
      description = "The satellite's camera, for the assistant router";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      bindsTo = [ "dev-${link}.device" ];
      unitConfig.ConditionPathExists = "/dev/${link}";
      environment = {
        ROUTER_BODY_URL = routerUrl;
        ROOM = room;
        DEVICE = "/dev/${link}";
        MOTION = toString cfg.motion;
        SAMPLE_S = toString cfg.sampleSeconds;
        GAP_S = toString cfg.gapSeconds;
        AGAIN_S = toString cfg.againSeconds;
      };
      serviceConfig = {
        ExecStart = lib.getExe camera;
        Restart = "always";
        RestartSec = "10s";
        DynamicUser = true;
        SupplementaryGroups = [ "video" ];
        # The camera, and nothing else of /dev.
        PrivateDevices = false;
        DevicePolicy = "closed";
        DeviceAllow = [ "char-video4linux rw" ];
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
