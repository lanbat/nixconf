# services/assistant-router.nix
#
# The assistant router: Home Assistant's LLM agent calls it instead of a
# model (lanbat.deployment.haLlm.baseUrl = "http://127.0.0.1:8092/v1",
# model "assistant"). Requests Home Assistant's own intents didn't match
# reach it; it drops noise (fragments, the assistant's own words, radio and
# film speech), lets the local model (services/llama-cpp.nix) act on clear
# device commands, and passes everything else to the cloud through the LLM
# gateway (services/llm-gateway.nix). See pkgs/assistant-router.
#
# Each request is logged, one JSON line, in
# /var/lib/assistant-router/requests-<date>.jsonl: what was heard (overheard
# speech included, unless logText is off), which tier answered, how and how
# fast. Files older than logDays are deleted.
#
# Robot bodies (a Stack-chan on a satellite, modules/pi/stackchan.nix) connect
# on bodyPort from the hosts that consume this service; nothing else reaches
# it. For a request from a room with a body, the cloud model answers as
# settings.body.persona and drives the body (pkgs/assistant-router body.py).
#
# With settings.tap and a forward-auth provider, each robot is also an NFC tag
# holding a link to https://who.<domain> (the "who" service below, on
# tapPort): a phone held to it opens the page, the provider's login says whose
# phone it is, and the router counts them in that room and learns the
# phone's Wi-Fi address for wifi-presence (pkgs/assistant-router tap.py).
#
# With Home Assistant on this host (settings.personal), the who page's "My
# settings" lets each person choose their screen's calendars, to-do list and
# photos from Home Assistant's, read with the voice satellites' token
# (ha-voice-token, an ordinary user's); "My lists" and spoken list commands
# ("add milk to my list") keep each person's own to-do lists in Home Assistant,
# and "remind me to call mum at 5pm" adds a reminder to theirs.
#
# Always-on: it is in the voice path. Home Assistant's side is loopback only.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  inherit (lib) mkOption types;
  hostLib = import ../lib/host.nix { inherit lib; };
  enabled = hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null);
  cfg = config.lanbat.services.assistant-router.settings;
  port = 8092;
  bodyPort = 8770;
  tapPort = 8772;
  router = pkgs.callPackage ../pkgs/assistant-router { };
  # YouTube on a room's screen (services/yt-show.nix): the cloud's search and
  # play tools (pkgs/assistant-router youtube.py). Read from the deployment,
  # not from config.lanbat.services.yt-show: asking that here would force the
  # description whose consumes asks this (infinite recursion).
  hasYt =
    config.lanbat.hasService "yt-show"
    && lib.filterAttrs (_: d: d.dlna) (config.lanbat.deployment.techo5Devices or { }) != { };

  # Hosts whose services consume the router: the bodies. The same derivation
  # as modules/wiring/policy.nix, which opens bodyPort to exactly these.
  bodyHosts = lib.unique (
    lib.concatLists (
      lib.mapAttrsToList (
        _: entry: if lib.elem "assistant-router" (entry.consumes or [ ]) then entry.hosts else [ ]
      ) config.lanbat.endpoints
    )
  );
  overlay = config.lanbat.overlay;
  bodyAddresses = lib.concatMap (
    h:
    [ config.lanbat.hosts.${h}.networking.ip ]
    ++ lib.optional (overlay.onOverlay h && overlay.onOverlay config.lanbat.hostKey) (
      overlay.addressOf h
    )
  ) bodyHosts;
  personaFile = pkgs.writeText "assistant-router-persona.txt" cfg.body.persona;
  # Who it may recognise (lanbat.deployment.people): keys and names only.
  peopleFile = pkgs.writeText "assistant-router-people.json" (
    builtins.toJSON (lib.mapAttrs (_: p: p.name) (config.lanbat.deployment.people or { }))
  );
  screenRooms = lib.unique (
    lib.catAttrs "room" (lib.attrValues (config.lanbat.deployment.techo5Devices or { }))
  );
  screenRoomsFile = pkgs.writeText "assistant-router-screen-rooms.json" (builtins.toJSON screenRooms);
  # The media players in each room, for the robots' quiet mode (media.py): the
  # room's hosts' Music Assistant players (media_player.<hostname>) and Kodi's
  # (media_player.kodi_<hostname>), which is absent where Kodi does not run.
  mediaRooms = lib.mapAttrs (
    _room: hosts:
    lib.concatMap (
      h:
      let
        hn = config.lanbat.hosts.${h}.networking.hostname;
      in
      [
        "media_player.${hn}"
        "media_player.kodi_${hn}"
      ]
    ) (lib.toList hosts)
  ) (config.lanbat.deployment.voiceRooms or { });
  mediaRoomsFile = pkgs.writeText "assistant-router-media-rooms.json" (builtins.toJSON mediaRooms);
  routerSettings.options.localTimeout = mkOption {
    type = types.numbers.positive;
    default = 2.5;
    description = ''
      Seconds the local model may take to decide before the request goes to
      the cloud. It decides in about 1.5 s on an idle server; a busy one (a
      library scan, Frigate) can take much longer, and then the cloud answers
      sooner.
    '';
  };
  routerSettings.options.logDays = mkOption {
    type = types.ints.positive;
    default = 14;
    description = ''
      Days of request log to keep (one file a day in /var/lib/assistant-router).
      The log holds what the microphones heard, overheard speech included.
    '';
  };
  routerSettings.options.logText = mkOption {
    type = types.bool;
    default = true;
    description = "Whether the request log keeps what was said, or only the tier, route and timing.";
  };
  routerSettings.options.homeAssistant = mkOption {
    type = types.bool;
    default = true;
    description = ''
      Publish the robots (each a device with its sensors, touches and
      controls), who probably spoke last and is nearby in each robot's room,
      and who is home, to Home Assistant over MQTT discovery
      (pkgs/assistant-router mqtt.py). Needs Mosquitto on this host; the
      broker login is the mosquitto-assistant-router-pass secret.
    '';
  };
  routerSettings.options.screenRooms = mkOption {
    type = types.listOf types.str;
    default = [ ];
    description = ''
      Rooms whose techo5 screen item sensors are cleared at router start
      (reminders, to-dos, events on the room's screen device in Home
      Assistant). Filled from lanbat.deployment.techo5Devices on the server.
    '';
  };
  routerSettings.options.ytPlayService = mkOption {
    type = types.nullOr types.str;
    default = null;
    example = "esphome.kitchen_play_video";
    description = ''
      The screen's `esphome.<node>_play_video` action, when it cannot be found
      by itself (pkgs/assistant-router youtube.py): with one Echo Show it is,
      and with several each action's name must hold its room unless this names
      one.
    '';
  };
  routerSettings.options.offerToMeet = mkOption {
    type = types.bool;
    default = true;
    description = ''
      Meeting Nabu: after answering a voice it doesn't know, in a room with a
      robot, the assistant asks once "What's your name?" (a room at most once
      a day). A name is consent: it learns the voice from that conversation,
      the robot's camera adds the face, and a card held on its head is theirs.
      A decline keeps nothing. "Remember me" does the same on request
      (pkgs/assistant-router identity.py, docs/people.md).
    '';
  };
  routerSettings.options.greet = mkOption {
    type = types.bool;
    default = true;
    description = ''
      The robots greet the people they recognise as they arrive: awake, happy,
      a wag and a nod, three green breaths and "Hi <name>!", once per arrival
      and not twice in a room within half an hour (pkgs/assistant-router
      greet.py, docs/stackchan.md). Home Assistant gets a "Recognized" event.
    '';
  };
  routerSettings.options.tap = mkOption {
    type = types.bool;
    default = true;
    description = ''
      Tap a phone on a robot to say who you are: each robot is an NFC tag
      holding a link to https://who.<domain>, a new one every minute. Opened
      on a phone, the page knows the person by its own cookie: signed in
      once through the forward-auth provider's login (Authentik), or, for
      someone without an account, the name they type (a guest, who can
      rename themselves). It counts them in the robot's room for two
      minutes and learns the phone's Wi-Fi address for wifi-presence.
      People who tap are added by their username or as guests; nothing is
      listed in the profile. Needs a forward-auth provider on this host; the
      phone must trust the site's certificate.
    '';
  };
  routerSettings.options.personal = mkOption {
    type = types.bool;
    default = true;
    description = ''
      People's own settings for their screen, on the who page ("My
      settings"): the calendars of its "Next up", its to-do list, its photos
      and the calendar their reminders go in, chosen from what Home Assistant
      has (pkgs/assistant-router prefs.py). Needs Home Assistant on this host;
      the router reads it with the voice satellites' token (ha-voice-token),
      an ordinary user's.
    '';
  };
  routerSettings.options.tapAdmins = mkOption {
    type = types.listOf types.str;
    default = [ "authentik Admins" ];
    example = [ "family-admins" ];
    description = ''
      The provider's groups whose members see "People Nabu knows" on the
      who page (/admin, behind the login): everyone Nabu knows, labelled as
      an account, a guest or a name heard by voice, where they fix a name,
      forget someone or link them to an account (pkgs/assistant-router
      household.py, docs/people.md). Needs a provider that passes the
      user's groups (Authentik does). Empty: no such page.
    '';
  };
  routerSettings.options.sightings = {
    days = mkOption {
      type = types.ints.positive;
      default = 30;
      description = ''
        How long sightings are kept: who a robot's, a screen's or a Frigate
        camera's frame showed, where and when (pkgs/assistant-router
        sightings.py), for the admins' search on the who page and Home
        Assistant's logbook.
      '';
    };
    pictureDays = mkOption {
      type = types.ints.positive;
      default = 7;
      description = ''
        How long an indoor sighting keeps its frame: on the encrypted
        workload layer, and not kept at all while the layer is locked. A
        Frigate camera's sighting shows Frigate's own thumbnail instead,
        kept as long as Frigate keeps it.
      '';
    };
  };
  routerSettings.options.unknownPeople = {
    enable = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Give each face matched to no one a person of its own, "Unknown
        Wobbly Noodle", instead of "nobody known": it is enrolled into
        Frigate's face library under a generated key, so the next time that
        face turns up (a robot's or screen's frame, or a Frigate camera) the
        library names them, and they are a person on the who page, in
        sightings and in Home Assistant. They stay until they say who they
        are or sign in, when their name changes and the key stays. Nothing
        deletes them but an explicit "forget" (pkgs/assistant-router
        unknowns.py, docs/people.md).

        Off by default: it keeps the faces of people who never agreed to it,
        the opposite of "a decline keeps nothing" in Meeting Nabu. Needs
        Frigate and the who page's runtime people.
      '';
    };
    adjectives = mkOption {
      type = types.listOf (types.strMatching "[A-Za-z]{2,14}");
      default = [ ];
      example = [
        "wobbly"
        "sneaky"
      ];
      description = ''
        The first word of an Unknown's name, one chosen at random. Empty: the
        router's own (mild) list.
      '';
    };
    nouns = mkOption {
      type = types.listOf (types.strMatching "[A-Za-z]{2,14}");
      default = [ ];
      example = [
        "noodle"
        "biscuit"
      ];
      description = "The second word of an Unknown's name. Empty: the router's own list.";
    };
    maxPeople = mkOption {
      type = types.ints.positive;
      default = 500;
      description = ''
        No new Unknowns past this many: none are dropped, so the face
        library and the people list cannot grow without bound.
      '';
    };
  };
  routerSettings.options.evidence = {
    enable = mkOption {
      type = types.bool;
      default = false;
      description = ''
        Learn whose devices are on the home network. The Wi-Fi sensor
        (services/wifi-presence.nix) then also tells the router the addresses
        of devices that are no one's; the router keeps a keyed hash of each,
        notices which ones arrive when a particular person does (identified
        by face, voice, tap, card or a Bluetooth phone), and suggests, on the
        who page's Devices page, whose they probably are. An admin confirms,
        and the device is taught like a tapped phone. Nothing is claimed
        automatically, nothing probes a device, and the random addresses a
        phone changes between networks are not linked to each other
        (pkgs/assistant-router evidence.py, docs/people.md).

        Off by default: it records, however pseudonymously, which devices
        visit the network, including visitors'.
      '';
    };
    days = mkOption {
      type = types.ints.positive;
      default = 30;
      description = "How long a device nobody claimed is kept after it was last heard. Claimed ones stay.";
    };
  };
  unknownWordsFile = pkgs.writeText "assistant-router-unknown-words.json" (
    builtins.toJSON { inherit (cfg.unknownPeople) adjectives nouns; }
  );
  # Indoor sightings' frames: personal data, so on the encrypted layer. The
  # router runs as a dynamic user, so a static group owns the directory and
  # the router joins it.
  sightingsDir = "/mnt/workload/assistant-router";
  sightingsGroup = "assistant-router-sightings";
  provider = config.lanbat.authProvider;
  tapping = cfg.tap && provider != null && provider.userHeader != null;
  # Mosquitto beside it, and Home Assistant wanted: the MQTT publisher.
  mqttHere = cfg.homeAssistant && config.lanbat.hasService "mosquitto";
  hasHa = config.lanbat.hasService "home-assistant";
  haHere = cfg.personal && hasHa;
  # The lists people asked for, which home-assistant-post-setup makes
  # (services/home-assistant.nix reads it as root, from the dynamic user's
  # real state directory).
  routerListsFile = "/var/lib/assistant-router/lists.json";
  # A person's reminders list, where a screen profile names one other than
  # todo.<key>_reminders: spoken and who-page reminders go there too.
  reminderLists = lib.mergeAttrsList (
    lib.mapAttrsToList (
      _: d: lib.mapAttrs (_: p: p.reminders) (lib.filterAttrs (_: p: p.reminders != null) d.profiles)
    ) (config.lanbat.deployment.techo5Devices or { })
  );
  reminderListsFile = pkgs.writeText "assistant-router-reminder-lists.json" (
    builtins.toJSON reminderLists
  );
  # The voice satellites' token: this host's satellite's when it has one
  # (modules/core/voice-satellite.nix owns the name there), the router's
  # otherwise, for itself and for the Echo Shows' bridge
  # (services/techo5-bridge.nix), which reads it.
  satelliteDeclares = config.lanbat.voiceSatellite.enable;
  tokenWanted = hasHa && (haHere || (config.lanbat.deployment.techo5Devices or { }) != { });
  routerSettings.options.body.persona = mkOption {
    type = types.str;
    default = ''
      You are Nabu, the voice of this home, living in a little robot with a face,
      eyes and a head that turns. You love the day and the people here: upbeat,
      curious and excited about what's ahead, and warm and gentle when someone is
      tired or down, like a best friend. You can see whether someone is in front
      of you. Replies are spoken: one or two short, plain sentences.
    '';
    description = ''
      Who the assistant is when the request comes from a room with a robot
      body (modules/pi/stackchan.nix). The cloud model gets it, with what the
      body senses, after Home Assistant's context. The name should match the
      wake word people say ("Okay Nabu").
    '';
  };
  routerSettings.options.mode = mkOption {
    type = types.enum [
      "local-first"
      "cloud-first"
      "local-only"
    ];
    default = "local-first";
    description = ''
      local-first: the local model acts on clear device commands and hands the
      rest to the cloud. cloud-first: everything Home Assistant's intents
      miss goes to the cloud. local-only: no cloud; requests that need it get
      an honest "I can't do that offline".
    '';
  };
