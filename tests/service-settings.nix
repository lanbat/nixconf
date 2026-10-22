# tests/service-settings.nix
#
# Service settings schemas other than Frigate's (tests/frigate-settings.nix).
# Pure evaluation of the example profile, and of variants of it whose server
# carries one more module, as a profile's hosts.<key>.modules would:
#
#   - Samba: the default share layout renders as before; a profile adds,
#     changes and drops shares field by field, smbd binds to the NFS mounts of
#     exactly the drives the shares use, and an unknown key is rejected.
#   - Wyoming: the defaults keep today's pipeline and server satellite, and a
#     profile's voice, model and ALSA card reach the servers, the satellite and
#     Home Assistant's pipeline.
#   - Home Assistant: the loopback URLs of the services it wires come from
#     their descriptions, and the Zigbee2MQTT bridge watch follows whether
#     Zigbee2MQTT runs on the host unless the profile says otherwise.
#   - Telegraf: InfluxDB, Redis and the health checks are reached at the ports
#     the services' descriptions give, and a profile sets the ping targets.
#   - RomM: the library and the browser's arcade copies default to drive b's
#     media/roms and media/roms-browser/mame, a profile moves them, and the
#     NFS dependency follows the drive.
#   - Music Assistant: its setup reaches Music Assistant and Home Assistant at
#     the ports and subdomains of their descriptions. The fanart.tv VIP key is
#     off and needs no key by default; settings.fanartTvVip requires
#     ma-fanarttv-key and hands it to the setup.
#   - Immich: the originals default to drive a's photos, a profile moves them
#     and the NFS dependency follows; PostgreSQL, Redis and its own API are
#     reached at the ports of their descriptions.
#   - Grafana: its InfluxDB datasource follows InfluxDB's endpoint port.
#   - Nextcloud: the bulk data directories default to drive b's nextcloud,
#     and a profile moves them.
#   - Jellyfin: IMVDb is off and needs no key by default; settings.imvdb
#     requires jellyfin-imvdb-env and hands it to jellyfin-bootstrap.
#   - Audiobookshelf: the library defaults to drive b's media/audiobooks and
#     Audible, a profile moves it and picks another provider, and the NFS
#     dependency follows the drive.
#   - Stack-chan (lanbat-stackchan plugin on the example Pi 3): the bridge
#     follows LVA's peripheral API on the loopback, a profile pins the robot,
#     keeps it awake and sets its sad words, and the Wyoming backend is
#     rejected. Wi-Fi is off by default; on, the bridge gets the
#     stackchan-wifi credential, a state directory and a port open to the
#     LAN alone.
#   - The Redis index registry keeps today's indexes and rejects a clash, and
#     Nextcloud's database is the workload instance's "nextcloud" over its
#     socket, as database.createLocally made it.
#
# Split into parts, one check each (service-settings, -2, -3): every variant is
# a whole profile evaluation, and the whole list in one process grew past the
# CI runner's memory (19.7 GB measured, 2026-10-06; CI builds each check in a
# process of its own). Part n takes every parts-th case from the n-th; the
# list is lazy, so a part evaluates only the variants its own cases use.
{
  lib,
  pkgs,
  inputs,
  self,
  agenix,
  disko,
  deploy-rs,
  nixpkgs,
  nixos-raspberrypi,
  part ? 1,
  parts ? 1,
}:

