# deployments/example/frigate.nix
#
# Frigate settings for the example profile. services/frigate.nix documents
# every option; the values here are placeholders that show each of them.
#
# A real profile keeps its own deployments/<profile>/frigate.nix next to its
# deploy.nix (gitignored, like deploy.nix) and lists it in
# hosts.server.modules.
#
# go2rtc ingests each camera stream once and Frigate reads it back from the
# local restream. Credentials stay in the frigate-rtsp-env secret: sources
# only name {FRIGATE_RTSP_USER} and {FRIGATE_RTSP_PASSWORD}.
{ config, ... }:

let
  camera = "front-camera.${config.lanbat.deployment.rootDomain}";
  rtsp = path: "rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@${camera}:554/${path}";

  people = [ "person" ];
  vehicles = [
    "car"
    "bicycle"
  ];
in
{
  lanbat.services.frigate.settings = {
    # Recordings and clips live on this drive's /srv/storage/<drive>/surveillance.
    drive = "a";

    detector.device = "AUTO";

    retention = {
      motionDays = 7;
      detectionDays = 14;
      alertDays = 14;
      snapshotDays = 30;
    };

    cameras.front = {
      inputs = [
        # Main stream for detection.
        {
          stream = "front";
          source = rtsp "main";
          roles = [ "detect" ];
        }
        # Sub stream for recording, which keeps the rolling window small.
        {
          stream = "front_sub";
          source = rtsp "sub";
          roles = [ "record" ];
        }
      ];

      detect = {
        width = 1280;
        height = 720;
        fps = 5;
        minInitialized = 2;
      };

      zones = {
        entrance = {
          coordinates = "0,0.6,0.5,0.6,0.5,1,0,1";
          friendlyName = "Entrance";
          inertia = 3;
          loiteringTime = 0;
          objects = people;
        };
        street = {
          coordinates = "0,0,1,0,1,0.2,0,0.2";
          friendlyName = "Street";
          inertia = 3;
        };
      };

      objects = {
        track = people ++ vehicles;
        filters = {
          person = {
            minScore = 0.5;
            threshold = 0.7;
          };
          car.threshold = 0.7;
        };
      };

      review = {
        alerts = {
          labels = people;
          cutoffTime = 30;
        };
        detections = {
          labels = people ++ vehicles;
          cutoffTime = 30;
        };
      };

      motion = {
        threshold = 25;
        contourArea = 10;
      };

      lpr = {
        enable = true;
        enhancement = 2;
        minArea = 1000;
      };

      notifications = true;

      # Raw Frigate keys for this camera, merged last.
      extraConfig.motion.mask = [ "0.8,0,1,0,1,0.1,0.8,0.1" ];
    };

    # Raw Frigate keys, merged last: here licence plate recognition, which the
    # schema does not model globally. It needs car in objects.track.
    extraConfig.lpr = {
      enabled = true;
      min_plate_length = 4;
      # Placeholder: match your region's plate format, or leave it unset.
      format = "^[A-Z0-9]{4,8}$";
    };
  };
}
