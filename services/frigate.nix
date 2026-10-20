# services/frigate.nix
#
# Frigate NVR — camera recording and detection.
#
# Configuration
# -------------
# The cameras, zones, detector and retention are deployment settings under
# lanbat.services.frigate.settings (options below). This module renders them,
# together with the parts fixed by how the container is run (database path,
# model, MQTT, go2rtc restream), into Frigate's config.yml. Keys the schema
# does not model go in settings.extraConfig (global) or
# settings.cameras.<name>.extraConfig (one camera), merged last.
#
# A profile sets them in deployments/<profile>/frigate.nix, gitignored like its
# deploy.nix and listed in hosts.server.modules there;
# deployments/example/frigate.nix shows every option with placeholder values.
# Evaluation fails when Frigate has no camera, unless settings.allowNoCameras.
#
# Frigate 0.17 constraints the rendering keeps:
#   - camera inputs live under cameras.<name>.ffmpeg.inputs (Camera.__init__
#     reads config['ffmpeg']['inputs']);
#   - an input takes no output_args (CameraInput has additionalProperties:
#     false); record output_args belong at cameras.<name>.ffmpeg.output_args,
#     and a bare preset name inside an output_args list is not expanded;
#   - retention is record.motion.days and record.{detections,alerts}.retain.days;
#   - semantic search stays off: its CLIP embeddings cost more CPU than the
#     detector itself. Licence plate recognition, where a profile enables it,
#     starts the same embeddings process and is the largest Frigate CPU user
#     (about one core on the server, more than the detector's half core).
#
# Temporary UI tuning
# -------------------
# The config is mounted read-only so Frigate's web UI can't save changes.
# To temporarily enable UI editing (zones, masks, filters), run on the server:
#
#   src=$(sudo -u frigate podman inspect frigate \
#     --format '{{range .Mounts}}{{if eq .Destination "/config/config.yml"}}{{.Source}}{{end}}{{end}}')
#   cp "$src" /var/lib/frigate/config.yml
#   chown frigate:frigate /var/lib/frigate/config.yml
#   systemctl stop podman-frigate
#   mount --bind /var/lib/frigate/config.yml "$src"
#   systemctl start podman-frigate
#
# The UI can now save changes. When done, retrieve the tuned config:
#   cat /var/lib/frigate/config.yml
# Then port the values back into the deployment's Frigate settings and
# rebuild. A nixos-rebuild or reboot undoes the bind mount automatically.
#
# Storage
# -------
# All state is local (always-on tier):
#   /var/lib/frigate/db/         — SQLite event metadata
#   /var/lib/frigate/clips/      — review snapshots/clips
#   /var/lib/frigate/recordings/ — motion-only recordings
#   /var/cache/frigate/          — clip buffer (safe to lose)
#
# rclone cloud sync will be added later.
#
# Detector
# --------
# Intel OpenVINO via /dev/dri (iGPU), running the bundled YOLOv8n model.
#
# Credentials
# -----------
# Camera credentials: secrets/frigate-rtsp-env.age, an environment file
#   FRIGATE_RTSP_USER=<camera user>
#   FRIGATE_RTSP_PASSWORD=<camera password>
# Frigate substitutes {FRIGATE_*} references in camera sources, so a source
# names {FRIGATE_RTSP_USER} rather than the credential itself.
# MQTT password: secrets/mosquitto-frigate-pass.age (plaintext)
#
# Both are combined into /run/frigate-env by ExecStartPre and injected
# into the container via --env-file.
#
# Home Assistant integration
# --------------------------
# Frigate publishes events via MQTT → Home Assistant listens.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  inherit (lib) mkOption types;

  yolov8nOpenVinoModel = pkgs.callPackage ../pkgs/frigate-yolov8n-openvino-model { };

  cfg = config.lanbat.services.frigate.settings;

  # Frigate records and detects perfectly well on its own; MQTT is how it tells
  # Home Assistant about events. A deployment without a broker keeps the camera
  # side and loses the announcements.
  hasMqtt = config.lanbat.hasService "mosquitto";

  # go2rtc re-serves every camera stream here; extraPorts below opens it.
  restreamPort = 8554;

  # Frigate's own name rule for cameras, zones and go2rtc streams.
  frigateName = "[A-Za-z0-9_-]+";

  # "x1,y1,x2,y2,...": at least three points, as Frigate's zone editor writes
  # them (fractions of the frame, or pixels).
  number = "[0-9]+(\\.[0-9]+)?";
  coordinates = types.strMatching "${number},${number}(,${number},${number}){2,}";

  score = types.numbers.between 0 1;

  optional =
    type: description:
    mkOption {
      type = types.nullOr type;
      default = null;
      inherit description;
    };

  rawConfig =
    description:
    mkOption {
      type = types.attrsOf types.anything;
      default = { };
      inherit description;
    };

  inputModule = {
    options = {
      stream = mkOption {
        type = types.strMatching frigateName;
        example = "driveway_sub";
        description = ''
          go2rtc stream name. Unique across all cameras: go2rtc has one stream
          namespace, and Frigate reads the stream back from
          rtsp://127.0.0.1:${toString restreamPort}/<stream>.
        '';
      };
      source = mkOption {
        type = types.str;
        example = "rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera.example.com:554/sub";
        description = ''
          go2rtc source for the stream (rtsp://, ffmpeg:..., and so on).
          Reference credentials as {FRIGATE_RTSP_USER} and
          {FRIGATE_RTSP_PASSWORD}, which Frigate substitutes from the
          frigate-rtsp-env secret; never write them here.
        '';
      };
      roles = mkOption {
        type = types.nonEmptyListOf (
          types.enum [
            "detect"
            "record"
            "audio"
          ]
        );
        example = [ "record" ];
        description = "What Frigate uses this input for. Exactly one input per camera has detect.";
      };
      inputArgs = mkOption {
        type = types.str;
        default = "preset-rtsp-restream";
        description = "ffmpeg input_args for reading the restreamed input.";
      };
    };
  };

  zoneModule = {
    options = {
      coordinates = mkOption {
        type = coordinates;
        example = "0,0,1,0,1,0.22,0,0.22";
        description = "Zone polygon as comma-separated x,y pairs, as Frigate's zone editor writes it.";
      };
      friendlyName = optional types.str "Name shown in the UI (friendly_name).";
      inertia = optional types.ints.positive "Frames an object must be in the zone before it counts (inertia).";
      loiteringTime = optional types.ints.unsigned "Seconds an object must stay to count as loitering (loitering_time).";
      objects = optional (types.listOf types.str) "Only these labels count in the zone (objects).";
    };
  };

  filterModule = {
    options = {
      minScore = optional score "Minimum score for a detection to start tracking (min_score).";
      threshold = optional score "Median score a tracked object needs to count (threshold).";
    };
  };

  reviewModule = {
    options = {
      labels = optional (types.listOf types.str) "Labels that produce this review item (labels).";
      cutoffTime = optional types.ints.unsigned "Seconds without activity that end the review item (cutoff_time).";
    };
  };

  cameraModule = {
    options = {
      inputs = mkOption {
        type = types.nonEmptyListOf (types.submodule inputModule);
        description = ''
          The camera's streams, in order. Each is ingested by go2rtc and read
          by Frigate from the local restream, so the camera sees one client
          per stream however many consumers there are.
        '';
      };

      detect = {
        enable = mkOption {
          type = types.bool;
          default = true;
          description = "Run object detection on this camera (detect.enabled).";
        };
        width = optional types.ints.positive "Width of the detect stream (detect.width).";
        height = optional types.ints.positive "Height of the detect stream (detect.height).";
        fps = mkOption {
          type = types.ints.positive;
          default = 5;
          description = "Frames per second sent to the detector (detect.fps).";
        };
        minInitialized = optional types.ints.positive "Consecutive hits before an object is tracked (detect.min_initialized).";
      };

      zones = mkOption {
        type = types.attrsOf (types.submodule zoneModule);
        default = { };
        description = "Zones by name. The name must match ${frigateName}.";
      };

      objects = {
        track = optional (types.listOf types.str) "Labels to track (objects.track); Frigate's default is person.";
        filters = mkOption {
          type = types.attrsOf (types.submodule filterModule);
          default = { };
          description = "Score filters by label (objects.filters).";
        };
      };

      review = {
        alerts = mkOption {
          type = types.submodule reviewModule;
          default = { };
          description = "Which detections become alerts (review.alerts).";
        };
        detections = mkOption {
          type = types.submodule reviewModule;
          default = { };
          description = "Which detections become review detections (review.detections).";
        };
      };

      motion = {
        threshold = optional (types.ints.between 1 255) "Pixel change that counts as motion (motion.threshold).";
        contourArea = optional types.ints.positive "Smallest moving area that counts (motion.contour_area).";
      };

      lpr = {
        enable = optional types.bool "Licence plate recognition on this camera (lpr.enabled).";
        enhancement = optional (types.ints.between 0 10) "Image enhancement before OCR (lpr.enhancement).";
        minArea = optional types.ints.positive "Smallest plate area to read (lpr.min_area).";
      };

      notifications = optional types.bool "Web push notifications for this camera (notifications.enabled).";

      extraConfig = rawConfig ''
        Raw Frigate configuration for this camera, merged last over what the
        options above render (lib.recursiveUpdate: attribute sets merge, any
        other value, lists included, replaces).
      '';
    };
  };

  frigateSettings = {
    options = {
      cameras = mkOption {
        type = types.attrsOf (types.submodule cameraModule);
        default = { };
        example = lib.literalExpression ''
          {
            driveway = {
              inputs = [
                {
                  stream = "driveway";
                  source = "rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera.example.com:554/main";
                  roles = [ "detect" ];
                }
                {
                  stream = "driveway_sub";
                  source = "rtsp://{FRIGATE_RTSP_USER}:{FRIGATE_RTSP_PASSWORD}@camera.example.com:554/sub";
                  roles = [ "record" ];
                }
              ];
              detect = { width = 1280; height = 720; };
              zones.drive.coordinates = "0,0.5,1,0.5,1,1,0,1";
            };
          }
        '';
        description = "Cameras by name. The name must match ${frigateName}.";
      };

      allowNoCameras = mkOption {
        type = types.bool;
        default = false;
        description = ''
          Let Frigate run with no cameras. Evaluation fails otherwise, so a
          profile that loses its camera module (deployments/<profile>/frigate.nix
          is gitignored) cannot deploy a Frigate that silently records nothing.
          Meant for test fixtures.
        '';
      };

      detector.device = mkOption {
        type = types.str;
        default = "AUTO";
        example = "CPU";
        description = ''
          OpenVINO device the bundled YOLOv8n model runs on: AUTO, GPU (the
          iGPU through /dev/dri) or CPU.
        '';
      };

      faceRecognition = {
        enable = mkOption {
          type = types.bool;
          default = false;
          description = ''
            Local face recognition on the detect stream (face_recognition.enabled).
            Frigate's bundled FaceNet model names faces it has been shown; the
            person mapper (services/person-mapper.nix) reads those matches from
            the frigate MQTT topics. Enrolling people happens in Frigate's face
            library, not here.
          '';
        };
        modelSize = mkOption {
          type = types.enum [
            "small"
            "medium"
            "large"
          ];
          default = "small";
          description = "FaceNet model size (face_recognition.model_size).";
        };
        confidence = optional score "Minimum match score to name a person (face_recognition.confidence).";
        threshold = optional score "Detection confidence threshold (face_recognition.threshold).";
      };

      retention = {
        motionDays = mkOption {
          type = types.ints.unsigned;
          default = 7;
          description = "Days of motion-only recording kept (record.motion.days).";
        };
        detectionDays = mkOption {
          type = types.ints.unsigned;
          default = 14;
          description = "Days detection clips are kept (record.detections.retain.days).";
        };
        alertDays = mkOption {
          type = types.ints.unsigned;
          default = 14;
          description = "Days alert clips are kept (record.alerts.retain.days).";
        };
        snapshotDays = mkOption {
          type = types.ints.unsigned;
          default = 30;
          description = "Days snapshots are kept (snapshots.retain.default).";
        };
      };

      extraConfig = rawConfig ''
        Raw Frigate configuration, merged last over the whole rendered config
        (lib.recursiveUpdate: attribute sets merge, any other value, lists
        included, replaces). The escape hatch for keys the options above do not
        model, such as the global lpr section; it can also override anything
        this module renders.
      '';
    };
  };

  # Drop unset (null) options, and sections left empty by that, so the
  # rendered config names only what a deployment chose.
  clean =
    v:
    if lib.isAttrs v then
      lib.filterAttrs (_: x: x != null && x != { }) (lib.mapAttrs (_: clean) v)
    else
      v;

  renderCamera =
    _: cam:
    lib.recursiveUpdate (clean {
      ffmpeg.inputs = map (input: {
        path = "rtsp://127.0.0.1:${toString restreamPort}/${input.stream}";
        input_args = input.inputArgs;
        inherit (input) roles;
      }) cam.inputs;
      detect = {
        enabled = cam.detect.enable;
        inherit (cam.detect) width height fps;
        min_initialized = cam.detect.minInitialized;
      };
      lpr = {
        enabled = cam.lpr.enable;
        inherit (cam.lpr) enhancement;
        min_area = cam.lpr.minArea;
      };
      zones = lib.mapAttrs (_: zone: {
        inherit (zone) coordinates inertia objects;
        loitering_time = zone.loiteringTime;
        friendly_name = zone.friendlyName;
      }) cam.zones;
      objects = {
        inherit (cam.objects) track;
        filters = lib.mapAttrs (_: f: {
          min_score = f.minScore;
          inherit (f) threshold;
        }) cam.objects.filters;
      };
      review = lib.mapAttrs (_: r: {
        inherit (r) labels;
        cutoff_time = r.cutoffTime;
      }) cam.review;
      motion = {
        inherit (cam.motion) threshold;
        contour_area = cam.motion.contourArea;
      };
      notifications.enabled = cam.notifications;
    }) cam.extraConfig;

  allInputs = lib.concatMap (cam: cam.inputs) (lib.attrValues cfg.cameras);

  rendered = lib.recursiveUpdate {
    mqtt =
      if hasMqtt then
        {
          enabled = true;
          host = "127.0.0.1";
          port = 1883;
          user = "frigate";
          password = "{FRIGATE_MQTT_PASSWORD}";
        }
      else
        # No broker in this deployment, so no event announcements.
        { enabled = false; };

    database.path = "/media/frigate/db/frigate.db";

    record = {
      enabled = true;
      motion.days = cfg.retention.motionDays;
      detections.retain.days = cfg.retention.detectionDays;
      alerts.retain.days = cfg.retention.alertDays;
    };

    snapshots = {
      enabled = true;
      retain.default = cfg.retention.snapshotDays;
    };

    # Global model config — read by all detectors via detector_config.model
    # (OvDetectorConfig inherits model from BaseDetectorConfig, not its own field).
    model = {
      path = "/models/yolov8n_openvino_model/yolov8n.xml";
      labelmap_path = "/labelmap/coco-80.txt";
      model_type = "yolo-generic";
      width = 640;
      height = 640;
      input_tensor = "nchw";
      input_dtype = "float";
      input_pixel_format = "rgb";
    };

    detectors.ov = {
      type = "openvino";
      inherit (cfg.detector) device;
    };

    go2rtc.streams = lib.listToAttrs (
      map (input: lib.nameValuePair input.stream [ input.source ]) allInputs
    );

    # Auto-detected vaapi hwaccel fails in rootless Podman without DRM access.
    ffmpeg.hwaccel_args = [ ];

    cameras = lib.mapAttrs renderCamera cfg.cameras;

    notifications.enabled = true;

    # The CLIP embeddings were the container's biggest CPU user (about 1.5
    # cores, more than the detector). Detection, LPR and zones are unaffected.
    # With LPR on, the embeddings process still runs for it: about one core.
    semantic_search = {
      enabled = false;
      model_size = "small";
    };

    # Face recognition is off by default; the person mapper names whatever
    # Frigate's face library holds when it is on.
    face_recognition = clean {
      enabled = cfg.faceRecognition.enable;
      model_size = cfg.faceRecognition.modelSize;
      confidence = cfg.faceRecognition.confidence;
      threshold = cfg.faceRecognition.threshold;
    };

    classification.bird.enabled = false;

    version = "0.17-0";
  } cfg.extraConfig;

  frigateConfig = (pkgs.formats.yaml { }).generate "frigate.yml" rendered;

  badNames =
    what: names:
    map (n: {
      assertion = builtins.match frigateName n != null;
      message = "lanbat.services.frigate: ${what} name \"${n}\" must match ${frigateName}.";
    }) names;

  streamNames = map (i: i.stream) allInputs;
  duplicateStreams = lib.unique (lib.filter (s: lib.count (x: x == s) streamNames > 1) streamNames);
in
{

  options.lanbat.frigate.renderedConfig = mkOption {
    type = types.attrsOf types.anything;
    readOnly = true;
    internal = true;
    description = "The Frigate configuration rendered from the settings, as written to config.yml.";
  };

  config = {
    # The schema is merged into this service's settings submodule, so it is typed
    # and documented exactly where a deployment sets it, and
    # modules/wiring/checks.nix rejects any key it does not declare.
    lanbat.settingsSchema.frigate = frigateSettings;

    lanbat.frigate.renderedConfig = rendered;

    assertions =
      badNames "camera" (lib.attrNames cfg.cameras)
      ++ lib.concatLists (
        lib.mapAttrsToList (
          camName: cam: badNames "camera ${camName} zone" (lib.attrNames cam.zones)
        ) cfg.cameras
      )
      ++ lib.mapAttrsToList (camName: cam: {
        assertion = lib.count (i: lib.elem "detect" i.roles) cam.inputs == 1;
        message = "lanbat.services.frigate: camera ${camName} needs exactly one input with the detect role.";
      }) cfg.cameras
      ++ [
        {
          assertion = cfg.cameras != { } || cfg.allowNoCameras;
          message = ''
            lanbat.services.frigate has no cameras. Set lanbat.services.frigate.settings.cameras
            from a module in the host's modules in deploy.nix, conventionally
            deployments/<profile>/frigate.nix (see deployments/example/frigate.nix),
            or set settings.allowNoCameras = true to run Frigate without any.
          '';
        }
        {
          assertion = duplicateStreams == [ ];
          message = "lanbat.services.frigate: go2rtc stream names used more than once: ${lib.concatStringsSep ", " duplicateStreams}.";
        }
      ];

    lanbat.services.frigate = {
      subdomain = "nvr";
      port = 5000;
      consumes = lib.optional hasMqtt "mosquitto";
      # Its broker password, which mosquitto.nix declares.
      readsSecrets = lib.optional hasMqtt "mosquitto-frigate-pass";
      extraPorts = [ restreamPort ]; # RTSP restream
      auth = "forward-auth";
      # Homepage's Frigate widget calls /api/* without an Authentik session.
      caddy.authBypassPaths = [ "/api/*" ];
      account = {
        uid = 995;
        container = true;
        # media: writes recordings to NFS. render + video: /dev/dri for OpenVINO.
        extraGroups = [
          "media"
          "render"
          "video"
        ];
        # Map the host video (26) and render (303) groups into the container.
        extraSubGidRanges = [
          {
            startGid = 26;
            count = 1;
          }
          {
            startGid = 303;
            count = 1;
          }
        ];
      };
      secrets = {
        frigate-rtsp-env.owner = "root"; # read by ExecStartPre
        rclone-frigate-config = { };
      };
      dashboard = {
        group = "Surveillance";
        name = "Frigate";
        description = "NVR & object detection";
        widget = {
          type = "frigate";
          enableRecentEvents = true;
        };
      };
    };

    # ---------------------------------------------------------------------------
    # Frigate container
    # ---------------------------------------------------------------------------
    virtualisation.oci-containers.containers."frigate" = {
      image = "ghcr.io/blakeblackshear/frigate:stable";

      # environmentFiles would become systemd EnvironmentFile= (read before
      # ExecStartPre runs, so the file doesn't exist yet).  Use --env-file in
      # extraOptions instead — podman reads it during ExecStart, after ExecStartPre
      # has already created /run/frigate-env.

      volumes = [
        "${frigateConfig}:/config/config.yml:ro"
        "/var/lib/frigate/db:/media/frigate/db"
        "/var/lib/frigate/clips:/media/frigate/clips"
        "/var/lib/frigate/recordings:/media/frigate/recordings"
        # The models Frigate downloads (face detection, landmarks, embeddings,
        # plates). Kept, because Frigate looks for the face landmark model once,
        # as it starts, while a fresh container is still downloading it: with
        # the cache in the container, face recognition never named anyone.
        "/var/lib/frigate/model_cache:/config/model_cache"
        "/var/cache/frigate:/tmp/cache"
        "/etc/localtime:/etc/localtime:ro"
        "${yolov8nOpenVinoModel}:/models/yolov8n_openvino_model:ro"
      ];

      extraOptions = [
        "--network=host"
        "--shm-size=256m"
        "--device=/dev/dri"
        # Pass the host frigate user's supplemental groups (render, video) into the
        # container by GID.  --group-add=keep-groups is the rootless Podman way —
        # using group names would look them up in the container's /etc/group, which
        # doesn't have render/video.
        "--group-add=keep-groups"
        # Env file created by ExecStartPre — pass directly to the container.
        "--env-file=/run/frigate-env"
      ];

      podman.user = "frigate";
      user = "0";
      autoStart = true;
    };

    # ---------------------------------------------------------------------------
    # Write combined env file before container starts
    # ---------------------------------------------------------------------------
    systemd.services."podman-frigate" = {
      # Order after the frigate user's systemd session (linger bus at
      # /run/user/995) — crun's systemd cgroup manager needs that bus to place
      # the pause process in its sandbox cgroup.
      after = [ "user@995.service" ];
      wants = [ "user@995.service" ];
      serviceConfig = {
        Restart = lib.mkForce "on-failure";
        RestartSec = "15s";
        ExecStartPre = [
          # Runs as root (+ prefix) even though the service User=frigate.
          # Combines both secrets into /run/frigate-env and hands ownership
          # to the frigate user so Podman (running rootless as frigate) can
          # read the env file.
          "+${pkgs.writeShellScript "frigate-write-env" ''
            set -euo pipefail
            {
              # awk 1 ensures a trailing newline even if the secret file lacks one,
              # preventing the next printf from being appended to the last line.
              ${pkgs.gawk}/bin/awk 1 ${config.lanbat.secrets.frigate-rtsp-env.path}
              ${lib.optionalString hasMqtt ''
                printf 'FRIGATE_MQTT_PASSWORD=%s\n' \
                  "$(${pkgs.coreutils}/bin/tr -d '\n' < ${config.lanbat.secrets.mosquitto-frigate-pass.path})"
              ''}
            } > /run/frigate-env
            chown frigate:frigate /run/frigate-env
            chmod 600 /run/frigate-env
          ''}"
        ];
      };
    };

    # ---------------------------------------------------------------------------
    # Face classifier
    # ---------------------------------------------------------------------------
    # Frigate (0.17) builds its face classifier on a thread of its own, and the
    # first build, while Frigate is still starting, never finishes: recognition
    # then names nobody ("No face was recognized.") until the classifier is
    # built again. A later build works. So, after each start: clear the
    # classifier (an empty delete in the "train" folder, which removes
    # nothing), and check with a picture from the face library that it names
    # its person, trying again for about ten minutes.
    systemd.services.frigate-face-classifier = lib.mkIf cfg.faceRecognition.enable {
      description = "Build Frigate's face classifier once Frigate has started";
      wantedBy = [ "podman-frigate.service" ];
      after = [ "podman-frigate.service" ];
      partOf = [ "podman-frigate.service" ];
      path = [
        pkgs.curl
        pkgs.findutils
        pkgs.coreutils
        pkgs.gnugrep
      ];
      script = ''
        api=http://127.0.0.1:5000/api
        probe=$(find /var/lib/frigate/clips/faces -mindepth 2 -maxdepth 2 -type f \
          ! -path '*/faces/train/*' 2>/dev/null | head -n 1)
        if [ -z "$probe" ]; then
          echo "no faces in the library yet: nothing to build"
          exit 0
        fi
        for _ in $(seq 60); do
          curl -sf "$api/version" >/dev/null && break
          sleep 5
        done
        for attempt in $(seq 10); do
          sleep 60
          curl -sf -X POST -H 'Content-Type: application/json' -d '{"ids":[]}' \
            "$api/faces/train/delete" >/dev/null || continue
          for _ in $(seq 12); do
            if curl -s -X POST -F "file=@$probe" "$api/faces/recognize" | grep -q '"success":true'; then
              echo "face classifier built (attempt $attempt)"
              exit 0
            fi
            sleep 5
          done
        done
        echo "the face classifier did not build: faces stay unnamed until Frigate restarts" >&2
        exit 1
      '';
      serviceConfig = {
        Type = "oneshot";
        User = "frigate";
        Group = "frigate";
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };

    # ---------------------------------------------------------------------------
    # State directories
    # ---------------------------------------------------------------------------
    systemd.tmpfiles.rules = [
      "d /var/cache/frigate           0750 frigate frigate -"
      "d /var/lib/frigate/db          0750 frigate frigate -"
      "d /var/lib/frigate/clips       0750 frigate frigate -"
      "d /var/lib/frigate/recordings  0750 frigate frigate -"
      "d /var/lib/frigate/model_cache 0750 frigate frigate -"
    ];
  };
}