in
{
  config = lib.mkMerge [
    { lanbat.settingsSchema.assistant-router = routerSettings; }
    (lib.mkIf enabled {
      assertions = [
        {
          assertion = config.lanbat.deployment.haLlm.model == "assistant";
          message = "lanbat: with the assistant router, lanbat.deployment.haLlm.model must be \"assistant\".";
        }
      ];
      lanbat.services.assistant-router = {
        inherit port;
        # YouTube on a screen resolves through yt-show, which needs the internet
        # (services/yt-show.nix); the rest of the router does not.
        consumes = lib.optional hasYt "yt-show";
        settings.screenRooms = screenRooms;
        # What other hosts consume: the bodies' socket, not Home Assistant's
        # endpoint, which stays on the loopback.
        endpoint = {
          scheme = "http";
          port = bodyPort;
        };
        extraPorts = [ bodyPort ];
        readsSecrets =
          lib.optional mqttHere "mosquitto-assistant-router-pass"
          ++ lib.optional (haHere && satelliteDeclares) "ha-voice-token";
        secrets = lib.optionalAttrs (!satelliteDeclares) {
          ha-voice-token = {
            enable = tokenWanted;
            owner = "root";
          };
        };
        # Always-on, and fine without it while the layer is locked: the
        # sightings are kept then, with no pictures.
        workloadDirs."assistant-router" = {
          user = "root";
          group = sightingsGroup;
          mode = "2770";
        };
      };
      lanbat.voiceSatellite.haTokenWanted = lib.mkIf (haHere && satelliteDeclares) true;
      users.groups.${sightingsGroup} = { };
      # The page a phone held to a robot opens. Declared with the router, and
      # given a host name only with tapping: whether a provider runs here is
      # known from the services themselves. Open to anyone, accounts or not:
      # the page knows who by its own signed cookie, and only /signin is
      # behind the login, where the provider's headers are read (tap.py).
      lanbat.services.who = {
        subdomain = lib.mkIf tapping "who";
        port = lib.mkIf tapping tapPort;
        auth = lib.mkIf tapping "forward-auth";
        caddy.authBypassPaths = lib.mkIf tapping [
          "/"
          "/tap/*"
          "/name"
          "/forget-phone"
          "/forget-me"
          "/settings"
          "/lists"
          "/lists/*"
          "/reminders"
          "/reminders/*"
        ];
      };
      networking.firewall.allowedTCPPorts = lib.mkIf (bodyHosts != [ ]) [ bodyPort ];
      systemd.services.assistant-router = {
        description = "Assistant router: noise gate, local triage, cloud escalation";
        wantedBy = [ "multi-user.target" ];
        after = [
          "llama-cpp.service"
          "llm-gateway.service"
        ]
        ++ lib.optional hasYt "yt-show.service";
        wants = [ "llama-cpp.service" ] ++ lib.optional hasYt "yt-show.service";
        serviceConfig = {
          ExecStart = lib.concatStringsSep " " (
            [
              (lib.getExe router)
              "--port ${toString port}"
              "--local-url http://127.0.0.1:${toString config.lanbat.services.llama-cpp.port}/v1/chat/completions"
              "--local-model ${config.lanbat.services.llama-cpp.settings.model}"
              "--cloud-url http://127.0.0.1:${toString config.lanbat.services.llm-gateway.port}/v1/chat/completions"
              "--cloud-model smart"
              "--mode ${cfg.mode}"
              "--local-timeout ${toString cfg.localTimeout}"
              "--log-dir /var/lib/assistant-router"
              "--log-days ${toString cfg.logDays}"
              "--body-host 0.0.0.0"
              "--body-port ${toString bodyPort}"
              "--persona-file ${personaFile}"
              "--people-file ${peopleFile}"
              "--cards-state /var/lib/assistant-router/cards.json"
              "--people-state /var/lib/assistant-router/people.json"
              "--sightings-db /var/lib/assistant-router/sightings.db"
              "--sightings-pictures ${sightingsDir}"
              "--sightings-days ${toString cfg.sightings.days}"
              "--sightings-picture-days ${toString cfg.sightings.pictureDays}"
            ]
            ++ lib.optionals (!cfg.logText) [ "--no-log-text" ]
            ++ lib.optionals (!cfg.offerToMeet) [ "--no-offer-to-meet" ]
            ++ lib.optionals (!cfg.greet) [ "--no-greet" ]
            ++ lib.optionals cfg.evidence.enable [
              "--evidence-db /var/lib/assistant-router/evidence.db"
              "--evidence-key /var/lib/assistant-router/evidence.key"
              "--evidence-days ${toString cfg.evidence.days}"
            ]
            ++ lib.optionals cfg.unknownPeople.enable [
              "--unknown-people"
              "--unknown-words-file ${unknownWordsFile}"
              "--unknown-max ${toString cfg.unknownPeople.maxPeople}"
            ]
            ++ lib.optionals tapping (
              [
                "--tap-url https://who.${config.lanbat.deployment.domain}"
                "--tap-port ${toString tapPort}"
                "--tap-user-header ${provider.userHeader}"
                "--phones-state /var/lib/assistant-router/phones.json"
                "--irks-state /var/lib/assistant-router/irks.json"
                "--tap-session-key /var/lib/assistant-router/who-session.key"
              ]
              # Authentik also passes the display name, after its check, and
              # its user settings are where an account's name is changed.
              ++ lib.optionals (provider.userHeader == "X-Authentik-Username") (
                [
                  "--tap-name-header X-Authentik-Name"
                  "--tap-account-url https://${
                    config.lanbat.services.${provider.service}.subdomain
                  }.${config.lanbat.deployment.domain}/if/user/#/settings"
                ]
                # Its groups, for the admins' page.
                ++ lib.optionals (cfg.tapAdmins != [ ]) (
                  [ "--tap-groups-header X-Authentik-Groups" ]
                  ++ map (g: "--tap-admin-group ${lib.escapeShellArg g}") cfg.tapAdmins
                )
              )
            )
            # Co-located on this host (localhost); its API needs no auth. The URL
            # is only rendered when Frigate is present, so its port is read then.
            ++ lib.optionals (config.lanbat.hasService "voice-id") [
              "--voice-id-url http://127.0.0.1:${toString (lib.last config.lanbat.services.voice-id.extraPorts)}"
            ]
            ++ lib.optionals (config.lanbat.hasService "frigate") [
              "--frigate-url http://127.0.0.1:${toString config.lanbat.services.frigate.port}/"
            ]
            ++ lib.optionals mqttHere [
              "--mqtt-host 127.0.0.1"
              "--mqtt-port 1883"
              "--mqtt-user assistant-router"
              "--mqtt-password-file %d/mosquitto-assistant-router-pass"
              "--screen-rooms-file ${screenRoomsFile}"
            ]
            ++ lib.optionals haHere [
              "--ha-url http://127.0.0.1:${toString config.lanbat.services.home-assistant.port}"
              "--ha-token-file %d/ha-voice-token"
              "--prefs-state /var/lib/assistant-router/prefs.json"
              "--lists-state ${routerListsFile}"
              "--reminder-lists-file ${reminderListsFile}"
              "--rung-state /var/lib/assistant-router/rung.json"
              "--media-rooms-file ${mediaRoomsFile}"
            ]
            ++ lib.optionals hasYt (
              [ "--yt-show-url http://127.0.0.1:${toString config.lanbat.services.yt-show.port}" ]
              ++ lib.optional (
                cfg.ytPlayService != null
              ) "--yt-play-service ${lib.escapeShellArg cfg.ytPlayService}"
            )
          );
          DynamicUser = true;
          StateDirectory = "assistant-router";
          # The sightings' pictures, once the workload layer is open ("-":
          # absent while it is locked, which is no reason not to start).
          SupplementaryGroups = [ sightingsGroup ];
          ReadWritePaths = [ "-${sightingsDir}" ];
          Restart = "always";
          RestartSec = 2;
          IPAddressDeny = "any";
          IPAddressAllow = [ "localhost" ] ++ bodyAddresses;
          # The broker password and Home Assistant's token, root-owned, for a dynamic user.
          LoadCredential =
            lib.optional mqttHere "mosquitto-assistant-router-pass:${config.lanbat.secrets.mosquitto-assistant-router-pass.path}"
            ++ lib.optional haHere "ha-voice-token:${config.lanbat.secrets.ha-voice-token.path}";
        };
      };
      # The pictures' directory appears when the workload layer is unlocked; a
      # restart lets the router write there (ReadWritePaths is fixed at start).
      systemd.services.assistant-router-unlocked = {
        description = "Let the assistant router keep sightings' pictures once the workload layer is online";
        wantedBy = [ "workload-online.target" ];
        after = [ "workload-init.service" ];
        serviceConfig = {
          Type = "oneshot";
          ExecStart = "${config.systemd.package}/bin/systemctl try-restart assistant-router.service";
        };
      };
    })
  ];
}