let
  inputsWithSelf = inputs // {
    self = self // {
      lanbatPlugins = import ../plugins;
    };
  };

  exampleDeploy = import ../deployments/example/deploy.nix { inputs = inputsWithSelf; };

  lanbatLib = import ../lib {
    self = inputsWithSelf.self;
    inputs = inputsWithSelf;
    profiles = { };
    inherit
      nixpkgs
      nixos-raspberrypi
      agenix
      disko
      deploy-rs
      ;
  };

  # The example server's configuration, with extra modules merged last.
  serverWith =
    modules:
    let
      deploy = exampleDeploy // {
        hosts = exampleDeploy.hosts // {
          server = exampleDeploy.hosts.server // {
            modules = (exampleDeploy.hosts.server.modules or [ ]) ++ modules;
          };
        };
      };
    in
    (lanbatLib.mkProfile "example" deploy).configurations.example-server.config;

  # Merge modules into named hosts (voice-satellite backend must match on every host).
  hostsWithModules =
    hostModules:
    exampleDeploy
    // {
      hosts = lib.mapAttrs (
        hostName: host:
        host
        // lib.optionalAttrs (hostModules ? ${hostName}) {
          modules = (host.modules or [ ]) ++ hostModules.${hostName};
        }
      ) exampleDeploy.hosts;
    };

  failedAssertions = config: map (a: a.message) (lib.filter (a: !a.assertion) config.assertions);

  base = serverWith [ ];

  # ── Person mapper ─────────────────────────────────────────────────────────
  # It is the only always-on service that names people, so its whole wiring
  # (account, secret, broker user, unit) follows settings.cameras being
  # non-empty while Frigate, the broker and the router are all present. The
  # router is present only when haLlm points at it, so the variant enables that
  # too; the example server loads every other service.
  personMapperOn = serverWith [
    {
      lanbat.deployment.haLlm = lib.mkForce {
        baseUrl = "http://127.0.0.1:8092/v1";
        model = "assistant";
      };
      lanbat.services.person-mapper.settings.cameras = {
        "Living Room" = "front";
      };
      lanbat.services.frigate.settings.faceRecognition.enable = true;
    }
  ];
  personMapperUnit = config: config.systemd.services.person-mapper;
  mosquittoUsers = config: lib.attrNames (lib.head config.services.mosquitto.listeners).users;

  # ── Jackett ──────────────────────────────────────────────────────────────
  jackett = base.lanbat.services.jackett;
  jackettService = base.services.jackett;
  jackettUnit = base.systemd.services.jackett;
  jackettPluginUnit = base.systemd.services.jackett-qbittorrent-plugin;
  jackettDefinitionsUnit = base.systemd.services.jackett-definitions;
  jackettDefinitionsTimer = base.systemd.timers.jackett-definitions;
  qbittorrentUnit = base.systemd.services.podman-qbittorrent;

  # ── Samba ────────────────────────────────────────────────────────────────
  smb = config: config.services.samba.settings;
  sambaDrives = config: config.lanbat.services.samba.nfs.drives;

  sambaChanged = serverWith [
    {
      lanbat.services.samba.settings = {
        workgroup = "LAB";
        shares = {
          private.enable = false;
          # Only the mode: the default owner and group stay.
          shared.createDirectory.mode = "0770";
          media-b.vetoFiles = [
            "adult"
            "tmp"
          ];
          scans = {
            drive = "a";
            path = "scans";
            readOnly = false;
            validUsers = [ "@media" ];
            extraConfig."hide dot files" = "yes";
          };
        };
      };
    }
  ];

  sambaDriveA = serverWith [
    {
      lanbat.services.samba.settings = {
        homes = false;
        shares = {
          media-b.enable = false;
          private.enable = false;
          shared.enable = false;
        };
      };
    }
  ];

  sambaTypo = serverWith [ { lanbat.services.samba.settings.share = { }; } ];

  # ── Wyoming ──────────────────────────────────────────────────────────────
  wyomingChanged = serverWith [
    {
      lanbat.services.wyoming.settings = {
        wakeWord.threshold = 0.5;
        speechToText = {
          model = "small-int8";
          language = "de";
        };
        textToSpeech.voice = "de_DE-thorsten-medium";
        satellite = {
          name = "Office Satellite";
          speaker = "plughw:CARD=Generic,DEV=0";
          mixer = [ ];
          microphoneUsbId = "046d:0825";
        };
      };
    }
  ];

  wyomingBadVoice = serverWith [ { lanbat.services.wyoming.settings.textToSpeech.voice = "alan"; } ];

  lvaServer =
    (lanbatLib.mkProfile "example" (hostsWithModules {
      server = [
        {
          lanbat.voiceSatellite = {
            backend = "lva";
            lva.continueConversationDelay = 0.8;
          };
        }
      ];
      pi-storage = [
        { lanbat.voiceSatellite.backend = "lva"; }
      ];
      pi-voice = [
        { lanbat.voiceSatellite.backend = "lva"; }
      ];
    })).configurations.example-server.config;

  # The example server's LVA satellite with these settings on top.
  lvaServerWith =
    settings:
    (lanbatLib.mkProfile "example" (hostsWithModules {
      server = [
        {
          lanbat.voiceSatellite = {
            backend = "lva";
          }
          // settings;
        }
      ];
      pi-storage = [ { lanbat.voiceSatellite.backend = "lva"; } ];
      pi-voice = [ { lanbat.voiceSatellite.backend = "lva"; } ];
    })).configurations.example-server.config;
  lvaTwoWords = lvaServerWith {
    lva.wakeModels = [
      "okay_nabu"
      "hey_nabu"
    ];
  };
  lvaOldOption = lvaServerWith { lva.wakeModel = "hey_jarvis"; };
  lvaWakeThreshold = lvaServerWith { lva.wakeThreshold = 0.95; };
  lvaThreeWords = lvaServerWith {
    lva.wakeModels = [
      "okay_nabu"
      "hey_jarvis"
      "alexa"
    ];
  };
  # The example Pi 3 on LVA, with echo cancellation and the music sent
  # through it.
  lvaPi3Aec =
    (lanbatLib.mkProfile "example" (hostsWithModules {
      server = [ { lanbat.voiceSatellite.backend = "lva"; } ];
      pi-storage = [ { lanbat.voiceSatellite.backend = "lva"; } ];
      pi-voice = [
        {
          lanbat.voiceSatellite = {
            backend = "lva";
            echoCancellation = {
              enable = true;
              includeMusic = true;
            };
          };
        }
      ];
    })).configurations.example-pi-voice.config;
  # The example TV box (pi-storage, lanbat-tv plugin) with an LVA satellite.
  lvaTvBox =
    (lanbatLib.mkProfile "example" (hostsWithModules {
      server = [ { lanbat.voiceSatellite.backend = "lva"; } ];
      pi-storage = [
        {
          lanbat.voiceSatellite.backend = "lva";
          lanbat.voiceSatellite.lva.volume = 0.4;
        }
      ];
      pi-voice = [ { lanbat.voiceSatellite.backend = "lva"; } ];
    })).configurations.example-pi-storage.config;
  # The example Pi 3 with a Stack-chan (lanbat-stackchan plugin), on the given
  # satellite backend and with these modules on top.
  pi3Stackchan =
    backend: modules:
    let
      deploy = hostsWithModules {
        server = [ { lanbat.voiceSatellite.backend = backend; } ];
        pi-storage = [ { lanbat.voiceSatellite.backend = backend; } ];
        pi-voice = [ { lanbat.voiceSatellite.backend = backend; } ] ++ modules;
      };
    in
    (lanbatLib.mkProfile "example" (
      deploy
      // {
        hosts = deploy.hosts // {
          pi-voice = deploy.hosts.pi-voice // {
            plugins = deploy.hosts.pi-voice.plugins ++ [ inputsWithSelf.self.lanbatPlugins.stackchan ];
          };
        };
      }
    )).configurations.example-pi-voice.config;
  stackchan = pi3Stackchan "lva" [ ];
  stackchanTuned = pi3Stackchan "lva" [
    {
      lanbat.stackchan = {
        usbSerial = "F4:12:FA:00:00:01";
        nightHours.start = null;
        napAfter = null;
        sadWords = [
          "sorry"
          "leider"
        ];
        soundDirection = false;
        soundOffsetDeg = "-15";
      };
    }
  ];
  stackchanWyoming = pi3Stackchan "wyoming" [ ];
  stackchanWifi = pi3Stackchan "lva" [ { lanbat.stackchan.wifi.enable = true; } ];
  # The Stack-chan on a Pi 3 in the Kitchen, with the assistant router on the
  # server: the robot is the router's body there.
  bodyProfile =
    let
      deploy = hostsWithModules {
        server = [
          {
            lanbat.voiceSatellite.backend = "lva";
            lanbat.services.assistant-router.settings.body.persona = "You are Robo.";
          }
        ];
        pi-storage = [ { lanbat.voiceSatellite.backend = "lva"; } ];
        pi-voice = [ { lanbat.voiceSatellite.backend = "lva"; } ];
      };
    in
    lanbatLib.mkProfile "example" (
      deploy
      // {
        deployment = deploy.deployment // {
          voiceRooms = deploy.deployment.voiceRooms // {
            Kitchen = "pi-voice";
          };
          haLlm = {
            baseUrl = "http://127.0.0.1:8092/v1";
            model = "assistant";
          };
        };
        hosts = deploy.hosts // {
          pi-voice = deploy.hosts.pi-voice // {
            plugins = deploy.hosts.pi-voice.plugins ++ [ inputsWithSelf.self.lanbatPlugins.stackchan ];
          };
        };
      }
    );
  bodyServer = bodyProfile.configurations.example-server.config;
  bodyPi = bodyProfile.configurations.example-pi-voice.config;
  # Phones in the Kitchen (lanbat-room-presence on the Pi 3), Alex with the
  # Home Assistant app's beacon, iPhone keys when `irks` is set.
  presenceProfile =
    irks:
    let
      deploy = hostsWithModules {
        server = [ { lanbat.voiceSatellite.backend = "lva"; } ];
        pi-storage = [ { lanbat.voiceSatellite.backend = "lva"; } ];
        pi-voice = [
          {
            lanbat.voiceSatellite.backend = "lva";
            lanbat.roomPresence.irks = irks;
            # It serves Wi-Fi too, so it reads the access point's clients.
            lanbat.network.bridge.enable = true;
            lanbat.wifi.accessPoint = {
              enable = true;
              interface = "wlan1";
              ssid = "Example";
              countryCode = "GB";
              security = "wpa2-wpa3";
            };
          }
        ];
      };
    in
    lanbatLib.mkProfile "example" (
      deploy
      // {
        deployment = deploy.deployment // {
          voiceRooms = deploy.deployment.voiceRooms // {
            Kitchen = "pi-voice";
          };
          haLlm = {
            baseUrl = "http://127.0.0.1:8092/v1";
            model = "assistant";
          };
          people = deploy.deployment.people // {
            alex = {
              name = "Alex";
              bleBeacon = "A1B2C3D4-E5F6-0718-293A-4B5C6D7E8F90";
              wifiMacs = [ "3A-1F-00-AA-BB-01" ];
            };
          };
        };
        hosts = deploy.hosts // {
          pi-voice = deploy.hosts.pi-voice // {
            plugins = deploy.hosts.pi-voice.plugins ++ [
              inputsWithSelf.self.lanbatPlugins.room-presence
              inputsWithSelf.self.lanbatPlugins.stackchan
            ];
          };
        };
      }
    );
  presence = presenceProfile false;
  presencePi = presence.configurations.example-pi-voice.config;
  presenceServer = presence.configurations.example-server.config;
  presenceIrksPi = (presenceProfile true).configurations.example-pi-voice.config;
  # A server whose Home Assistant agent talks to the assistant router.
  # It also extends the home Wi-Fi from a dongle, on a bridge over its port
  # (and a second port, for a host plugged in behind it).
  routerServer = serverWith [
    {
      lanbat.network.bridge = {
        enable = true;
        macAddress = "6c:2b:59:00:00:01";
        extraPorts = [ "enp2s0" ];
      };
      lanbat.wifi.accessPoint = {
        enable = true;
        interface = "wlp0s20f0u1";
        ssid = "Example";
        countryCode = "GB";
      };
      lanbat.deployment.haLlm = lib.mkForce {
        baseUrl = "http://127.0.0.1:8092/v1";
        model = "assistant";
      };
      lanbat.services.llama-cpp.settings.model = "qwen3-4b";
      lanbat.services.llm-gateway.settings.models.smart = [
        {
          model = "gemini/gemini-3.5-flash";
          params.reasoning_effort = "none";
        }
        { model = "gemini/gemini-3.1-flash-lite"; }
      ];
    }
  ];
  # The same, without phones tapped on robots.
  routerNoTap = serverWith [
    {
      lanbat.deployment.haLlm = lib.mkForce {
        baseUrl = "http://127.0.0.1:8092/v1";
        model = "assistant";
      };
      lanbat.services.llama-cpp.settings.model = "qwen3-4b";
      lanbat.services.assistant-router.settings.tap = false;
      # An Echo Show on TECHO5 in the Kitchen (services/techo5-bridge.nix): here
      # rather than in a variant of its own, which would cost the check another
      # whole profile evaluation.
      lanbat.deployment.techo5Devices.kitchen = {
        address = "192.0.2.60";
        room = "Kitchen";
        playerName = "Kitchen Show";
        profiles.alex = { };
        profiles.sam.todo = "todo.sam_chores";
        household.calendars = [ "calendar.family" ];
      };
      # Someone with no profile on the screen: the default lists all the same.
      lanbat.deployment.people.jo.name = "Jo";
    }
  ];

  techo5BadTodo = serverWith [
    {
      lanbat.deployment.techo5Devices.kitchen = {
        address = "192.0.2.60";
        room = "Kitchen";
        playerName = "Kitchen Show";
        profiles.alex.todo = "sensor.x";
      };
    }
  ];

  # The router configured on a server that lists its services without it.
  routerUnlisted =
    (lanbatLib.mkProfile "example" (
      exampleDeploy
      // {
        hosts = exampleDeploy.hosts // {
          server = exampleDeploy.hosts.server // {
            services = [
              "home-assistant"
              "llama-cpp"
              "postgresql"
              "wyoming"
            ];
            modules = (exampleDeploy.hosts.server.modules or [ ]) ++ [
              {
                lanbat.deployment.haLlm = lib.mkForce {
                  baseUrl = "http://127.0.0.1:8092/v1";
                  model = "assistant";
                };
              }
            ];
          };
        };
      }
    )).configurations.example-server.config;
  # The TV box with its shows in another directory.
  tvShowsElsewhere =
    (lanbatLib.mkProfile "example" (hostsWithModules {
      pi-storage = [
        { lanbat.services.kodi.settings.videoSources.tv.path = "/mnt/storage-a/media/tv/shows/"; }
      ];
    })).configurations.example-pi-storage.config;
  # The example Android TV box with apps to open by voice.
  tvApps = serverWith [
    {
      androidDevices.bedroom = {
        room = "Living Room";
        apps = {
          Netflix = "com.netflix.ninja";
          YouTube = "com.google.android.youtube.tv";
          "YouTube Music" = "com.google.android.youtube.tvmusic";
        };
      };
    }
  ];
  automationById = config: id: lib.findFirst (a: a.id == id) null (haConfig config).automation;
  lvaExec = config: config.systemd.services.linux-voice-assistant.serviceConfig.ExecStart;
  lvaPre = config: config.systemd.services.linux-voice-assistant.serviceConfig.ExecStartPre;

  postSetup = config: config.systemd.services.home-assistant-post-setup.script;

  # ── Home Assistant ───────────────────────────────────────────────────────
  haConfig = config: config.services.home-assistant.config;
  # Dashboards are in storage mode, generated by post-setup.
  haYamlDashboard = config: config.services.home-assistant.lovelaceConfig != null;

  haMoved = serverWith [
    {
      lanbat.services = {
        home-assistant.port = lib.mkForce 18123;
        frigate.port = lib.mkForce 15000;
        music-assistant.port = lib.mkForce 18095;
        mosquitto.endpoint.port = lib.mkForce 11883;
      };
    }
  ];

  haNoZigbee = serverWith [
    { lanbat.services.home-assistant.settings.zigbee2mqttBridge = false; }
  ];

  # ── Jellyfin ─────────────────────────────────────────────────────────────
  jellyfinBootstrap = config: config.systemd.services.jellyfin-bootstrap.script;

  jellyfinImvdb = serverWith [ { lanbat.services.jellyfin.settings.imvdb = true; } ];

  # ── Audiobookshelf ───────────────────────────────────────────────────────
  absEnv = config: config.systemd.services.audiobookshelf-bootstrap.environment;

  absMoved = serverWith [
    {
      lanbat.services.audiobookshelf.settings = {
        drive = "a";
        libraryPath = "books/audio";
        metadataProvider = "audible.uk";
      };
    }
  ];

  # ── Redis indexes and Nextcloud's database ───────────────────────────────
  redisClash = serverWith [ { lanbat.redis.databases.other.index = 1; } ];

  envOf = config: container: config.virtualisation.oci-containers.containers.${container}.environment;
  imageOf = config: container: config.virtualisation.oci-containers.containers.${container}.image;
  optionsOf =
    config: container: config.virtualisation.oci-containers.containers.${container}.extraOptions;

  # ── Telegraf ─────────────────────────────────────────────────────────────
  telegrafConf = config: config.services.telegraf.extraConfig;
  healthChecks =
    config: map (c: "${c.name_override} ${lib.head c.urls}") (telegrafConf config).inputs.http_response;

  telegrafChanged = serverWith [
    {
      lanbat.services = {
        influxdb.endpoint.port = lib.mkForce 18086;
        grafana.port = lib.mkForce 13030;
        telegraf.settings.pingTargets = [ "192.0.2.1" ];
      };
      services.redis.servers.shared.port = lib.mkForce 16379;
    }
  ];

  # ── RomM ─────────────────────────────────────────────────────────────────
  rommVolumes = config: config.virtualisation.oci-containers.containers.romm.volumes;

  rommMoved = serverWith [
    {
      lanbat.services.romm.settings = {
        drive = "a";
        libraryPath = "games/roms";
        browserArcadePath = "games/arcade";
        authentikAdmin = "alice";
      };
    }
  ];

  # ── Music Assistant ──────────────────────────────────────────────────────
  maScript = config: config.systemd.services.music-assistant-setup.script;

  maMoved = serverWith [
    {
      lanbat.services = {
        music-assistant.port = lib.mkForce 18095;
        home-assistant = {
          port = lib.mkForce 18123;
          subdomain = lib.mkForce "hass";
        };
      };
    }
  ];

  maFanartVip = serverWith [
    { lanbat.services.music-assistant.settings.fanartTvVip = true; }
  ];

  # ── Snapcast ─────────────────────────────────────────────────────────────
  fwStart = config: config.networking.firewall.extraCommands;
  fwStop = config: config.networking.firewall.extraStopCommands;
  admits =
    address: config:
    lib.all
      (
        port: lib.hasInfix "INPUT -p tcp --dport ${toString port} -s ${address} -j ACCEPT" (fwStart config)
      )
      [
        1704
        1705
      ];
  snapClients = serverWith [
    { lanbat.services.snapcast.settings.clients.phone.host = "192.0.2.70"; }
  ];
  snapBadHost = serverWith [
    { lanbat.services.snapcast.settings.clients.phone.host = "phone.lan"; }
  ];
  snapDuplicate = serverWith [
    {
      lanbat.services.snapcast.settings.clients = {
        phone.host = "192.0.2.70";
        tablet.host = "192.0.2.70";
      };
    }
  ];
  snapMac = serverWith [
    { lanbat.services.snapcast.settings.clients.tv.mac = "2c:d8:ae:00:00:01"; }
  ];
  snapEmpty = serverWith [ { lanbat.services.snapcast.settings.clients.tv = { }; } ];
  snapBadMac = serverWith [ { lanbat.services.snapcast.settings.clients.tv.mac = "2c-d8-ae"; } ];
  # Music Assistant placed on a host without the snapserver.
  maApart = serverWith [ { lanbat.endpoints.snapcast.hosts = lib.mkForce [ "pi-storage" ]; } ];

  # ── Immich ───────────────────────────────────────────────────────────────
  immichMoved = serverWith [
    {
      lanbat.services.immich = {
        port = lib.mkForce 12283;
        settings = {
          drive = "b";
          uploadPath = "media/photos";
        };
      };
      services.redis.servers.shared.port = lib.mkForce 16379;
    }
  ];
  immichUpload =
    config: lib.head config.virtualisation.oci-containers.containers.immich-server.volumes;

  # ── Nextcloud ────────────────────────────────────────────────────────────
  nextcloudMoved = serverWith [
    {
      lanbat.services.nextcloud.settings.storage = {
        drive = "a";
        path = "cloud";
      };
    }
  ];
  nextcloudDirs =
    config: lib.filter (lib.hasInfix " nextcloud nextcloud ") config.systemd.tmpfiles.rules;

  expect = name: ok: if ok then null else name;

  cases = [
    (expect "samba: the default layout renders today's shares" (
      smb base == {
        global = (smb base).global;
        homes = {
          comment = "Home Directories";
          browseable = "no";
          "read only" = "no";
          "create mask" = "0700";
          "directory mask" = "0700";
          "valid users" = "%S";
          path = "/srv/storage/b/users/%S/files";
        };
        media = {
          comment = "Media: movies, TV, music videos";
          path = "/srv/storage/a/media";
          browseable = "yes";
          "read only" = "yes";
          "guest ok" = "no";
          "valid users" = "@media";
        };
        media-b = {
          comment = "Media: music, documentaries, books, ROMs";
          path = "/srv/storage/b/media";
          browseable = "yes";
          "read only" = "yes";
          "guest ok" = "no";
          "valid users" = "@media";
          "veto files" = "/adult/";
        };
        private = {
          comment = "Private";
          path = "/srv/storage/b/media/adult";
          browseable = "no";
          "read only" = "yes";
          "guest ok" = "no";
          "valid users" = "@private";
        };
        shared = {
          comment = "Shared";
          path = "/srv/storage/b/shared";
          browseable = "yes";
          "read only" = "no";
          "guest ok" = "no";
          "valid users" = "@media";
          "create mask" = "0664";
          "directory mask" = "0775";
          "force group" = "media";
        };
      }
      && (smb base).global.workgroup == "WORKGROUP"
      && (smb base).global."netbios name" == "server"
      &&
        sambaDrives base == [
          "a"
          "b"
        ]
      && lib.elem "d /srv/storage/b/shared 0775 root media -" base.systemd.tmpfiles.rules
    ))

    (expect "samba: a profile adds, changes and drops shares field by field" (
      let
        s = smb sambaChanged;
      in
      s.global.workgroup == "LAB"
      && !(s ? private)
      && s.media-b."veto files" == "/adult/tmp/"
      && s.media-b.comment == "Media: music, documentaries, books, ROMs"
      &&
        s.scans == {
          comment = "";
          path = "/srv/storage/a/scans";
          browseable = "yes";
          "read only" = "no";
          "guest ok" = "no";
          "valid users" = "@media";
          "hide dot files" = "yes";
        }
      && lib.elem "d /srv/storage/b/shared 0770 root media -" sambaChanged.systemd.tmpfiles.rules
    ))

    (expect "samba: smbd binds to the drives its shares use" (
      sambaDrives sambaDriveA == [ "a" ]
      && !((smb sambaDriveA) ? homes)
      &&
        lib.attrNames (smb sambaDriveA) == [
          "global"
          "media"
        ]
    ))

    (expect "samba: an unknown settings key is rejected" (
      lib.any (lib.hasInfix "samba has no setting \"share\"") (failedAssertions sambaTypo)
    ))

    (expect "wyoming: the defaults keep today's pipeline and satellite" (
      let
        w = base.services.wyoming;
        sat = base.lanbat.voiceSatellite;
      in
      w.openwakeword.threshold == toString 0.35
      && w.faster-whisper.servers.main.model == "base-int8"
      && w.faster-whisper.servers.main.language == "en"
      && w.piper.servers.main.voice == "en_GB-alan-medium"
      && sat.name == "Server Satellite"
      && sat.speaker == "plughw:CARD=PCH,DEV=0"
      && sat.mixer == [ "-c PCH sset Master 80% unmute" ]
      && sat.microphone.usbId == "1415:2000"
    ))

    (expect "wyoming: a profile's settings reach the servers, satellite and pipeline" (
      let
        w = wyomingChanged.services.wyoming;
        sat = wyomingChanged.lanbat.voiceSatellite;
      in
      w.openwakeword.threshold == toString 0.5
      && w.faster-whisper.servers.main.model == "small-int8"
      && w.piper.servers.main.voice == "de_DE-thorsten-medium"
      && sat.name == "Office Satellite"
      && sat.speaker == "plughw:CARD=Generic,DEV=0"
      && sat.mixer == [ ]
      && sat.microphone.usbId == "046d:0825"
      && lib.hasInfix ''PIPELINE_STT_LANGUAGE="de"'' (postSetup wyomingChanged)
      && lib.hasInfix ''PIPELINE_TTS_LANGUAGE="de_DE"'' (postSetup wyomingChanged)
    ))

    (expect "wyoming: a voice without a language prefix is rejected" (
      !(builtins.tryEval (
        builtins.deepSeq wyomingBadVoice.services.wyoming.piper.servers.main.voice true
      )).success
    ))

    (expect "lva: backend runs Linux Voice Assistant and registers ESPHome in post-setup" (
      let
        exec = lvaServer.systemd.services.linux-voice-assistant.serviceConfig.ExecStart or "";
        setup = postSetup lvaServer;
      in
      !(lvaServer.services.wyoming.satellite.enable or false)
      && lib.hasInfix "linux-voice-assistant" exec
      && lib.hasInfix "--wake-model" exec
      && lib.hasInfix "--wake-model okay_nabu" exec
      && lib.hasInfix "--stop-model" exec
      && lib.hasInfix "--continue-conversation-delay" exec
      && lib.hasInfix "0.8" exec
      && lib.hasInfix "VOICE_SATELLITE_REGISTRATIONS" setup
      && lib.hasInfix "|lva|Server Satellite" setup
      && lib.elem "esphome" lvaServer.services.home-assistant.extraComponents
    ))

    # LVA's audio library talks only to a PulseAudio server: a server without
    # one gets system-wide PipeWire, its mixer settings before LVA starts, and
    # the microphone's mono capture node.
    (expect "lva: a server gets system-wide PipeWire with a PulseAudio server" (
      lvaServer.services.pipewire.enable
      && lvaServer.services.pipewire.systemWide
      && lvaServer.services.pipewire.pulse.enable
    ))

    (expect "lva: the server's mixer settings run before LVA" (
      # systemd reads % as a specifier, so the command line carries %%.
      lib.any (lib.hasInfix "sset Master 80%% unmute") (
        lvaServer.systemd.services.linux-voice-assistant.serviceConfig.ExecStartPre or [ ]
      )
    ))

    (expect "lva: the microphone gets its mono capture node" (
      lvaServer.services.pipewire.wireplumber.extraConfig ? "52-lva-microphone"
    ))

    (expect "lva: the server passes its assertions" (failedAssertions lvaServer == [ ]))

    # LVA satellites fetch replies from internal_url, so it must be reachable
    # from other hosts: the HTTPS address through Caddy, and the player gets
    # the profile's CA to verify it.
    (expect "lva: internal_url is the HTTPS address, and LVA verifies it" (
      (haConfig lvaServer).homeassistant.internal_url == "https://ha.home.example.com"
      && lvaServer.systemd.services.linux-voice-assistant.environment ? LVA_TLS_CA_FILE
    ))

    # "play ..." on the room's speaker, and each satellite's room and host name
    # for post-setup's area step (the example puts the server in the Office).
    (expect "lva: voice play and stop automations, and rooms in the registrations" (
      lib.all (id: lib.elem id (map (a: a.id) (haConfig lvaServer).automation)) [
        "lanbat_voice_play"
        "lanbat_voice_stop"
      ]
      && lib.hasInfix "|lva|Server Satellite|Office|" (postSetup lvaServer)
    ))

    (expect "lva: the music ducks to near silence while listening, a quarter while answering" (
      lvaPi3Aec.systemd.services.lva-snapcast-duck.environment.DUCK_LISTEN_VOLUME == "0.05"
      # A ducker restarted with the music down finds the volumes to put back.
      && lvaPi3Aec.systemd.services.lva-snapcast-duck.environment ? STATE_FILE
      && lvaPi3Aec.systemd.services.lva-snapcast-duck.serviceConfig.RuntimeDirectoryPreserve
      # And WirePlumber doesn't give a new music stream the ducked volume.
      && lvaPi3Aec.services.pipewire.wireplumber.extraConfig ? "51-snapclient-volume"
      && lvaPi3Aec.systemd.services.lva-snapcast-duck.environment.DUCK_VOLUME == "0.25"
    ))

    # One case for both (the parts take cases by position: a new case would
    # move every later one to another part, and with it the variants it needs).
    (expect
      "lva: replies speak up in a loud room, measured on the echo-cancelled microphone; a microphone whose reads keep failing is reopened"
      (
        let
          env = lvaPi3Aec.systemd.services.lva-snapcast-duck.environment;
          watchdog = lvaPi3Aec.systemd.services.lanbat-mic-watchdog;
        in
        env.ADAPT_MIC_NODE == "lanbat_aec_mic"
        && env.ADAPT_QUIET_DB == "-55"
        && env.ADAPT_MAX_BOOST_DB == "8"
        && env.LVA_PREFS == "/var/lib/linux-voice-assistant/prefs.json"
        && lib.hasSuffix "/bin/pw-record" env.PW_RECORD
        && watchdog.environment.MIC_USB_ID == lvaPi3Aec.lanbat.voiceSatellite.microphone.usbId
        && lib.hasSuffix "/bin/wpctl" watchdog.environment.WPCTL
        && lib.hasSuffix "/bin/journalctl" watchdog.environment.JOURNALCTL
        && lib.hasSuffix "/bin/mic-watchdog" watchdog.serviceConfig.ExecStart
        && watchdog.environment.MIC_NODE == "lanbat_ps_eye_capture"
        && lib.hasSuffix "/bin/pw-record" watchdog.environment.PW_RECORD
        && lib.elem "multi-user.target" watchdog.wantedBy
      )
    )

    (expect "stackchan: the bridge follows LVA's peripheral API on the loopback" (
      let
        unit = stackchan.systemd.services.lva-stackchan;
      in
      lib.elem "lva-stackchan" stackchan.lanbat.voiceSatellite.lva.peripheralApiUsers
      &&
        unit.environment.LVA_PERIPHERAL_URL
        == "ws://127.0.0.1:${toString stackchan.lanbat.voiceSatellite.lva.peripheralPort}"
      && lib.elem "linux-voice-assistant.service" unit.partOf
      && lib.elem "dialout" unit.serviceConfig.SupplementaryGroups
      && lib.elem "pipewire" unit.serviceConfig.SupplementaryGroups
      && unit.environment.NIGHT_START == "23:00"
      && unit.environment.NAP_AFTER_S == "900"
      && unit.environment.AUTO_BRIGHTNESS == "1"
      && unit.environment.SOUND_DIRECTION == "1"
      && unit.environment.SOUND_OFFSET_DEG == "0"
      && unit.environment.SOUND_TURN_MIN_DEG == "15"
      && unit.environment.SOUND_MIC_GEOMETRY == "-0.03,0;-0.01,0;0.01,0;0.03,0"
      && unit.environment.SOUND_SOURCE == "lanbat_ps_eye_capture"
      && !(unit.environment ? SAD_WORDS)
      && !(unit.environment ? WIFI_HOST)
      && !(unit.serviceConfig ? LoadCredential)
      && !(stackchan.lanbat.secrets ? stackchan-wifi)
      && !(lib.hasInfix "8771" stackchan.networking.firewall.extraCommands)
      && lib.hasInfix ''SYMLINK+="stackchan"'' stackchan.services.udev.extraRules
      && !(lib.hasInfix "ID_SERIAL_SHORT" stackchan.services.udev.extraRules)
      && failedAssertions stackchan == [ ]
    ))

    (expect
      "stackchan: a profile pins the robot, keeps it awake, gives it its own sad words and aims its sound"
      (
        let
          env = stackchanTuned.systemd.services.lva-stackchan.environment;
        in
        lib.hasInfix ''ENV{ID_SERIAL_SHORT}=="F4:12:FA:00:00:01"'' stackchanTuned.services.udev.extraRules
        && env.NIGHT_START == ""
        && env.SAD_WORDS == "sorry,leider"
        && env.NAP_AFTER_S == "0"
        && env.SOUND_DIRECTION == "0"
        && env.SOUND_OFFSET_DEG == "-15"
      )
    )

    (expect
      "stackchan: on Wi-Fi the robot dials the Pi on a port open to the LAN, with details from a credential"
      (
        let
          unit = stackchanWifi.systemd.services.lva-stackchan;
          subnet = stackchanWifi.lanbat.deployment.lanSubnet;
        in
        unit.environment.WIFI_HOST == stackchanWifi.lanbat.hosts.pi-voice.networking.ip
        && unit.environment.WIFI_PORT == "8771"
        && unit.environment.SOUND_OVER_WIFI == "0" # elsewhere in the room by default
        && unit.environment.ROUTER_PHONES == "0" # no router here to hand on keys from
        &&
          unit.serviceConfig.LoadCredential == [
            "stackchan-wifi:${stackchanWifi.lanbat.secrets.stackchan-wifi.path}"
          ]
        && unit.serviceConfig.StateDirectory == "lva-stackchan"
        && lib.elem subnet unit.serviceConfig.IPAddressAllow
        && lib.elem 8771 stackchanWifi.lanbat.services.stackchan.extraPorts
        && lib.hasInfix "iptables -I INPUT -p tcp --dport 8771 -s ${subnet} -j ACCEPT" stackchanWifi.networking.firewall.extraCommands
        && lib.hasInfix "iptables -D INPUT -p tcp --dport 8771 -s ${subnet} -j ACCEPT" stackchanWifi.networking.firewall.extraStopCommands
        && failedAssertions stackchanWifi == [ ]
      )
    )

    (expect "stackchan: the robot is the router's body in its room, over a port opened to it alone" (
      let
        env = bodyPi.systemd.services.lva-stackchan.environment;
        router = bodyServer.systemd.services.assistant-router.serviceConfig;
        piIp = bodyServer.lanbat.hosts.pi-voice.networking.ip;
        serverIp = bodyServer.lanbat.hosts.server.networking.ip;
      in
      env.ROOM == "Kitchen"
      && env.ROUTER_BODY_URL == "ws://${serverIp}:8770/v1/body"
      && env.ROUTER_PHONES == "1" # the paired phones' keys, from the router
      && lib.elem serverIp bodyPi.systemd.services.lva-stackchan.serviceConfig.IPAddressAllow
      && lib.hasInfix "--body-port 8770" router.ExecStart
      && lib.elem piIp router.IPAddressAllow
      && lib.elem 8770 bodyServer.networking.firewall.allowedTCPPorts
      && lib.any (r: lib.hasInfix "--dport 8770 -s ${piIp} -j ACCEPT" r) (
        lib.splitString "\n" bodyServer.networking.firewall.extraCommands
      )
      && failedAssertions bodyServer == [ ]
      && failedAssertions bodyPi == [ ]
    ))

    (expect "stackchan: where its Pi scans the room for phones, the robot leaves them to it" (
      let
        unit = presencePi.systemd.services.lva-stackchan;
        irksUnit = presenceIrksPi.systemd.services.lva-stackchan;
      in
      # room-presence runs on this Pi: no beacons, no keys, no secret for the robot
      # (its RAM can't hold Bluetooth beside its camera and Wi-Fi).
      !(unit.environment ? BEACONS)
      && unit.environment.ROUTER_PHONES == "0"
      && !(unit.serviceConfig ? LoadCredential)
      && !(irksUnit.serviceConfig ? LoadCredential)
      && presencePi.lanbat.services.stackchan.readsSecrets == [ ]
      && presenceIrksPi.lanbat.services.stackchan.readsSecrets == [ ]
      && failedAssertions presencePi == [ ]
      && failedAssertions presenceIrksPi == [ ]
    ))

    (expect
      "assistant-router: publishes the robots and who is around to Home Assistant, with its own broker login"
      (
        let
          unit = routerServer.systemd.services.assistant-router.serviceConfig;
          broker = (lib.head routerServer.services.mosquitto.listeners).users.assistant-router;
        in
        lib.hasInfix "--mqtt-host 127.0.0.1" unit.ExecStart
        && lib.hasInfix "--mqtt-password-file %d/mosquitto-assistant-router-pass" unit.ExecStart
        && lib.elem "mosquitto-assistant-router-pass:${routerServer.lanbat.secrets.mosquitto-assistant-router-pass.path}" unit.LoadCredential
        && lib.elem "mosquitto-assistant-router-pass" routerServer.lanbat.services.assistant-router.readsSecrets
        && lib.elem "readwrite lanbat/#" broker.acl
        && !(lib.elem "readwrite #" broker.acl)
        && failedAssertions routerServer == [ ]
      )
    )

    (expect "wifi-presence: beside the router, it listens for the people's phones on the LAN" (
      let
        unit = presenceServer.systemd.services.wifi-presence;
      in
      builtins.fromJSON unit.environment.PHONES == {
        alex = [ "3A-1F-00-AA-BB-01" ];
      }
      && unit.environment.INTERFACE == presenceServer.lanbat.hosts.server.networking.interface
      && unit.environment.SUBNET == presenceServer.lanbat.deployment.lanSubnet
      && unit.environment.ROUTER_BODY_URL == "ws://127.0.0.1:8770/v1/body"
      && unit.serviceConfig.AmbientCapabilities == [ "CAP_NET_RAW" ]
      && unit.serviceConfig.IPAddressAllow == [ "localhost" ]
      && lib.hasInfix "--cards-state /var/lib/assistant-router/cards.json" presenceServer.systemd.services.assistant-router.serviceConfig.ExecStart
      # Without the router, or without anyone's wifiMacs or taps, it stays off.
      && !(base.systemd.services ? wifi-presence)
      && !(routerNoTap.systemd.services ? wifi-presence)
      && failedAssertions presenceServer == [ ]
    ))

    (expect
      "who: a phone held to a robot opens a page open to guests, accounts sign in, its phone is learned"
      (
        let
          exec = routerServer.systemd.services.assistant-router.serviceConfig.ExecStart;
          domain = routerServer.lanbat.deployment.domain;
          vhost = routerServer.services.caddy.virtualHosts."who.${domain}".extraConfig;
        in
        routerServer.lanbat.services.who.subdomain == "who"
        && routerServer.lanbat.services.who.port == 8772
        && routerServer.lanbat.services.who.auth == "forward-auth"
        && routerServer.services.caddy.virtualHosts ? "who.${domain}"
        # Open to anyone; only /signin is behind the login, where the
        # provider's headers are read (the page's own cookie elsewhere).
        && lib.hasInfix "@auth_bypass path / /tap/* /name /forget-phone /forget-me" vhost
        && !(lib.hasInfix "/signin" vhost)
        && lib.hasInfix "--tap-url https://who.${domain}" exec
        && lib.hasInfix "--tap-user-header X-Authentik-Username" exec
        && lib.hasInfix "--tap-name-header X-Authentik-Name" exec
        && lib.hasInfix "--tap-session-key /var/lib/assistant-router/who-session.key" exec
        && lib.hasInfix "--tap-account-url https://auth.${domain}/if/user/#/settings" exec
        # The admins' page: Authentik's groups, the repo's admin group by default.
        && lib.hasInfix "--tap-groups-header X-Authentik-Groups" exec
        && lib.hasInfix "--tap-admin-group 'authentik Admins'" exec
        && !(lib.hasInfix "/admin" vhost)
        && lib.hasInfix "--phones-state /var/lib/assistant-router/phones.json" exec
        # wifi-presence runs for the learned phones, with none in the profile.
        && builtins.fromJSON routerServer.systemd.services.wifi-presence.environment.PHONES == { }
        # On the bridge: the address, the gateway and the LAN listeners move
        # to it, the ports join it, and the access point serves the home
        # network from the dongle into it (docs/wifi.md).
        && routerServer.systemd.services.wifi-presence.environment.INTERFACE == "br0"
        && routerServer.networking.interfaces ? br0
        && !(routerServer.networking.interfaces ? eno1)
        && routerServer.networking.defaultGateway.interface == "br0"
        && routerServer.systemd.network.netdevs."20-br0".netdevConfig.Kind == "bridge"
        && routerServer.systemd.network.netdevs."20-br0".netdevConfig.MACAddress == "6c:2b:59:00:00:01"
        && routerServer.systemd.network.networks."30-eno1".networkConfig.Bridge == "br0"
        && routerServer.systemd.network.networks."30-enp2s0".networkConfig.Bridge == "br0"
        && routerServer.systemd.network.networks."30-wlp0s20f0u1".linkConfig.Unmanaged
        && (
          let
            ap = routerServer.services.hostapd.radios.wlp0s20f0u1;
            net = ap.networks.wlp0s20f0u1;
          in
          routerServer.services.hostapd.enable
          && ap.channel == 6
          && ap.countryCode == "GB"
          && net.ssid == "Example"
          && net.settings.bridge == "br0"
          && net.authentication.wpaPasswordFile == routerServer.lanbat.secrets.wifi-access-point.path
          # The default stays plain WPA2.
          && net.authentication.mode == "wpa2-sha1"
          && net.authentication.saePasswords == [ ]
        )
        && routerNoTap.networking.interfaces ? eno1
        && !routerNoTap.services.hostapd.enable
        && routerNoTap.lanbat.services.who.subdomain == null
        && !(routerNoTap.services.caddy.virtualHosts ? "who.${domain}")
        && !(lib.hasInfix "--tap-url" routerNoTap.systemd.services.assistant-router.serviceConfig.ExecStart)
        && failedAssertions routerServer == [ ]
        && failedAssertions routerNoTap == [ ]
      )
    )

    (expect
      "assistant-router: sightings in its state, their pictures on the workload layer, for a dynamic user"
      (
        let
          unit = routerServer.systemd.services.assistant-router.serviceConfig;
          group = "assistant-router-sightings";
        in
        lib.hasInfix "--sightings-db /var/lib/assistant-router/sightings.db" unit.ExecStart
        && lib.hasInfix "--sightings-pictures /mnt/workload/assistant-router" unit.ExecStart
        && lib.hasInfix "--sightings-days 30" unit.ExecStart
        && lib.hasInfix "--sightings-picture-days 7" unit.ExecStart
        # A static group owns the directory, made when the layer is unlocked; the
        # dynamic user joins it, and may write there once it exists.
        &&
          routerServer.lanbat.services.assistant-router.workloadDirs."assistant-router" == {
            user = "root";
            inherit group;
            mode = "2770";
          }
        && routerServer.users.groups ? ${group}
        && unit.SupplementaryGroups == [ group ]
        && lib.elem "-/mnt/workload/assistant-router" unit.ReadWritePaths
        && routerServer.systemd.services.assistant-router-unlocked.wantedBy == [ "workload-online.target" ]
        && failedAssertions routerServer == [ ]
      )
    )

    # Telegraf 1.38 won't start with an empty input list ("unsupported config
    # format: prometheus"): a Pi without CoreDNS has no prometheus input at all.
    (expect "telegraf: CoreDNS metrics only where CoreDNS runs" (
      !bodyPi.services.coredns.enable
      && !(bodyPi.services.telegraf.extraConfig.inputs ? prometheus)
      && bodyServer.lanbat.hostKey == "server"
    ))

    (expect "stackchan: the router's persona is the profile's" (
      bodyServer.lanbat.services.assistant-router.settings.body.persona == "You are Robo."
      && lib.hasInfix "assistant-router-people.json" bodyServer.systemd.services.assistant-router.serviceConfig.ExecStart
      && bodyServer.lanbat.deployment.people.alex.name == "Alex"
      && lib.hasInfix "assistant-router-persona.txt" bodyServer.systemd.services.assistant-router.serviceConfig.ExecStart
    ))

    (expect "stackchan: without a room or a router, no body connection" (
      !(stackchan.systemd.services.lva-stackchan.environment ? ROUTER_BODY_URL)
      && stackchan.lanbat.services.stackchan.consumes == [ ]
    ))

    (expect "room-presence: the Pi scans for the people's phones and reports them to the router alone" (
      let
        unit = presencePi.systemd.services.room-presence;
        env = unit.environment;
        piIp = presenceServer.lanbat.hosts.pi-voice.networking.ip;
        serverIp = presenceServer.lanbat.hosts.server.networking.ip;
      in
      env.ROOM == "Kitchen"
      && env.ROUTER_BODY_URL == "ws://${serverIp}:8770/v1/body"
      && env.PEOPLE == "alex,sam"
      && env.BEACONS == "alex=A1B2C3D4-E5F6-0718-293A-4B5C6D7E8F90"
      && !(env ? IRK_FILE)
      # Serving Wi-Fi: the access point's clients, the profile's phones, iw
      # and its netlink socket.
      && env.AP_INTERFACE == "wlan1"
      # Offered as the router offers it: WPA2 and WPA3 together, on one password.
      && (
        let
          net = presencePi.services.hostapd.radios.wlan1.networks.wlan1;
        in
        net.authentication.mode == "wpa3-sae-transition"
        && net.settings.wpa_key_mgmt == "WPA-PSK SAE"
        && net.authentication.wpaPasswordFile == presencePi.lanbat.secrets.wifi-access-point.path
        &&
          map (e: e.passwordFile) net.authentication.saePasswords
          == [ presencePi.lanbat.secrets.wifi-access-point.path ]
      )
      && builtins.fromJSON env.WIFI_PHONES == { alex = [ "3A-1F-00-AA-BB-01" ]; }
      && lib.any (p: lib.hasInfix "-iw-" "${p}") unit.path
      && lib.elem "AF_NETLINK" unit.serviceConfig.RestrictAddressFamilies
      && lib.elem serverIp unit.serviceConfig.IPAddressAllow
      && presencePi.lanbat.services.room-presence.consumes == [ "assistant-router" ]
      && lib.any (r: lib.hasInfix "--dport 8770 -s ${piIp} -j ACCEPT" r) (
        lib.splitString "\n" presenceServer.networking.firewall.extraCommands
      )
      && failedAssertions presencePi == [ ]
    ))

    (expect "room-presence: iPhone keys only when asked for" (
      presenceIrksPi.systemd.services.room-presence.environment.IRK_FILE
      == presenceIrksPi.lanbat.secrets.room-presence-irks.path
      && !(presencePi.lanbat.secrets ? room-presence-irks)
    ))

    (expect "stackchan: needs the LVA backend" (
      lib.any (lib.hasInfix "lanbat.stackchan follows Linux Voice Assistant") (
        failedAssertions stackchanWyoming
      )
    ))

    # A WirePlumber or PipeWire restart recreates the microphone's node; LVA
    # must restart with them or it stays deaf.
    (expect "lva: restarts with the audio stack" (
      lib.all (u: lib.elem u lvaPi3Aec.systemd.services.linux-voice-assistant.partOf) [
        "pipewire.service"
        "wireplumber.service"
      ]
    ))

    (expect "lva: includeMusic plays Snapcast into the echo-cancel sink" (
      lib.hasInfix "--soundcard lanbat_aec_playback" lvaPi3Aec.systemd.services.snapclient.serviceConfig.ExecStart
      && !(lib.hasInfix "--soundcard" lvaServer.systemd.services.snapclient.serviceConfig.ExecStart or "")
    ))

    (expect "lva: replies play into the echo-cancel sink through mpv's pulse driver" (
      lib.hasInfix "--audio-output-device pulse/lanbat_aec_playback" lvaPi3Aec.systemd.services.linux-voice-assistant.serviceConfig.ExecStart
    ))

    (expect "lva: stop works at the end of a transcript that caught the radio" (
      let
        stop = lib.findFirst (a: a.id == "lanbat_voice_stop") null (haConfig lvaServer).automation;
      in
      lib.elem "{noise} (stop|turn off) the (music|radio|podcast|audiobook|player|speaker)" (lib.head stop.trigger)
      .command
    ))

    # Speech-to-text goes through the speaker-identification proxy, which
    # passes it on to faster-whisper; faster-whisper's entry stays.
    (expect "voice-id: the pipeline's speech-to-text, in front of faster-whisper" (
      base.systemd.services ? voice-id
      && lib.hasInfix "--upstream tcp://127.0.0.1:10301" base.systemd.services.voice-id.serviceConfig.ExecStart
      && lib.hasInfix ''export PIPELINE_STT_ENGINE="stt.voice_id"'' (postSetup base)
      && lib.elem 10303 base.lanbat.services.voice-id.extraPorts
    ))

    # A "stop" step inside if/then throws away the reply set before it (HA's
    # _StopScript skips copying the sub-script's response), so the automations
    # branch with if/then/else instead and every branch ends with a reply.
    (expect "voice automations never use stop, so their replies reach the satellite" (
      let
        voiceAutomations = lib.filter (a: lib.hasPrefix "lanbat_voice" a.id) (haConfig base).automation;
      in
      voiceAutomations != [ ]
      && !(lib.any (a: lib.hasInfix "\"stop\":" (builtins.toJSON a.action)) voiceAutomations)
    ))

    # Any host with an LVA satellite plays music in its room: a Snapcast client
    # (a Music Assistant player) and the ducker, like the Pis.
    (expect "lva: a server satellite is also a music player, with ducking" (
      lvaServer.lanbat.snapclient.enable
      && lvaServer.systemd.services ? snapclient
      && lvaServer.systemd.services ? lva-snapcast-duck
      && lvaServer.services.pipewire.wireplumber.extraConfig ? "51-snapclient-volume"
    ))

    (expect "lva: playMusic = false keeps a satellite to spoken replies" (
      !(lvaServerWith { playMusic = false; }).lanbat.snapclient.enable
    ))

    (expect "lva: the wake words are set in prefs.json on every start" (
      lib.any (lib.hasInfix "lva-set-prefs") (lvaPre lvaServer)
      && lvaServer.lanbat.voiceSatellite.lva.wakeModels == [ "okay_nabu" ]
    ))

    (expect "lva: two wake words; the first is --wake-model, hey_nabu brings its model" (
      lvaTwoWords.lanbat.voiceSatellite.lva.wakeModels == [
        "okay_nabu"
        "hey_nabu"
      ]
      && lib.hasInfix "--wake-model okay_nabu" (lvaExec lvaTwoWords)
      && lib.hasInfix "lva-wakewords-hey-nabu" (lvaExec lvaTwoWords)
    ))

    (expect "lva: the old wakeModel option still works, with a warning" (
      lvaOldOption.lanbat.voiceSatellite.lva.wakeModels == [ "hey_jarvis" ]
      && lib.hasInfix "--wake-model hey_jarvis" (lvaExec lvaOldOption)
      && lib.any (lib.hasInfix "wakeModel is deprecated") lvaOldOption.warnings
    ))

    (expect "lva: more than two wake words are rejected" (
      !(builtins.tryEval (builtins.deepSeq lvaThreeWords.lanbat.voiceSatellite.lva.wakeModels true))
      .success
    ))

    (expect "tv: Home Assistant reaches each Kodi with its password and knows its room" (
      lib.all (name: lib.elem name base.lanbat.services.home-assistant.consumes) [
        "kodi"
        "kodi-events"
      ]
      && base.lanbat.services.home-assistant.secrets.kodi-web-password.owner == "hass"
      && lib.all (c: lib.elem c base.services.home-assistant.extraComponents) [
        "kodi"
        "androidtv"
      ]
      && lib.hasInfix "pi-storage|" (postSetup base)
      && lib.hasInfix "|Living Room|" (postSetup base)
      && lib.hasInfix "KODI_PASSWORD_FILE" (postSetup base)
      && lib.hasInfix "ANDROID_TVS" (postSetup base)
    ))

    (expect
      "tv: an LVA satellite on the TV box pauses Kodi, ducks its music and keeps the peripheral API"
      (
        lvaTvBox.systemd.services ? lva-kodi-companion
        && lvaTvBox.systemd.services.lva-kodi-companion.serviceConfig.DynamicUser
        && lvaTvBox.systemd.services.lva-kodi-companion.environment.KODI_PORT == "9090"
        && lvaTvBox.systemd.services.lva-snapcast-duck.environment.DUCK_BINARIES == "snapclient,kodi.bin"
        && lib.hasInfix "--peripheral-port" (lvaExec lvaTvBox)
        && !(base.systemd.services ? lva-kodi-companion)
      )
    )

    # The reply volume is set at every start, the wake words' way; without
    # the option, the volume Home Assistant last set stays.
    (expect "lva: the configured reply volume is written before LVA starts" (
      lib.any (lib.hasInfix "lva-set-prefs 0.4") lvaTvBox.systemd.services.linux-voice-assistant.serviceConfig.ExecStartPre
      && lib.any (lib.hasInfix "lva-set-prefs") lvaPi3Aec.systemd.services.linux-voice-assistant.serviceConfig.ExecStartPre
      && !lib.any (lib.hasInfix "lva-set-prefs ") lvaPi3Aec.systemd.services.linux-voice-assistant.serviceConfig.ExecStartPre
      # A cutoff with no volume still passes an empty first argument, so the
      # probability stays the second argument of lva-set-prefs.
      && lib.any (lib.hasInfix ''lva-set-prefs "" 0.95'') lvaWakeThreshold.systemd.services.linux-voice-assistant.serviceConfig.ExecStartPre
    ))

    # They stop with LVA (partOf); starting LVA again (a restart by hand, the
    # microphone plugged back in) must bring them back, or nothing pauses or
    # ducks until a reboot.
    (expect "lva: the ducker and the Kodi companion start with LVA" (
      lib.all (u: lib.elem "linux-voice-assistant.service" lvaTvBox.systemd.services.${u}.wantedBy) [
        "lva-snapcast-duck"
        "lva-kodi-companion"
      ]
    ))

    # Kodi's startup update rescans only folders it has scanned before; a music
    # source nothing scanned stayed empty (9,784 songs on the Pi 5, 2026-10-08).
    (expect "tv: Kodi's first music scan runs after Kodi starts" (
      let
        scan = lvaTvBox.systemd.services.kodi-music-scan;
      in
      lib.elem "tv-kodi.service" scan.wantedBy
      && lib.elem "tv-kodi.service" scan.after
      && lib.hasInfix "music-scan" scan.script
    ))

    # The library sources come from settings: the repository's layout by
    # default, and a profile changes one field without losing the others.
    (expect "tv: Kodi's library sources are settings, the layout by default" (
      let
        env = c: c.systemd.services.kodi-bootstrap.environment;
        lines = c: lib.splitString "\n" (env c).KODI_VIDEO_SOURCES;
      in
      lib.elem "tv|/mnt/storage-a/media/tv/|tvshows|metadata.tvshows.themoviedb.org.python|0|0" (
        lines lvaTvBox
      )
      && lib.elem "movies|/mnt/storage-a/media/movies/|movies|metadata.themoviedb.org.python|1|0" (
        lines lvaTvBox
      )
      && lib.elem "music-videos|/mnt/storage-a/media/music-videos/|musicvideos|metadata.local|1|0" (
        lines lvaTvBox
      )
      && lib.hasInfix "Music|/mnt/storage-b/media/music/" (env lvaTvBox).KODI_MUSIC_SOURCES
      && lib.elem "tv|/mnt/storage-a/media/tv/shows/|tvshows|metadata.tvshows.themoviedb.org.python|0|0" (
        lines tvShowsElsewhere
      )
      && lib.length (lines tvShowsElsewhere) == lib.length (lines lvaTvBox)
      && tvShowsElsewhere.systemd.services.kodi-music-scan.environment ? KODI_MUSIC_SOURCES
    ))

    # Kodi never writes advancedsettings.xml, so it follows the repository;
    # a copy made once ("C") never saw a later change. Show folders named
    # like "Pantheon.S01.1080p..." are looked up without the season tag.
    (expect "tv: Kodi's advancedsettings.xml is the repository's, season tags cleaned" (
      lib.any (lib.hasPrefix "L+ /home/media/.kodi/userdata/advancedsettings.xml") lvaTvBox.systemd.tmpfiles.rules
      && lib.hasInfix ''<cleanstrings action="append">'' (
        builtins.readFile ../pkgs/kodi-tv-config/advancedsettings.xml
      )
    ))

    (expect "voice-id: identifies speakers into the router, voiceprints on the workload layer" (
      let
        exec = routerServer.systemd.services.voice-id.serviceConfig.ExecStart;
      in
      lib.hasInfix "--model /nix/store/" exec
      && lib.hasInfix "campplus" exec
      && lib.hasInfix "--store-dir /mnt/workload/voice-id" exec
      && lib.hasInfix "--api-port 10304" exec
      && lib.hasInfix "--router-url http://127.0.0.1:8092/v1/voice" exec
      && lib.hasInfix "--voice-id-url http://127.0.0.1:10304" routerServer.systemd.services.assistant-router.serviceConfig.ExecStart
      && routerServer.users.users.voice-id.uid == 959
      && routerServer.systemd.services.voice-id.serviceConfig.User == "voice-id"
      && lib.elem "-/mnt/workload/voice-id" routerServer.systemd.services.voice-id.serviceConfig.ReadWritePaths
      && lib.elem "workload-online.target" routerServer.systemd.services.voice-id-unlocked.wantedBy
      && routerServer.lanbat.services.voice-id.tier != "workload"
      && failedAssertions routerServer == [ ]
    ))

    (expect "voice-id: without the router it identifies but tells nobody" (
      !(lib.hasInfix "--router-url" base.systemd.services.voice-id.serviceConfig.ExecStart)
      && !(lib.hasInfix "--voice-id-url" (
        base.systemd.services.assistant-router.serviceConfig.ExecStart or ""
      ))
    ))

    (expect "router: HA's agent behind the router starts it, llama.cpp and the gateway" (
      routerServer.systemd.services ? assistant-router
      && routerServer.systemd.services ? llm-gateway
      && routerServer.services.llama-cpp.enable
      && routerServer.services.litellm.enable
      && lib.hasInfix "--local-model qwen3-4b" routerServer.systemd.services.assistant-router.serviceConfig.ExecStart
      && lib.hasInfix "--mode local-first" routerServer.systemd.services.assistant-router.serviceConfig.ExecStart
      && routerServer.lanbat.services.llm-gateway.secrets ? llm-gateway-env
      # LiteLLM runs as a dynamic user; systemd reads the EnvironmentFile as root.
      && routerServer.lanbat.services.llm-gateway.secrets.llm-gateway-env.owner == "root"
      && failedAssertions routerServer == [ ]
    ))

    (expect "router: Home Assistant's agent prompt is the router's context block, with tools" (
      lib.hasInfix ''export LLM_ROUTER="1"'' (postSetup routerServer)
      && lib.hasInfix ''export LLM_USE_TOOLS="true"'' (postSetup routerServer)
      && !(lib.hasInfix ''export LLM_ROUTER="1"'' (postSetup base))
      && lib.hasInfix "LANBAT-CONTEXT v1" (
        builtins.readFile ../pkgs/home-assistant-post-setup/setup-ha.sh
      )
      && lib.hasInfix "name: media_control" (
        builtins.readFile ../pkgs/home-assistant-post-setup/setup-ha.sh
      )
    ))

    (expect "router: a host that lists its services must list the router and the gateway" (
      lib.any (lib.hasInfix "assistant-router") (failedAssertions routerUnlisted)
    ))

    (expect "router: the request log rotates and keeps two weeks" (
      lib.hasInfix "--log-dir /var/lib/assistant-router" routerServer.systemd.services.assistant-router.serviceConfig.ExecStart
      && lib.hasInfix "--log-days 14" routerServer.systemd.services.assistant-router.serviceConfig.ExecStart
    ))

    (expect "router: gateway models take extra LiteLLM parameters and fall back in order" (
      let
        litellm = routerServer.services.litellm.settings;
        smart = lib.findFirst (m: m.model_name == "smart") null litellm.model_list;
      in
      smart.litellm_params == {
        model = "gemini/gemini-3.5-flash";
        reasoning_effort = "none";
      }
      && lib.elem { smart = [ "smart-fallback-1" ]; } litellm.router_settings.fallbacks
      # A model out of quota (429) is passed over at once, not retried, and
      # left alone for ten minutes.
      && litellm.router_settings.num_retries == 0
      && litellm.router_settings.allowed_fails == 0
      && litellm.router_settings.cooldown_time == 600
    ))

    # Behind the router the local model is on the voice path: it gets the CPU
    # ahead of Frigate and Jellyfin, and a busy server hands over to the cloud
    # after 2.5 s instead of 4.
    (expect "router: the local model has priority and a short time limit" (
      routerServer.systemd.services.llama-cpp.serviceConfig.CPUWeight == 200
      && routerServer.systemd.services.llama-cpp.serviceConfig.Nice == -5
      && base.systemd.services.llama-cpp.serviceConfig.CPUWeight or 50 == 50
      && lib.hasInfix "--local-timeout 2.5" routerServer.systemd.services.assistant-router.serviceConfig.ExecStart
    ))

    (expect "router: a server without it runs as before" (
      !(base.systemd.services ? assistant-router)
      && !(base.systemd.services ? llm-gateway)
      && !(base.services.litellm.enable or false)
    ))

    (expect "tv: the companion rewinds a film it paused for a question" (
      lvaTvBox.systemd.services.lva-kodi-companion.environment.RESUME_REWIND_SECONDS == "3"
    ))

    (expect "tv: a new Kodi stream doesn't inherit a ducked volume" (
      lvaTvBox.services.pipewire.wireplumber.extraConfig ? "51-kodi-volume"
    ))

    (expect "tv: Kodi's power is switched over CEC, library calls go through an event" (
      (automationById base "lanbat_kodi_power").action != [ ]
      && lib.hasInfix "tv.{{ trigger.id }}" (
        builtins.toJSON (automationById base "lanbat_kodi_power").action
      )
      && lib.any (t: lib.elem "watch {query}" t.command) (automationById base "lanbat_voice_play").trigger
      && lib.hasInfix "VideoLibrary.GetEpisodes" (
        builtins.toJSON (automationById base "lanbat_voice_play")
      )
    ))

    (expect "tv: an Android box opens its own apps by name, longest first, and nothing else" (
      let
        tvTriggers = (automationById tvApps "lanbat_voice_tv").trigger;
        baseTv = (automationById base "lanbat_voice_tv").trigger;
      in
      lib.any (
        t: (t.id or "") == "open" && lib.hasInfix "(YouTube Music|Netflix|YouTube)" (lib.head t.command)
      ) tvTriggers
      && !lib.any (t: (t.id or "") == "open") baseTv
      && lib.hasInfix "|Living Room" (postSetup tvApps)
    ))

    # Seeking, subtitles, what's on, stopping and episodes act on the film or
    # show on the room's Kodi; none takes "play ...", which the video and
    # music requests own.
    (expect "tv: Kodi takes seek, subtitles, what's on, stop and episode requests" (
      let
        tvTriggers = (automationById base "lanbat_voice_tv").trigger;
        ids = map (t: t.id or "") tvTriggers;
        sentences = lib.concatMap (t: t.command or [ ]) tvTriggers;
      in
      lib.all (id: lib.elem id ids) [
        "seek"
        "subtitles"
        "whats_on"
        "stop_video"
        "episode"
      ]
      && !lib.any (lib.hasPrefix "[play]") sentences
      && !lib.any (lib.hasPrefix "play ") sentences
    ))

    # Home Assistant has no regex_escape filter; a template using one
    # disables its whole automation at load.
    (expect "voice automations use only filters Home Assistant has" (
      !lib.hasInfix "regex_escape" (builtins.toJSON (haConfig base).automation)
    ))

    (expect "home assistant: the defaults keep today's URLs and Zigbee watch" (
      (haConfig base).homeassistant.internal_url == "http://127.0.0.1:8123"
      && lib.hasInfix ''export FRIGATE_URL="http://127.0.0.1:5000/"'' (postSetup base)
      && lib.hasInfix ''export MUSIC_ASSISTANT_URL="http://127.0.0.1:8095"'' (postSetup base)
      && lib.hasInfix ''export MQTT_PORT="1883"'' (postSetup base)
      && base.lanbat.voiceSatellite.homeAssistant.url == "http://127.0.0.1:8123"
      &&
        map (a: a.id) (haConfig base).automation == [
          "lanbat_zigbee_bridge_offline"
          "lanbat_zigbee_bridge_online"
          "lanbat_voice_play"
          "lanbat_voice_stop"
          "lanbat_voice_volume"
          "lanbat_voice_tv"
          "lanbat_kodi_call"
          "lanbat_kodi_power"
        ]
      && !(haYamlDashboard base)
    ))

    # Two satellites in one room (the server's and a Pi's in the Office) each
    # have a Music Assistant player there; "the room's player" alone chose the
    # same one for both, so a volume command to the Pi changed the server's.
    (expect "voice: play and volume prefer the player of the satellite that heard the request" (
      let
        vars = id: lib.foldl' (acc: s: acc // (s.variables or { })) { } (automationById base id).action;
        volume = vars "lanbat_voice_volume";
        play = vars "lanbat_voice_play";
        hosts = builtins.fromJSON volume.satellite_hosts;
      in
      hosts."Pi 3 Satellite" == "pi3"
      && hosts."Server Satellite" == "server"
      && volume.satellite_hosts == play.satellite_hosts
      && lib.hasInfix "device_attr(d, 'name')" volume.own_host
      && lib.hasInfix "own_host in [device_attr(p, 'name_by_user'), device_attr(p, 'name')]" volume.player
      && lib.hasInfix "own_host in [device_attr(p, 'name_by_user'), device_attr(p, 'name')]" play.player
      # Kodi still wins the volume while it has something on.
      && lib.hasInfix "integration_entities('kodi')" volume.player
    ))

    (expect "home assistant: dashboards generated from the registries, rooms from deviceAreas" (
      lib.hasInfix "export DEVICE_AREAS=" (postSetup base)
      && lib.hasInfix "livingroom_lamp" (postSetup base)
      && lib.hasInfix "/bin/home-assistant-dashboards" (postSetup base)
      && lib.hasInfix ''"Frigate":"https://'' (postSetup base)
      && lib.hasInfix "https://grafana." (postSetup base)
      && base.systemd.timers.home-assistant-post-setup.timerConfig.OnCalendar != null
    ))

    (expect "home assistant: the URLs follow the services' descriptions" (
      (haConfig haMoved).homeassistant.internal_url == "http://127.0.0.1:18123"
      && lib.hasInfix ''export INTERNAL_URL="http://127.0.0.1:18123"'' haMoved.systemd.services.home-assistant-bootstrap.script
      && lib.hasInfix ''export FRIGATE_URL="http://127.0.0.1:15000/"'' (postSetup haMoved)
      && lib.hasInfix ''export MUSIC_ASSISTANT_URL="http://127.0.0.1:18095"'' (postSetup haMoved)
      && lib.hasInfix ''export MQTT_PORT="11883"'' (postSetup haMoved)
      && haMoved.lanbat.voiceSatellite.homeAssistant.url == "http://127.0.0.1:18123"
    ))

    (expect "home assistant: the Zigbee watch can be turned off" (
      map (a: a.id) (haConfig haNoZigbee).automation == [
        "lanbat_voice_play"
        "lanbat_voice_stop"
        "lanbat_voice_volume"
        "lanbat_voice_tv"
        "lanbat_kodi_call"
        "lanbat_kodi_power"
      ]
      && !(haYamlDashboard haNoZigbee)
    ))

    (expect "telegraf: the defaults keep today's outputs and inputs" (
      let
        t = telegrafConf base;
      in
      (lib.head t.outputs.influxdb_v2).urls == [ "http://localhost:8086" ]
      &&
        healthChecks base == [
          "grafana http://127.0.0.1:3030/api/health"
          "home-assistant http://127.0.0.1:8123/"
          "jellyfin http://127.0.0.1:8096/health"
          "immich http://127.0.0.1:2283/api/server/ping"
          "vaultwarden http://127.0.0.1:8222/alive"
        ]
      &&
        (lib.head t.inputs.ping).urls == [
          "192.0.2.11"
          "192.0.2.1"
          "1.1.1.1"
        ]
      && (lib.head t.inputs.redis).servers == [ "tcp://127.0.0.1:6379" ]
    ))

    (expect "telegraf: the ports follow the services and the ping targets the profile" (
      let
        t = telegrafConf telegrafChanged;
      in
      (lib.head t.outputs.influxdb_v2).urls == [ "http://localhost:18086" ]
      && lib.head (healthChecks telegrafChanged) == "grafana http://127.0.0.1:13030/api/health"
      && (lib.head t.inputs.ping).urls == [ "192.0.2.1" ]
      && (lib.head t.inputs.redis).servers == [ "tcp://127.0.0.1:16379" ]
    ))

    (expect "romm: the defaults keep today's library on drive b" (
      lib.sublist 3 2 (rommVolumes base) == [
        "/srv/storage/b/media/roms:/romm/library/roms"
        "/srv/storage/b/media/roms-browser/mame:/romm/library/roms/mame"
      ]
      && base.lanbat.services.romm.nfs.drives == [ "b" ]
      && base.lanbat.services.romm.workloadDirs.romm.mode == "0711"
      && base.lanbat.services.romm.workloadDirs."romm/resources".mode == "0711"
      &&
        base.systemd.services.romm-browser-romsets.environment.SOURCE_DIR
        == "/srv/storage/b/media/roms/mame"
      && (envOf base "romm").REDIS_PORT == "6379"
      && (envOf base "romm").OIDC_ALLOW_REGISTRATION == "false"
      && (envOf base "romm").DISABLE_USERPASS_LOGIN == "false"
      && lib.hasSuffix "/api/oauth/openid" (envOf base "romm").OIDC_REDIRECT_URI
      && lib.hasInfix "-v user=akadmin " base.systemd.services.romm-admin-email.script
      && lib.elem "romm-admin-email.service" base.systemd.services.podman-romm.wants
      && lib.elem "romm-admin-email" base.lanbat.services.romm.units
    ))

    (expect "romm: a profile moves the library, and the NFS dependency follows" (
      lib.sublist 3 2 (rommVolumes rommMoved) == [
        "/srv/storage/a/games/roms:/romm/library/roms"
        "/srv/storage/a/games/arcade:/romm/library/roms/mame"
      ]
      && rommMoved.lanbat.services.romm.nfs.drives == [ "a" ]
      &&
        rommMoved.systemd.services.romm-browser-romsets.environment.TARGET_DIR
        == "/srv/storage/a/games/arcade"
      && lib.hasInfix "-v user=alice " rommMoved.systemd.services.romm-admin-email.script
      && failedAssertions rommMoved == [ ]
    ))

    (expect "music-assistant: the setup keeps today's URLs" (
      lib.all (line: lib.hasInfix line (maScript base)) [
        ''export MA_URL="http://127.0.0.1:8095"''
        ''export MA_PUBLIC_URL="https://music.home.example.com"''
        ''export HA_INTERNAL_URL="http://127.0.0.1:8123"''
        ''export HA_PUBLIC_URL="https://ha.home.example.com"''
      ]
    ))

    (expect "music-assistant: the setup follows the services' ports and subdomains" (
      lib.all (line: lib.hasInfix line (maScript maMoved)) [
        ''export MA_URL="http://127.0.0.1:18095"''
        ''export HA_INTERNAL_URL="http://127.0.0.1:18123"''
        ''export HA_PUBLIC_URL="https://hass.home.example.com"''
      ]
    ))

    (expect "music-assistant: the fanart.tv VIP key is off by default and needs no key" (
      !(base.lanbat.secrets ? ma-fanarttv-key) && !lib.hasInfix "MA_FANARTTV_KEY" (maScript base)
    ))

    (expect "music-assistant: settings.fanartTvVip requires the key and hands it to the setup" (
      maFanartVip.lanbat.secrets ? ma-fanarttv-key
      && lib.hasInfix maFanartVip.lanbat.secrets.ma-fanarttv-key.path (maScript maFanartVip)
      && failedAssertions maFanartVip == [ ]
    ))

    (expect "immich: the defaults keep today's upload directory and ports" (
      immichUpload base == "/srv/storage/a/photos:/usr/src/app/upload"
      && base.lanbat.services.immich.nfs.drives == [ "a" ]
      && lib.elem "d /srv/storage/a/photos 0750 immich immich -" base.systemd.tmpfiles.rules
      && (envOf base "immich-server").DB_PORT == "5432"
      && (envOf base "immich-server").REDIS_PORT == "6379"
      && lib.hasInfix ''IMMICH_URL="http://127.0.0.1:2283"'' base.systemd.services.immich-bootstrap.script
      && lib.hasInfix ''"https://photos.home.example.com"'' base.systemd.services.podman-immich-server.preStart
    ))

    (expect "immich: images are pinned to the deployed v3.2.1 digests" (
      imageOf base "immich-server"
      == "ghcr.io/immich-app/immich-server:v3.2.1@sha256:87bb1b208434a8503e1a2465edd84f3cf94bd72c66feb7ca474629015b8dbfd6"
      &&
        imageOf base "immich-machine-learning"
        == "ghcr.io/immich-app/immich-machine-learning:v3.2.1@sha256:49a53dbf5fbea5c785075667056fb010498969b9143005df5434868035bf5654"
    ))

    (expect "immich: machine learning cannot exhaust the host" (
      optionsOf base "immich-machine-learning" == [
        "--network=host"
        "--memory=8g"
        "--memory-reservation=6g"
        "--memory-swap=10g"
        "--cpus=2"
      ]
    ))

    (expect "server: compressed swap and bounded core dumps preserve recovery headroom" (
      base.zramSwap.enable
      && base.zramSwap.memoryPercent == 25
      && base.zramSwap.memoryMax == 8 * 1024 * 1024 * 1024
      && base.zramSwap.priority == 100
      && lib.all (line: lib.hasInfix line base.environment.etc."systemd/coredump.conf".text) [
        "Storage=external"
        "ProcessSizeMax=1G"
        "ExternalSizeMax=1G"
        "MaxUse=2G"
        "KeepFree=15G"
      ]
    ))

    (expect "immich: a profile moves the uploads, and the ports follow the services" (
      immichUpload immichMoved == "/srv/storage/b/media/photos:/usr/src/app/upload"
      && immichMoved.lanbat.services.immich.nfs.drives == [ "b" ]
      && (envOf immichMoved "immich-server").REDIS_PORT == "16379"
      && lib.hasInfix ''IMMICH_URL="http://127.0.0.1:12283"'' immichMoved.systemd.services.immich-bootstrap.script
      && failedAssertions immichMoved == [ ]
    ))

    (expect "grafana: the InfluxDB datasource follows InfluxDB's endpoint" (
      let
        influxUrl =
          config: (lib.head config.services.grafana.provision.datasources.settings.datasources).url;
      in
      influxUrl base == "http://localhost:8086"
      && influxUrl telegrafChanged == "http://localhost:18086"
      && base.services.grafana.settings.server.root_url == "https://grafana.home.example.com"
    ))

    (expect "nextcloud: the bulk data directories keep today's paths, and move" (
      lib.all (rule: lib.elem rule (nextcloudDirs base)) [
        "d /srv/storage/b/nextcloud          0750 nextcloud nextcloud -"
        "d /srv/storage/b/nextcloud/external 0750 nextcloud nextcloud -"
        "d /srv/storage/b/nextcloud/users    0750 nextcloud nextcloud -"
      ]
      && lib.elem "d /srv/storage/a/cloud/users    0750 nextcloud nextcloud -" (
        nextcloudDirs nextcloudMoved
      )
      && !lib.any (lib.hasPrefix "d /srv/storage/b/nextcloud") (nextcloudDirs nextcloudMoved)
      && nextcloudMoved.lanbat.services.nextcloud.nfs.drives == [ ]
    ))

    (expect "redis: the consumers keep today's indexes" (
      lib.mapAttrs (_: db: db.index) base.lanbat.redis.databases == {
        authentik = 0;
        immich = 1;
        romm = 2;
      }
      && (envOf base "authentik-server").AUTHENTIK_REDIS__DB == "0"
      && (envOf base "immich-server").REDIS_DBINDEX == "1"
      && (envOf base "romm").REDIS_DB == "2"
    ))

    (expect "redis: two consumers claiming one index are rejected" (
      lib.any (lib.hasInfix "index 1 is claimed by immich and other") (failedAssertions redisClash)
    ))

    (expect "nextcloud: the workload instance's nextcloud database, over its socket" (
      let
        nc = base.services.nextcloud;
        pg = base.services.postgresql;
      in
      !nc.database.createLocally
      && nc.config.dbhost == "/run/postgresql"
      && nc.config.dbname == "nextcloud"
      && nc.config.dbuser == "nextcloud"
      && nc.config.dbpassFile == null
      && base.lanbat.postgresql.databases.nextcloud.instance == "workload"
      && lib.elem "nextcloud" pg.ensureDatabases
      && lib.any (u: u.name == "nextcloud" && u.ensureDBOwnership) pg.ensureUsers
      && lib.elem "postgresql.target" base.systemd.services.nextcloud-setup.requires
      && lib.elem "postgresql.target" base.systemd.services.nextcloud-setup.after
    ))

    (expect "jellyfin: IMVDb is off by default and needs no key" (
      !(base.lanbat.secrets ? jellyfin-imvdb-env) && !lib.hasInfix "imvdb" (jellyfinBootstrap base)
    ))

    (expect "jellyfin: settings.imvdb requires the IMVDb key and hands it to the bootstrap" (
      jellyfinImvdb.lanbat.secrets ? jellyfin-imvdb-env
      && lib.hasInfix jellyfinImvdb.lanbat.secrets.jellyfin-imvdb-env.path (
        jellyfinBootstrap jellyfinImvdb
      )
      && failedAssertions jellyfinImvdb == [ ]
    ))

    (expect "audiobookshelf: the library defaults to drive b's audiobooks and Audible" (
      (absEnv base).LIBRARY_PATH == "/srv/storage/b/media/audiobooks"
      && (absEnv base).METADATA_PROVIDER == "audible"
      && base.lanbat.services.audiobookshelf.nfs.drives == [ "b" ]
      && (absEnv base).EXTERNAL_URL == "https://audiobooks.home.example.com"
    ))

    (expect "audiobookshelf: a profile moves the library and picks the provider" (
      (absEnv absMoved).LIBRARY_PATH == "/srv/storage/a/books/audio"
      && (absEnv absMoved).METADATA_PROVIDER == "audible.uk"
      && absMoved.systemd.services.audiobookshelf-match.environment.METADATA_PROVIDER == "audible.uk"
      && absMoved.lanbat.services.audiobookshelf.nfs.drives == [ "a" ]
      && lib.elem "srv-storage-a.mount" absMoved.systemd.services.audiobookshelf.bindsTo
      && failedAssertions absMoved == [ ]
    ))

    (expect "snapcast: its ports are no longer open to the whole LAN" (
      !lib.elem 1704 base.networking.firewall.allowedTCPPorts
      && !lib.elem 1705 base.networking.firewall.allowedTCPPorts
    ))

    (expect "snapcast: the Pi's snapclient and the Snapdroid box are admitted on both ports" (
      admits "192.0.2.11" base && admits "192.0.2.50" base
    ))

    (expect "snapcast: a listed client is admitted, and its rules are removed on stop" (
      admits "192.0.2.70" snapClients
      && lib.hasInfix "iptables -D INPUT -p tcp --dport 1705 -s 192.0.2.70 -j ACCEPT" (fwStop snapClients)
      && !lib.hasInfix "192.0.2.70" (fwStart base)
    ))

    (expect "snapcast: a client listed by MAC is admitted over IPv4 and IPv6, and removed on stop" (
      lib.all
        (
          cmd:
          lib.all
            (
              port:
              lib.hasInfix "${cmd} -I INPUT -p tcp --dport ${toString port} -m mac --mac-source 2c:d8:ae:00:00:01 -j ACCEPT" (
                fwStart snapMac
              )
              && lib.hasInfix "${cmd} -D INPUT -p tcp --dport ${toString port} -m mac --mac-source 2c:d8:ae:00:00:01 -j ACCEPT" (
                fwStop snapMac
              )
            )
            [
              1704
              1705
            ]
        )
        [
          "iptables"
          "ip6tables"
        ]
    ))

    (expect "snapcast: a client needs a MAC or an IPv4 address, and a well-formed MAC" (
      lib.any (lib.hasInfix "client tv") (failedAssertions snapEmpty)
      && lib.any (lib.hasInfix "2c-d8-ae") (failedAssertions snapBadMac)
    ))

    (expect "snapcast: a client must be an IPv4 address" (
      lib.any (lib.hasInfix "phone.lan") (failedAssertions snapBadHost)
    ))

    (expect "jackett: workload-native service stays private behind admin forward auth" (
      jackett.subdomain == "jackett"
      && jackett.port == 9117
      && jackett.auth == "forward-auth"
      && jackett.access.groups == [ "authentik Admins" ]
      && jackett.tier == "workload"
      && jackett.state == [ "jackett" ]
      &&
        jackett.units == [
          "jackett"
          "jackett-definitions"
          "jackett-qbittorrent-plugin"
        ]
      && jackettService.enable
      && !jackettService.openFirewall
      && lib.elem "workload-online.target" jackettUnit.wantedBy
    ))

    (expect "jackett: it listens only on loopback, and no firewall rule admits 9117" (
      lib.hasInfix " --ListenPrivate" jackettUnit.serviceConfig.ExecStart
      && !lib.hasInfix "--ListenPublic" jackettUnit.serviceConfig.ExecStart
      && lib.hasInfix "--Port 9117" jackettUnit.serviceConfig.ExecStart
      && !lib.elem 9117 base.networking.firewall.allowedTCPPorts
      && !lib.elem 9117 base.networking.firewall.allowedUDPPorts
    ))

    (expect "jackett: the plugin unit runs again before every qBittorrent start" (
      jackettPluginUnit.serviceConfig.Type == "oneshot"
      && !(jackettPluginUnit.serviceConfig.RemainAfterExit or false)
    ))

    (expect "jackett: its data directory is created for the jackett user on the workload layer" (
      lib.attrNames jackett.workloadDirs == [
        "jackett"
        "jackett/.config"
        "jackett/.config/Jackett"
      ]
      && lib.all (dir: dir.user == "jackett" && dir.group == "jackett" && dir.mode == "0700") (
        lib.attrValues jackett.workloadDirs
      )
    ))

    (expect "jackett: qBittorrent's plugin is configured from Jackett's generated API key" (
      lib.elem "workload-online.target" jackettPluginUnit.wantedBy
      && lib.elem "jackett.service" jackettPluginUnit.requires
      && lib.elem "jackett.service" jackettPluginUnit.after
      && lib.all (text: lib.hasInfix text jackettPluginUnit.script) [
        "/var/lib/jackett/.config/Jackett/ServerConfig.json"
        "/var/lib/qbittorrent/qBittorrent/nova3/engines"
        "http://127.0.0.1:9117"
        "YOUR_API_KEY_HERE"
        "chown qbt:qbt"
        "chmod 0600"
      ]
      && !lib.hasInfix "--ListenPublic" jackettUnit.serviceConfig.ExecStart
    ))

    # pkgs/jackett exists to be newer than nixpkgs'. If nixpkgs overtakes it,
    # the pin has become a downgrade: bump it (pkgs/jackett/update.sh) or drop it.
    (expect "jackett: the pinned build is not older than the locked nixpkgs'" (
      jackettService.package.pname == "jackett"
      && lib.versionAtLeast jackettService.package.version pkgs.jackett.version
    ))

    (expect
      "jackett: upstream's indexer definitions are synced on the workload layer, before Jackett starts"
      (
        jackettDefinitionsUnit.serviceConfig.Type == "oneshot"
        && lib.elem "jackett.service" jackettDefinitionsUnit.before
        && !lib.elem "jackett.service" (jackettDefinitionsUnit.requires or [ ])
        && lib.elem "workload-online.target" jackettDefinitionsUnit.wantedBy
        && lib.elem "workload-online.target" jackettDefinitionsTimer.wantedBy
        && lib.elem "workload-online.target" jackettDefinitionsTimer.partOf
        && jackettDefinitionsTimer.timerConfig.OnCalendar != null
        && jackettUnit.environment.XDG_CONFIG_HOME == "/var/lib/jackett/xdg"
        && lib.all (text: lib.hasInfix text jackettDefinitionsUnit.script) [
          "/var/lib/jackett/xdg/cardigann/definitions"
          "src/Jackett.Common/Definitions"
          "restart --no-block jackett.service"
        ]
      )
    )

    (expect "jackett: qBittorrent starts only after its plugin is configured" (
      lib.elem "jackett-qbittorrent-plugin.service" qbittorrentUnit.requires
      && lib.elem "jackett-qbittorrent-plugin.service" qbittorrentUnit.after
    ))

    (expect "snapcast: two clients can't share an address" (
      lib.any (lib.hasInfix "192.0.2.70") (failedAssertions snapDuplicate)
    ))

    (expect "music-assistant: a snapserver on another host is rejected" (
      lib.any (lib.hasInfix "#117") (failedAssertions maApart)
    ))

    (expect "person-mapper: no cameras means no unit, no account and no wiring" (
      !(base.systemd.services ? person-mapper)
      && !(base.users.users ? person-mapper)
      && base.lanbat.services.person-mapper.account == null
      && base.lanbat.services.person-mapper.consumes == [ ]
      && base.lanbat.services.person-mapper.readsSecrets == [ ]
      && base.lanbat.services.person-mapper.units == [ ]
    ))

    (expect "person-mapper: a camera switches on the unit, the account and the wiring" (
      personMapperOn.systemd.services ? person-mapper
      && personMapperOn.users.users ? person-mapper
      && personMapperOn.users.users.person-mapper.uid == 996
      && personMapperOn.lanbat.services.person-mapper.account.uid == 996
      &&
        personMapperOn.lanbat.services.person-mapper.consumes == [
          "mosquitto"
          "assistant-router"
        ]
      && personMapperOn.lanbat.services.person-mapper.readsSecrets == [ "mosquitto-person-mapper-pass" ]
      && personMapperOn.lanbat.services.person-mapper.units == [ "person-mapper" ]
    ))

    (expect
      "person-mapper: the broker carries its least-privilege user, and the secret is declared and read"
      (
        personMapperOn.lanbat.secrets ? mosquitto-person-mapper-pass
        && lib.elem "person-mapper" (mosquittoUsers personMapperOn)
        &&
          (lib.head personMapperOn.services.mosquitto.listeners).users."person-mapper".acl == [
            "read frigate/#"
            "write homeassistant/#"
            "write homelab/#"
          ]
      )
    )

    (expect
      "person-mapper: the unit runs as its account, copies the broker password as root, and starts after the broker and the router"
      (
        let
          unit = personMapperUnit personMapperOn;
          env = unit.serviceConfig.Environment or [ ];
        in
        unit.serviceConfig.User == "person-mapper"
        && unit.serviceConfig.Group == "person-mapper"
        && unit.serviceConfig.RuntimeDirectory == "person-mapper"
        && lib.hasPrefix "+" (lib.head unit.serviceConfig.ExecStartPre)
        && lib.hasPrefix "/nix/store/" unit.serviceConfig.ExecStart
        && lib.elem "mosquitto.service" unit.after
        && lib.elem "assistant-router.service" unit.after
        && lib.elem "network-online.target" unit.wants
        && lib.elem "multi-user.target" unit.wantedBy
        && lib.elem "MQTT_PASSWORD_FILE=/run/person-mapper/mqtt.pass" env
        && lib.elem "ROUTER_URL=ws://127.0.0.1:8770/v1/body" env
        # Anyone the face library names: the router knows its runtime people.
        && !(lib.any (lib.hasPrefix "PEOPLE_FILE=") env)
        && lib.any (lib.hasPrefix "CAMERAS_FILE=/nix/store/") env
        && failedAssertions personMapperOn == [ ]
      )
    )

    (expect "the example profile's server has no failed assertion" (failedAssertions base == [ ]))

    (expect
      "techo5-bridge: beside the router, each Echo Show is a body; Home Assistant adds it with its key"
      (
        let
          unit = routerNoTap.systemd.services.techo5-bridge;
          svc = routerNoTap.lanbat.services.techo5-bridge;
          setup = cfg: cfg.systemd.services.home-assistant-post-setup.script;
        in
        unit.environment ? CONFIG_FILE
        && unit.environment.HA_TOKEN_FILE == "/run/techo5-bridge/ha-token"
        && unit.serviceConfig.User == "techo5-bridge"
        && svc.secrets.techo5-kitchen-api-key.enable
        && lib.elem "assistant-router" svc.consumes
        && svc.extraPorts == [ 8775 ]
        # The CA goes on the device over SSH, with the bridge's key; host keys kept.
        && svc.secrets.techo5-ssh-key.enable
        && unit.serviceConfig.StateDirectory == "techo5-bridge"
        && unit.environment.HOME == "/var/lib/techo5-bridge"
        # The example server's own satellite declares the token; the bridge reads it.
        && lib.elem "ha-voice-token" svc.readsSecrets
        && !(svc.secrets ? ha-voice-token)
        # The sign-in code's port, for the device alone.
        && lib.hasInfix "iptables -I INPUT -p tcp --dport 8775 -s 192.0.2.60 -j ACCEPT" routerNoTap.networking.firewall.extraCommands
        && lib.hasInfix "TECHO5_DEVICES=" (setup routerNoTap)
        && lib.hasInfix "192.0.2.60" (setup routerNoTap)
        && lib.hasInfix "lanbat-screen-kitchen" (setup routerNoTap)
        && lib.hasInfix "VOICE_TOKEN_RECORD_FILE=" (setup routerNoTap)
        && lib.elem "esphome" routerNoTap.services.home-assistant.extraComponents
        && lib.elem "techo5-kitchen-api-key" routerNoTap.lanbat.services.home-assistant.readsSecrets
        # A message for a person reaches the body beside them; replies come from HTTPS.
        && (haConfig routerNoTap).script ? lanbat_notify_person
        &&
          (haConfig routerNoTap).homeassistant.internal_url
          == "https://ha.${routerNoTap.lanbat.deployment.domain}"
        # The example has no Echo: Home Assistant keeps its loopback URL.
        && !(lib.hasInfix "TECHO5_DEVICES=" (setup base))
        && failedAssertions routerNoTap == [ ]
      )
    )

    (expect "techo5-bridge: per-person to-do and reminder lists in the profile" (
      let
        cfg = builtins.fromJSON (
          builtins.unsafeDiscardStringContext (
            builtins.readFile routerNoTap.systemd.services.techo5-bridge.environment.CONFIG_FILE
          )
        );
        kitchen = lib.findFirst (d: d.name == "kitchen") null cfg.devices;
      in
      kitchen.profiles.alex.todo == "todo.alex_todo"
      && kitchen.profiles.alex.reminders == "todo.alex_reminders"
      && kitchen.profiles.sam.todo == "todo.sam_chores"
      && kitchen.profiles.sam.reminders == "todo.sam_reminders"
      && kitchen.household.todo == ""
      && kitchen.household.reminders == ""
    ))

    (expect "techo5-bridge: a profile's todo must name a todo list" (
      !(builtins.tryEval (builtins.deepSeq techo5BadTodo.lanbat.deployment.techo5Devices.kitchen true))
      .success
    ))

    (expect "techo5: completing a reminder from the screen uses the MQTT sensors and todo.update_item" (
      let
        script = (haConfig routerNoTap).script.lanbat_complete_reminder or null;
        json = builtins.toJSON script;
      in
      script != null
      && script.mode == "single"
      && lib.hasInfix "todo.update_item" json
      && lib.hasInfix ''"status":"completed"'' json
      && lib.hasInfix "state_attr(sensor, 'uid')" json
      && lib.hasInfix "state_attr(sensor, 'list')" json
      && lib.hasInfix "lanbat_screen_" json
      && lib.hasInfix "regex_replace('[^a-z0-9]+', '_')" json
      && lib.hasInfix "regex_replace('^_+|_+$', '')" json
      && lib.hasInfix "slot | int(0) >= 1 and slot | int(0) <= 5" json
      && lib.hasInfix "reminder_state not in ['none', 'unavailable', 'unknown', '']" json
      && !(haConfig base).script ? lanbat_complete_reminder
    ))

    (expect "techo5: Home Assistant post-setup creates default local_todo lists for profiles" (
      let
        setup = postSetup routerNoTap;
      in
      lib.hasInfix "TECHO5_TODO_LISTS=" setup
      && lib.hasInfix "alex_todo" setup
      && lib.hasInfix "alex_reminders" setup
      && lib.hasInfix "sam_reminders" setup
      && !(lib.hasInfix "sam_chores" setup)
      && lib.hasInfix "jo_todo" setup
      && lib.hasInfix "jo_reminders" setup
      && lib.hasInfix "ensure_todo_lists" (
        builtins.readFile ../pkgs/home-assistant-post-setup/setup-ha.sh
      )
      && lib.hasInfix "todo_list_name" (builtins.readFile ../pkgs/home-assistant-post-setup/setup-ha.sh)
      && lib.hasInfix "storage_key" (builtins.readFile ../pkgs/home-assistant-post-setup/setup-ha.sh)
    ))

    (expect "assistant-router: reads Home Assistant with the satellites' token, for My settings" (
      let
        unit = routerServer.systemd.services.assistant-router.serviceConfig;
        svc = routerServer.lanbat.services.assistant-router;
        satellite = routerServer.lanbat.voiceSatellite.enable;
      in
      lib.hasInfix "--ha-url http://127.0.0.1:" unit.ExecStart
      && lib.hasInfix "--ha-token-file %d/ha-voice-token" unit.ExecStart
      && lib.hasInfix "--prefs-state /var/lib/assistant-router/prefs.json" unit.ExecStart
      && lib.elem "ha-voice-token:${routerServer.lanbat.secrets.ha-voice-token.path}" unit.LoadCredential
      && (
        if satellite then
          lib.elem "ha-voice-token" svc.readsSecrets && routerServer.lanbat.voiceSatellite.haTokenWanted
        else
          svc.secrets.ha-voice-token.enable
      )
      && lib.elem "/settings" routerServer.lanbat.services.who.caddy.authBypassPaths
    ))

    (expect "assistant-router: personal lists are kept for post-setup, which runs when the file changes"
      (
        let
          unit = routerServer.systemd.services.assistant-router.serviceConfig;
          path = routerServer.systemd.paths.home-assistant-post-setup-lists;
          setup = postSetup routerServer;
        in
        lib.hasInfix "--lists-state /var/lib/assistant-router/lists.json" unit.ExecStart
        && path.pathConfig.PathChanged == "/var/lib/private/assistant-router/lists.json"
        && path.pathConfig.Unit == "home-assistant-post-setup.service"
        && lib.hasInfix "LISTS_FILE=\"/var/lib/private/assistant-router/lists.json\"" setup
        && lib.elem "/lists/*" routerServer.lanbat.services.who.caddy.authBypassPaths
      )
    )

    (expect
      "pi3: the watchdog restarts a Pi that stops, or whose card stops; store paths are on the card before they count"
      (
        lvaPi3Aec.systemd.settings.Manager.RuntimeWatchdogSec == "15s"
        && lvaPi3Aec.boot.kernel.sysctl."kernel.hung_task_panic" == 1
        && lvaPi3Aec.boot.kernel.sysctl."kernel.hung_task_timeout_secs" == 300
        && lvaPi3Aec.nix.settings.fsync-store-paths
      )
    )
  ];

  partCases = map (c: c.value) (
    lib.filter (c: lib.mod c.index parts == part - 1) (
      lib.imap0 (index: value: { inherit index value; }) cases
    )
  );

  failures = lib.filter (x: x != null) partCases;
in
pkgs.runCommand "service-settings-check-${toString part}" { } ''
  if [ ${toString (lib.length failures)} -ne 0 ]; then
    echo "service settings checks (part ${toString part} of ${toString parts}) failed:" >&2
    ${lib.concatStringsSep "\n" (map (msg: "echo \"  - ${msg}\" >&2") failures)}
    exit 1
  fi
  touch $out
''
