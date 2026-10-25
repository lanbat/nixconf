# services/home-assistant.nix
#
# Home Assistant — home automation hub.
#
# Why the NixOS module and not a container?
#   The NixOS `services.home-assistant` module handles component packaging,
#   config dir, user/group, and service lifecycle very cleanly.
#   It is significantly easier to maintain than a container for HA specifically
#   because NixOS can manage the Python component set declaratively.
#
# Zigbee
# ------
# Zigbee devices are bridged via Zigbee2MQTT (see zigbee2mqtt.nix).
# Z2M owns the USB dongle and publishes to Mosquitto; HA discovers devices
# via MQTT auto-discovery.  Do NOT add ZHA here — it would conflict with Z2M.
# settings.zigbee2mqttBridge (on by default when Zigbee2MQTT runs on this host)
# adds a notification while the bridge is offline and its card on the Overview
# dashboard.
#
# Other services
# --------------
# The loopback URLs of Home Assistant itself, Frigate, Music Assistant and the
# MQTT port come from those services' descriptions (lanbat.services.<name>.port
# and the Mosquitto endpoint), not literals.
#
# Auth with Authentik
# -------------------
# HA's own login page hands over to Authentik over OpenID Connect (the
# auth_oidc custom component); Caddy proxies HA without forward auth. Browsers
# and the companion apps use the same hostname: the apps sign in with a device
# code that is entered in the phone's browser. An Authentik username links to
# the existing HA user of the same name, which the bootstrap provisions from
# ssoUsers; no new HA users are created.
#
# API clients authenticate with HA long-lived tokens as usual. HA's password
# login stays, at ?skip_oidc_redirect=true, for the owner and the loopback
# setup scripts.
#
# First-run onboarding is completed automatically by home-assistant-bootstrap
# (owner account + SSO user mirror).
#
# Bluetooth
# ---------
# A USB Bluetooth adapter on the server carries BLE sensors.  hardware.bluetooth
# enables BlueZ; the "bluetooth", "xiaomi_ble" and "bthome" components let HA
# scan and decode the advertisements.
#
# home-assistant-post-setup also writes the bluetooth integration's own config
# entry, one per adapter.  Home Assistant creates those by itself only during
# onboarding, so on an instance onboarded before the adapter existed it would
# otherwise wait for a click in the UI and never scan.
#
# Stock-firmware Xiaomi models such as the LYWSD02MMC clock broadcast encrypted
# and need a per-device bind key.  With lanbat.deployment.haXiaomiBle set, those
# keys live in ha-xiaomi-ble.age and home-assistant-post-setup writes one
# xiaomi_ble config entry per device, so a key is never typed into the UI and
# never reaches the Nix store.  Obtain one locally from
# atc1441.github.io/Temp_universal_mi_activate.html; the Xiaomi cloud is not
# involved.  (The LYWSD02MMC has no Telink OTA service, so custom firmware would
# need a wired programmer; the bind key is the practical route.)
#
# Sensors reflashed with pvvx firmware broadcast BTHome v2 in the clear and need
# no key; "bthome" covers those.  Anything else is discovered in the UI.
#
# Note: the adapter shares 2.4 GHz with the Zigbee coordinator.  Keep the two
# dongles physically apart or both will degrade.
#
# Voice
# -----
# home-assistant-post-setup adds the Wyoming services and satellites
# (services/wyoming.nix) and makes a "Voice" pipeline the preferred one:
# openWakeWord, faster-whisper, piper, and as the conversation agent the LLM
# in lanbat.haLlm, or Home Assistant's own agent without one. Local intents
# are tried first, so simple commands don't wait for the LLM.
#
# Always-on: yes — HA should survive Pi NFS loss.
{
  config,
  pkgs,
  lib,
  ...
}:

let
  domain = config.lanbat.deployment.domain;
  # Called from HA's own Python package set, which provides its dependencies,
  # as nixpkgs calls home-assistant-custom-components.
  authOidcComponent =
    pkgs.home-assistant.python.pkgs.callPackage ../pkgs/home-assistant-auth-oidc
      { };
  oidcSecret = pkgs.callPackage ../pkgs/home-assistant-oidc-secret { };
  bootstrap = pkgs.callPackage ../pkgs/home-assistant-bootstrap { };
  postSetup = pkgs.callPackage ../pkgs/home-assistant-post-setup { };
  haDashboards = pkgs.callPackage ../pkgs/home-assistant-dashboards { };
  # The link buttons on the generated System and Cameras dashboards: the web
  # UIs of the services on this profile, from their descriptions.
  dashboardLinks = builtins.toJSON (
    lib.listToAttrs (
      lib.concatMap
        (
          { name, title }:
          let
            svc = config.lanbat.services.${name} or null;
          in
          lib.optional (config.lanbat.hasService name && svc.subdomain != null) {
            name = title;
            value = "https://${svc.subdomain}.${config.lanbat.deployment.domain}";
          }
        )
        [
          {
            name = "grafana";
            title = "Grafana";
          }
          {
            name = "frigate";
            title = "Frigate";
          }
          {
            name = "music-assistant";
            title = "Music Assistant";
          }
          {
            name = "zigbee2mqtt";
            title = "Zigbee2MQTT";
          }
          {
            name = "homepage";
            title = "Homepage";
          }
        ]
    )
  );
  llm = config.lanbat.deployment.haLlm;
  # An LLM on this host's loopback (services/llama-cpp.nix) needs no API key,
  # and no keepalive to hold off a scale-to-zero cold start.
  llmLocal = (import ../lib/host.nix { inherit lib; }).haLlmIsLocal llm;
  # The agent behind the assistant router (services/assistant-router.nix) gets
  # the router's context block as its prompt, and tools.
  llmRouter = (import ../lib/host.nix { inherit lib; }).haLlmIsRouter llm;
  llmKey = llm != null && (llm.apiKey or (!llmLocal));
  # Where the heavy voice work runs (lib/voice-compute.nix).
  voiceCompute = (import ../lib/voice-compute.nix { inherit lib; }).forConfig config;
  voiceComputeProfile = config.lanbat.deployment.voiceCompute.profile or "low-spec";
  llmComponent = pkgs.callPackage ../pkgs/home-assistant-extended-openai-conversation { };
  satellite = config.lanbat.voiceSatellite;
  piper = config.services.wyoming.piper.servers.main;
  # A satellite with a room hands its replies to the voice_reply script.
  voiceRooms = config.lanbat.deployment.voiceRooms != { };
  xiaomiBle = config.lanbat.deployment.haXiaomiBle;
  # The house's Google Calendar, from the iCal feed URL in ha-gcal-url.age.
  haCalendar = config.lanbat.deployment.haCalendar;
  # The storage Pi's satellite, reached the way policy on the Pi admits Home
  # Assistant: over the overlay when the profile runs one, the LAN otherwise.
  storageKey = config.lanbat.deployment.primaryStorage;
  # Every voice-pi host's satellite too, as "<host key>=<address>".
  hostLib = import ../lib/host.nix { inherit lib; };
  extraSatellites = map (key: "${key}=${config.lanbat.endpointHost "voice-satellite" key}") (
    hostLib.hostsWithRole config.lanbat.hosts "voice-pi"
  );
  piHost =
    if storageKey == null then
      config.lanbat.deployment.storageIp
    else
      config.lanbat.endpointHost "voice-satellite" storageKey;

  voiceSatelliteEntry = config.lanbat.endpoints.voice-satellite or null;
  voiceSatelliteHosts = voiceSatelliteEntry.hosts or [ ];
  voiceSatelliteRegistrations = voiceSatelliteEntry.registrations or { };
  voiceSatellitePort = voiceSatelliteEntry.endpoint.port or 10700;
  # Hosts running Kodi (the lanbat-tv plugin), found from the deploy entries
  # rather than the endpoint table: consumes below must not depend on it.
  kodiHosts = lib.filter (
    hostKey: lib.elem "lanbat-tv" (config.lanbat.hosts.${hostKey}.pluginNames or [ ])
  ) (lib.attrNames config.lanbat.hosts);
  hasKodi = kodiHosts != [ ];
  # One line per Kodi: "hostKey|address|room|hostname" for post-setup, room
  # being its host's voiceRooms room (empty outside one).
  kodiHostsEnv = lib.concatStringsSep "\n" (
    map (
      hostKey:
      let
        room = lib.defaultTo "" (hostLib.voiceRoomForHost config.lanbat.deployment.voiceRooms hostKey);
      in
      "${hostKey}|${config.lanbat.endpointHost "kodi" hostKey}|${room}|${
        config.lanbat.hosts.${hostKey}.networking.hostname or hostKey
      }"
    ) kodiHosts
  );

  # Android TV boxes (modules/server/android-devices.nix) that Home Assistant
  # controls over ADB with the provisioning key, with their rooms and apps.
  androidTvs = lib.filterAttrs (_: d: d.enable) (config.androidDevices or { });
  hasAndroidTv = androidTvs != { };
  androidTvsJson = builtins.toJSON (
    lib.mapAttrsToList (name: d: {
      inherit name;
      inherit (d) host port apps;
      room = if d.room == null then "" else d.room;
    }) androidTvs
  );

  # Echo Shows on TECHO5 (services/techo5-bridge.nix): ESPHome devices with an
  # encryption key, added here and put in their rooms.
  techo5Devices = config.lanbat.deployment.techo5Devices;
  hasTechos = techo5Devices != { } && config.lanbat.hasService "techo5-bridge";
  techo5Json = builtins.toJSON (
    lib.mapAttrsToList (key: d: {
      title = "TECHO5 ${d.room}";
      host = d.address;
      inherit (d) room;
      key_file = config.lanbat.secrets."techo5-${key}-api-key".path;
      player = if d.playerName == null then "" else d.playerName;
      inherit (d) dlna;
    }) techo5Devices
  );

  techo5ProfileListEntity =
    person: kind: p:
    if kind == "todo" then
      if p.todo != null then p.todo else "todo.${person}_todo"
    else if p.reminders != null then
      p.reminders
    else
      "todo.${person}_reminders";

  # Default local_todo list titles (entity object id) for post-setup to create:
  # for each profile's person, and each person the profile declares (the bridge
  # gives anyone it shows without a profile of their own the default lists).
  techo5ListPeople =
    d:
    lib.mapAttrs (_: _: {
      todo = null;
      reminders = null;
    }) (config.lanbat.deployment.people or { })
    // d.profiles;
  techo5TodoLists = lib.unique (
    lib.concatLists (
      lib.mapAttrsToList (
        _: d:
        lib.concatLists (
          lib.mapAttrsToList (
            person: p:
            let
              todo = techo5ProfileListEntity person "todo" p;
              reminders = techo5ProfileListEntity person "reminders" p;
            in
            lib.optional (todo == "todo.${person}_todo") (lib.removePrefix "todo." todo)
            ++ lib.optional (reminders == "todo.${person}_reminders") (lib.removePrefix "todo." reminders)
          ) (techo5ListPeople d)
        )
      ) techo5Devices
    )
  );

  # The assistant router's lists.json (pkgs/assistant-router lists.py): where
  # root reads the lists people asked for, in the router's real state directory.
  personalLists =
    config.lanbat.hasService "assistant-router"
    && config.lanbat.services.assistant-router.settings.personal
    && hostLib.haLlmIsRouter (config.lanbat.deployment.haLlm or null);
  routerListsPath = "/var/lib/private/assistant-router/lists.json";

  hasLvaSatellite = lib.any (
    hostKey: (voiceSatelliteRegistrations.${hostKey}.backend or "wyoming") == "lva"
  ) voiceSatelliteHosts;
  voiceSatelliteRegistrationLine =
    hostKey:
    let
      reg =
        voiceSatelliteRegistrations.${hostKey} or {
          backend = "wyoming";
          displayName = hostKey;
        };
      # This host's own satellite: Wyoming listens on the loopback, LVA on the
      # LAN address only.
      address =
        if hostKey != config.lanbat.hostKey then
          config.lanbat.endpointHost "voice-satellite" hostKey
        else if reg.backend == "lva" then
          config.lanbat.hosts.${hostKey}.networking.ip
        else
          "127.0.0.1";
      # The Home Assistant area the satellite and its host's Snapcast player
      # belong in (lanbat.deployment.voiceRooms), and the host name the Music
      # Assistant player carries.
      room = lib.defaultTo "" (hostLib.voiceRoomForHost config.lanbat.deployment.voiceRooms hostKey);
      hostname = config.lanbat.hosts.${hostKey}.networking.hostname or hostKey;
    in
    "${hostKey}|${address}|${toString voiceSatellitePort}|${reg.backend}|${reg.displayName}|${room}|${hostname}";
  # One line per satellite: names contain spaces.
  voiceSatelliteRegistrationsEnv = lib.concatStringsSep "\n" (
    map voiceSatelliteRegistrationLine voiceSatelliteHosts
  );

  # Home Assistant on the loopback: what Music Assistant and the local tools
  # call, rather than the public URL behind forward auth.
  internalUrl = "http://127.0.0.1:${toString config.lanbat.services.home-assistant.port}";

  # The web port of another service on this host, from its description.
  localUrl = name: path: "http://127.0.0.1:${toString config.lanbat.services.${name}.port}${path}";

  cfg = config.lanbat.services.home-assistant.settings;

  homeAssistantSettings = {
    options.zigbee2mqttBridge = lib.mkOption {
      type = lib.types.bool;
      default = config.lanbat.hasService "zigbee2mqtt";
      defaultText = lib.literalExpression ''config.lanbat.hasService "zigbee2mqtt"'';
      description = ''
        Watch the Zigbee2MQTT bridge: a persistent notification while it has
        lost its MQTT connection. (The generated System dashboard shows its
        state and permit-join switch whenever the bridge is in Home
        Assistant.) The default follows whether Zigbee2MQTT runs on this host.
      '';
    };
    options.deviceAreas = lib.mkOption {
      type = lib.types.attrsOf lib.types.str;
      default = { };
      example = {
        "livingroom_lamp" = "Living Room";
        "switch.0x00124b0012345678" = "Hall";
      };
      description = ''
        Rooms for devices, set by home-assistant-post-setup on every run: a
        device's name as Home Assistant shows it (or the id of one of its
        entities, for devices whose names repeat) → the name of its area. An
        area that does not exist yet is created. The generated dashboards
        (pkgs/home-assistant-dashboards) group by these rooms.
      '';
    };
  };

  # Playing anything by voice on the room's speaker, without the LLM.
  #
  # Home Assistant's own "play {name}" sentence only takes its fixed patterns
  # and picks a player by name; these sentence triggers (which win over the
  # built-in intents) take everyday phrasing and play on the Music Assistant
  # player in the area of the satellite that heard the request, or in an area
  # named at the end ("... in the living room"). Music Assistant searches for
  # the name across its providers (radio stations, podcasts, Audiobookshelf,
  # music); a word in the request narrows the kind:
  #
  #   "play BBC Radio 4", "put on Massive Attack", "I want to listen to jazz"
  #   "play the radio <station>", "play <station> radio", "tune in to <station>"
  #   "play the podcast <name>", "play <name> podcast"
  #   "play the audiobook <title>", "play the album/artist/song/playlist <name>"
  #   "play something like <artist>" (Music Assistant's radio mode: similar
  #   music, endlessly)
  #   "stop the music", "turn off the radio"
  # Kind words other than radio, and how each may be taken off the name: any
  # of them as "the <word> <name>" or "<name> <word>", and the ones no name
  # starts with also as "<word> <name>" ("song 2" is a song).
  voicePlayKinds = {
    podcast = "podcast";
    audiobook = "audiobook";
    album = "album";
    artist = "artist";
    song = "track";
    track = "track";
    playlist = "playlist";
  };
  voicePlayWords = lib.concatStringsSep "|" (lib.attrNames voicePlayKinds);
  voicePlayLeadingWords = "podcast|audiobook|album|artist|playlist";

  # Satellite device name -> the host name its Music Assistant player carries
  # (the player is the Snapcast client of the same host). Two satellites can
  # share a room (the server's and a Pi's in the Office), and each room has
  # one player per host, so "the room's player" alone picked the same one for
  # both: a command to the Pi 3 changed the server's volume.
  satellitePlayerHosts = builtins.toJSON (
    lib.listToAttrs (
      map (hostKey: {
        name = (voiceSatelliteRegistrations.${hostKey} or { displayName = hostKey; }).displayName;
        value = config.lanbat.hosts.${hostKey}.networking.hostname or hostKey;
      }) voiceSatelliteHosts
    )
  );

  # The host name of the satellite that heard the request, "" when it is not
  # one of ours (typed in the UI) or has no player.
  ownHostTemplate = ''
    {%- set m = satellite_hosts | from_json -%}
    {%- set d = trigger.device_id -%}
    {{ m.get(device_attr(d, 'name_by_user') or "", "") or m.get(device_attr(d, 'name') or "", "") }}'';

  # The Music Assistant player to use in the area: the one of the satellite's
  # own host when it is in the area, else the first one there (a room named in
  # the request, or a satellite whose host has no player). Unavailable players
  # are left out: a satellite whose host has no music player (the server's,
  # lanbat.voiceSatellite.playMusic = false) still has an unavailable player in
  # its area, and picking it made play/volume fail with "Player ... is not
  # available" instead of falling back to the room's real speaker.
  roomPlayerTemplate = ''
    {%- set room = area_entities(area) if area else [] -%}
    {%- set ma = integration_entities('music_assistant')
                 | select('match', 'media_player\\.') | select('in', room)
                 | reject('is_state', ['unavailable', 'unknown']) | list -%}
    {%- set ns = namespace(own="") -%}
    {%- for p in ma -%}
      {%- if not ns.own and own_host
            and own_host in [device_attr(p, 'name_by_user'), device_attr(p, 'name')] -%}
        {%- set ns.own = p -%}
      {%- endif -%}
    {%- endfor -%}
    {{ ns.own or (ma | first | default("")) }}'';

  # The request with any room named at the end taken off, the area (named or
  # the satellite's), the kind of media and the bare name to search for. Each
  # uses the ones before it, and Home Assistant evaluates them in order, which
  # a Nix attribute set (sorted by name) would not keep: voicePlaySteps turns
  # them into one variables step each, in voicePlayOrder.
  voicePlayVariables = {
    raw = "{{ trigger.slots.query | default('') | trim }}";
    sentence = "{{ trigger.sentence | lower }}";
    named_area = ''
      {%- set ns = namespace(id="") -%}
      {%- for a in areas() -%}
        {%- set n = area_name(a) | lower -%}
        {%- if (raw | lower).endswith(' in the ' ~ n) or (raw | lower).endswith(' in ' ~ n) -%}
          {%- set ns.id = a -%}
        {%- endif -%}
      {%- endfor -%}
      {{ ns.id }}'';
    area = "{{ named_area if named_area else (area_id(trigger.device_id) or '') }}";
    request = ''
      {%- if named_area -%}
        {%- set n = area_name(named_area) | lower -%}
        {%- set cut = (' in the ' ~ n) if (raw | lower).endswith(' in the ' ~ n) else (' in ' ~ n) -%}
        {{ raw[:(raw | length) - (cut | length)] }}
      {%- else -%}{{ raw }}{%- endif -%}'';
    kinds = builtins.toJSON voicePlayKinds;
    # Radio is any request with the word in it ("Radio 1", "Absolute Radio")
    # or "tune in to ...", and keeps the station's name whole.
    kind = ''
      {%- set k = kinds | from_json -%}
      {%- set r = request | lower -%}
      {%- set ns = namespace(kind="") -%}
      {%- if sentence.startswith('tune in') or r is search('\\bradio\\b') -%}{%- set ns.kind = 'radio' -%}{%- endif -%}
      {%- for word, value in k.items() -%}
        {%- if not ns.kind and (r.startswith('the ' ~ word ~ ' ') or r.endswith(' ' ~ word)
              or (word in '${voicePlayLeadingWords}'.split('|') and r.startswith(word ~ ' '))) -%}
          {%- set ns.kind = value -%}
        {%- endif -%}
      {%- endfor -%}
      {{ ns.kind }}'';
    similar = "{{ (request | lower) is match('(something|music|songs|stuff) like ') }}";
    query = ''
      {%- set q = request | regex_replace('(?i)^(something|music|songs|stuff) like\\s+', "") -%}
      {%- if kind == 'radio' -%}
        {{ q | regex_replace('(?i)^(the\\s+)?radio\\s+station\\s+|^the\\s+radio\\s+', "") | trim }}
      {%- else -%}
        {{ q | regex_replace('(?i)^the\\s+(${voicePlayWords})\\s+|^(${voicePlayLeadingWords})\\s+', "")
             | regex_replace('(?i)\\s+(${voicePlayWords})$', "") | trim }}
      {%- endif -%}'';
    # "La Isla Bonita by Madonna": a track by that artist; "something by
    # Madonna" (or a song, music, anything): the artist herself.
    by_artist = "{{ query | regex_findall('(?i)^.+?\\s+by\\s+(.+)$') | first | default(\"\") }}";
    by_title = "{{ query | regex_replace('(?i)\\s+by\\s+.+$', \"\") if by_artist else \"\" }}";
    play_type = ''
      {%- set generic = ['something', 'anything', 'a song', 'some songs', 'songs', 'music', 'some music', 'a track', 'tracks'] -%}
      {%- if by_artist and (by_title | lower) in generic -%}artist
      {%- elif by_artist -%}track
      {%- else -%}{{ kind }}{%- endif -%}'';
    play_id = "{{ by_artist if (by_artist and play_type == 'artist') else (by_title if by_artist else query) }}";
    play_artist = "{{ by_artist if play_type == 'track' else \"\" }}";
    satellite_hosts = satellitePlayerHosts;
    own_host = ownHostTemplate;
    player = roomPlayerTemplate;
    place = "{{ 'in the ' ~ area_name(area) if named_area else 'in here' }}";
  };

  # "stop the music [in the <room>]": the same area lookup, every player in it.
  voicePlayStopVariables = voicePlayVariables // {
    raw = "{{ 'x in ' ~ trigger.slots.query if trigger.slots.query is defined else '' }}";
    # And a TV in the room (Kodi, an Android TV box) with something on it.
    players = ''
      {%- set room = area_entities(area) if area else [] -%}
      {%- set tvs = (integration_entities('kodi') + integration_entities('androidtv'))
            | select('match', 'media_player\\.') | select('in', room)
            | select('is_state', ['playing', 'paused']) | list -%}
      {{ integration_entities('music_assistant')
         | select('match', 'media_player\\.')
         | select('in', room)
         | reject('is_state', ['unavailable', 'unknown'])
         | list + tvs }}'';
  };

  # "volume to 70 percent", "louder", "turn the music down", "increase the
  # volume by 20 percent", "volume up by 30": the room's Music Assistant player,
  # which is the music's volume. Home Assistant's own volume sentences need a
  # player's name and, without one, fell through to the LLM, which can't set a
  # volume. A phrasing none of these sentences matches ("volume up by 30%" did
  # not, until 2026-10-22) is answered "Volume set" by Home Assistant's own
  # handler, which moved another player and left a film's volume alone.
  voiceVolumeVariables = {
    inherit (voicePlayVariables)
      sentence
      named_area
      area
      place
      satellite_hosts
      own_host
      ;
    # Kodi's own volume while it has something on (a film paused for the
    # question counts), else the music player of the satellite that heard it,
    # else the room's first.
    player = ''
      {%- set room = area_entities(area) if area else [] -%}
      {%- set kodi = integration_entities('kodi') | select('match', 'media_player\\.') | select('in', room)
                     | select('is_state', ['playing', 'paused']) | first | default("", true) -%}
      {%- if kodi -%}{{ kodi }}{%- else -%}${roomPlayerTemplate}{%- endif -%}'';
    raw = "{{ \"\" }}";
    level = "{{ (trigger.slots.level | default(\"\") | regex_findall('\\d+') | first | default(\"\")) }}";
  };

  voicePlayOrder = [
    "raw"
    "sentence"
    "named_area"
    "area"
    "satellite_hosts"
    "own_host"
    "request"
    "kinds"
    "kind"
    "similar"
    "query"
    "by_artist"
    "by_title"
    "play_type"
    "play_id"
    "play_artist"
    "player"
    "players"
    "place"
    "level"
    "video_kind"
    "video_title"
    "tv"
    "tvs"
    "kodi"
    "androids"
    "app"
  ];
  voicePlaySteps =
    vars:
    map (name: { variables.${name} = vars.${name}; }) (
      lib.filter (name: vars ? ${name}) voicePlayOrder
    );

  voiceVolumeAutomation = {
    alias = "Voice: the room's speaker volume";
    id = "lanbat_voice_volume";
    mode = "parallel";
    trigger = [
      {
        platform = "conversation";
        id = "set";
        command = [
          "[set] [the] volume to {level}"
          "(set|turn) the (volume|music|radio) to {level}"
          "(increase|raise|decrease|lower|reduce) the volume to {level}"
        ];
      }
      {
        platform = "conversation";
        id = "up";
        command = [
          "(increase|raise) [the] volume [by {level}]"
          "turn (it|the volume|the music|the radio) up [by {level}]"
          "turn up the (volume|music|radio) [by {level}]"
          "[make it] louder [by {level}] [please]"
          "volume up [by {level}]"
        ];
      }
      {
        platform = "conversation";
        id = "down";
        command = [
          "(decrease|lower|reduce) [the] volume [by {level}]"
          "turn (it|the volume|the music|the radio) down [by {level}]"
          "turn down the (volume|music|radio) [by {level}]"
          "[make it] quieter [by {level}] [please]"
          "volume down [by {level}]"
        ];
      }
    ];
    action = voicePlaySteps voiceVolumeVariables ++ [
      {
        "if" = [
          {
            condition = "template";
            value_template = "{{ not player or (trigger.id == 'set' and not level) }}";
          }
        ];
        "then" = [
          {
            set_conversation_response = "{{ 'There is no speaker ' ~ place if not player else 'To what level?' }}";
          }
        ];
        "else" = [
          {
            variables.target = ''
              {%- set now = state_attr(player, 'volume_level') | float(0.5) -%}
              {%- set step = (level | int / 100) if level else 0.15 -%}
              {%- if trigger.id == 'set' -%}{{ [[level | int / 100, 0.02] | max, 1.0] | min }}
              {%- elif trigger.id == 'up' -%}{{ [now + step, 1.0] | min }}
              {%- else -%}{{ [now - step, 0.02] | max }}{%- endif -%}'';
          }
          {
            service = "media_player.volume_set";
            target.entity_id = "{{ player }}";
            data.volume_level = "{{ target | float }}";
          }
          { set_conversation_response = "Volume {{ (target | float * 100) | round | int }} percent."; }
        ];
      }
    ];
  };

  # Films and TV episodes from the room's Kodi library (a TV box with the
  # lanbat-tv plugin). "watch <title>", "play the movie <title>", "play the
  # (next) episode of <show>", "play the show <show>": the request is a video
  # by its words, and plays on the Kodi in the satellite's room (or the room
  # named) rather than on the music player. A film is looked for first, then a
  # show, whose next unwatched episode plays.
  voiceVideoVariables = {
    video_kind = ''
      {%- set r = request | lower -%}
      {%- if r is match('(the\\s+)?(movie|film)\\s+') or r is search('\\s(movie|film)$') -%}movie
      {%- elif r is match('(the\\s+)?((next|new|latest)\\s+)?episodes?\\s+of\\s+')
            or r is match('(the\\s+)?(tv\\s+)?(show|series)\\s+') or r is search('\\s(tv show|show|series)$') -%}show
      {%- elif sentence is match('(i want to |i.d like to |let.s |let me |can i |can we )?watch ') -%}any
      {%- endif -%}'';
    video_title = ''
      {{ request
         | regex_replace('(?i)^(the\\s+)?(((next|new|latest)\\s+)?episodes?\\s+of|movie|film|(tv\\s+)?show|series)\\s+', "")
         | regex_replace('(?i)\\s+(movie|film|tv show|show|series)$', "") | trim }}'';
    tv = ''
      {{ integration_entities('kodi')
         | select('match', 'media_player\\.')
         | select('in', area_entities(area) if area else [])
         | first | default("") }}'';
  };

  # One Kodi JSON-RPC call whose result the steps after it read as
  # wait.trigger.event.data.result. kodi.call_method only reports its result as
  # an event, fired before the call returns, so a wait after the call would
  # miss it: the call is made by lanbat_kodi_call (below) on an event, and
  # this waits for its result.
  kodiCall = method: params: [
    {
      event = "lanbat_kodi_call";
      event_data = {
        entity_id = "{{ tv }}";
        inherit method params;
      };
    }
    {
      wait_for_trigger = [
        {
          platform = "event";
          event_type = "kodi_call_method_result";
          event_data.entity_id = "{{ tv }}";
        }
      ];
      timeout = "00:00:08";
      continue_on_timeout = true;
    }
  ];
  # The item of a library list named exactly as asked, else the first.
  kodiPick = list: ''
    {%- set items = (wait.trigger.event.data.result.${list} | default([])) if wait.trigger else [] -%}
    {%- set ns = namespace(exact=[]) -%}
    {%- for i in items if (i.label | lower) == (video_title | lower) -%}{%- set ns.exact = ns.exact + [i] -%}{%- endfor -%}
    {{ (ns.exact + items) | first | default({}) }}'';
  reply = text: { set_conversation_response = text; };
  ifThen = test: thenSteps: elseSteps: {
    "if" = [
      {
        condition = "template";
        value_template = test;
      }
    ];
    "then" = thenSteps;
    "else" = elseSteps;
  };

  voiceShowSteps =
    kodiCall "VideoLibrary.GetTVShows" {
      filter = {
        field = "title";
        operator = "contains";
        value = "{{ video_title }}";
      };
      limits = {
        start = 0;
        end = 10;
      };
    }
    ++ [
      { variables.show = kodiPick "tvshows"; }
      (ifThen "{{ not show }}" [ (reply "I couldn't find {{ video_title }} in the library.") ] (
        kodiCall "VideoLibrary.GetEpisodes" {
          tvshowid = "{{ show.tvshowid }}";
          properties = [
            "season"
            "episode"
            "title"
          ];
          # The first unwatched episode, specials (season 0) aside.
          filter.and = [
            {
              field = "playcount";
              operator = "is";
              value = "0";
            }
            {
              field = "season";
              operator = "greaterthan";
              value = "0";
            }
          ];
          sort = {
            method = "episode";
            order = "ascending";
          };
          limits = {
            start = 0;
            end = 1;
          };
        }
        ++ [
          {
            variables.episode = "{{ ((wait.trigger.event.data.result.episodes | default([])) if wait.trigger else []) | first | default({}) }}";
          }
          (ifThen "{{ not episode }}"
            [ (reply "You've seen every episode of {{ show.label }}.") ]
            [
              {
                service = "media_player.play_media";
                target.entity_id = "{{ tv }}";
                data = {
                  media_content_type = "episode";
                  media_content_id = "{{ episode.episodeid }}";
                };
              }
              (reply "Playing {{ show.label }}, season {{ episode.season }} episode {{ episode.episode }}: {{ episode.title }}.")
            ]
          )
        ]
      ))
    ];

  voiceVideoSteps = [
    (ifThen "{{ not tv }}"
      [ (reply "There's no Kodi {{ place }}.") ]
      [
        (ifThen "{{ video_kind == 'show' }}" voiceShowSteps (
          kodiCall "VideoLibrary.GetMovies" {
            filter = {
              field = "title";
              operator = "contains";
              value = "{{ video_title }}";
            };
            limits = {
              start = 0;
              end = 10;
            };
          }
          ++ [
            { variables.movie = kodiPick "movies"; }
            (ifThen "{{ not movie }}"
              [
                (ifThen "{{ video_kind == 'any' }}" voiceShowSteps [
                  (reply "I couldn't find the film {{ video_title }} in the library.")
                ])
              ]
              [
                {
                  service = "media_player.play_media";
                  target.entity_id = "{{ tv }}";
                  data = {
                    media_content_type = "movie";
                    media_content_id = "{{ movie.movieid }}";
                  };
                }
                (reply "Playing {{ movie.label }}.")
              ]
            )
          ]
        ))
      ]
    )
  ];

  # Every app name the Android TV boxes open by voice, longest first so that
  # "YouTube Music" is not taken for "YouTube".
  androidApps = lib.sort (a: b: lib.stringLength a > lib.stringLength b) (
    lib.unique (lib.concatMap (d: lib.attrNames d.apps) (lib.attrValues androidTvs))
  );

  # The TV in the satellite's room: power, pause and resume, and opening an
  # app on an Android TV box. Kodi's "turn on/off" becomes CEC through the TV
  # box (lanbat_kodi_power below and pkgs/lva-kodi-companion); an Android box
  # takes its power key. Pause and resume act on whatever plays in the room,
  # music included, and pausing tells a Kodi that the companion paused for the
  # question to stay paused.
  voiceTvVariables = {
    inherit (voicePlayVariables)
      sentence
      named_area
      area
      place
      ;
    raw = "{{ \"\" }}";
    tvs = ''
      {{ (integration_entities('kodi') + integration_entities('androidtv'))
         | select('match', 'media_player\\.')
         | select('in', area_entities(area) if area else []) | list }}'';
    # The room's Kodi with a film or show on (one paused for the question
    # counts), for seeking, subtitles and the like.
    kodi = ''
      {{ integration_entities('kodi') | select('match', 'media_player\\.')
         | select('in', area_entities(area) if area else [])
         | select('is_state', ['playing', 'paused']) | first | default("") }}'';
    androids = ''
      {{ integration_entities('androidtv')
         | select('match', 'media_player\\.')
         | select('in', area_entities(area) if area else []) | list }}'';
    app = ''
      {%- set ns = namespace(app="") -%}
      {%- for a in ${builtins.toJSON androidApps} -%}
        {%- if not ns.app and (a | lower) in sentence -%}{%- set ns.app = a -%}{%- endif -%}
      {%- endfor -%}
      {{ ns.app }}'';
    players = ''
      {{ (integration_entities('music_assistant') + integration_entities('kodi') + integration_entities('androidtv'))
         | select('match', 'media_player\\.')
         | select('in', area_entities(area) if area else []) | list }}'';
  };
  tvWords = "(tv|television|telly)";
  videoWords = "(film|movie|show|video|episode)";
  mediaWords = "(tv|television|telly|film|movie|show|video|music|radio|podcast|audiobook|song)";
  voiceTvAutomation = {
    alias = "Voice: the room's TV";
    id = "lanbat_voice_tv";
    mode = "parallel";
    trigger = [
      {
        platform = "conversation";
        id = "on";
        command = [
          "(turn|switch) on the ${tvWords} [please]"
          "(turn|switch) the ${tvWords} on [please]"
        ];
      }
      {
        platform = "conversation";
        id = "off";
        command = [
          "(turn|switch) off the ${tvWords} [please]"
          "(turn|switch) the ${tvWords} off [please]"
        ];
      }
      {
        platform = "conversation";
        id = "pause";
        command = [ "pause [the] [${mediaWords}] [please]" ];
      }
      {
        platform = "conversation";
        id = "resume";
        command = [
          "(resume|continue|unpause) [the] [${mediaWords}] [please]"
          "carry on [playing] [please]"
        ];
      }
      # Kodi only, on the film or show on in the room.
      {
        platform = "conversation";
        id = "seek";
        command = [
          "(skip|jump|go) (forward|forwards|ahead|back|backward|backwards) [by] {amount}"
          "(fast forward|rewind) [by] {amount}"
          "(skip|jump|go) (forward|forwards|ahead|back|backward|backwards) [please]"
          "rewind [a bit] [please]"
        ];
      }
      {
        platform = "conversation";
        id = "subtitles";
        command = [
          "(turn|switch) (on|off) [the] (subtitles|captions)"
          "(turn|switch) [the] (subtitles|captions) (on|off)"
          "(subtitles|captions) (on|off) [please]"
          "(show|hide) [the] (subtitles|captions)"
        ];
      }
      {
        platform = "conversation";
        id = "whats_on";
        command = [
          "what am I watching"
          "(what is|what's) (this|on) [the ${tvWords}]"
          "what (film|movie|show|episode) is this"
        ];
      }
      {
        platform = "conversation";
        id = "stop_video";
        command = [
          "stop [the] ${videoWords} [please]"
          "stop watching [please]"
        ];
      }
      {
        platform = "conversation";
        id = "episode";
        # Not "play the next episode": that is "play the next episode of
        # <show>" (voiceVideoVariables) and the music's "play {query}".
        command = [
          "(next|previous) episode [please]"
          "(skip|go) to the (next|previous) episode [please]"
        ];
      }
    ]
    ++ lib.optional (androidApps != [ ]) {
      platform = "conversation";
      id = "open";
      command = [
        "(open|launch) (${lib.concatStringsSep "|" androidApps}) [on the ${tvWords}] [please]"
      ];
    };
    action = voicePlaySteps voiceTvVariables ++ [
      {
        choose = [
          {
            conditions = [
              {
                condition = "trigger";
                id = [
                  "on"
                  "off"
                ];
              }
            ];
            sequence = [
              (ifThen "{{ tvs | length == 0 }}"
                [ (reply "There's no TV {{ place }}.") ]
                [
                  {
                    service = "media_player.turn_{{ trigger.id }}";
                    target.entity_id = "{{ tvs }}";
                    continue_on_error = true;
                  }
                  (reply "Turning the TV {{ trigger.id }}.")
                ]
              )
            ];
          }
          {
            conditions = [
              {
                condition = "trigger";
                id = "pause";
              }
            ];
            sequence = [
              {
                variables.kodis = "{{ players | select('in', integration_entities('kodi')) | select('is_state', ['playing', 'paused']) | list }}";
              }
              {
                variables.playing = "{{ players | select('is_state', 'playing') | list }}";
              }
              {
                repeat.for_each = "{{ kodis }}";
                repeat.sequence = [
                  {
                    service = "kodi.call_method";
                    target.entity_id = "{{ repeat.item }}";
                    continue_on_error = true;
                    data = {
                      method = "JSONRPC.NotifyAll";
                      sender = "lanbat";
                      message = "tv.hold";
                    };
                  }
                ];
              }
              (ifThen "{{ playing | length == 0 and kodis | length == 0 }}"
                [ (reply "Nothing is playing {{ place }}.") ]
                [
                  # A film the companion paused for the question is already
                  # paused, and stays so.
                  (ifThen "{{ playing | length > 0 }}"
                    [
                      {
                        service = "media_player.media_pause";
                        target.entity_id = "{{ playing }}";
                        continue_on_error = true;
                      }
                    ]
                    [ ]
                  )
                  (reply "Paused.")
                ]
              )
            ];
          }
          {
            conditions = [
              {
                condition = "trigger";
                id = "resume";
              }
            ];
            sequence = [
              { variables.paused = "{{ players | select('is_state', 'paused') | list }}"; }
              (ifThen "{{ paused | length == 0 }}"
                [ (reply "Nothing is paused {{ place }}.") ]
                [
                  {
                    service = "media_player.media_play";
                    target.entity_id = "{{ paused }}";
                    continue_on_error = true;
                  }
                  (reply "Okay.")
                ]
              )
            ];
          }
          {
            conditions = [
              {
                condition = "trigger";
                id = [
                  "seek"
                  "subtitles"
                  "whats_on"
                  "stop_video"
                  "episode"
                ];
              }
            ];
            sequence = [
              (ifThen "{{ not kodi }}"
                [ (reply "Nothing is on the TV {{ place }}.") ]
                [
                  {
                    choose = [
                      {
                        conditions = [
                          {
                            condition = "trigger";
                            id = "seek";
                          }
                        ];
                        sequence = [
                          # "skip back 2 minutes", "go forward 30 seconds",
                          # "rewind": 30 seconds unless a number is said.
                          {
                            variables.seconds = ''
                              {%- set n = (trigger.slots.amount | default("") | regex_findall('\\d+') | first | default(30)) | int -%}
                              {%- set n = n * 60 if 'minute' in sentence else n -%}
                              {{ -n if (sentence is search('\\b(back|backward|backwards|rewind)\\b')) else n }}'';
                          }
                          {
                            service = "kodi.call_method";
                            target.entity_id = "{{ kodi }}";
                            data = {
                              method = "Player.Seek";
                              playerid = 1;
                              value.seconds = "{{ seconds | int }}";
                            };
                          }
                          (reply "{{ 'Back' if (seconds | int) < 0 else 'Forward' }} {{ (seconds | int) | abs }} seconds.")
                        ];
                      }
                      {
                        conditions = [
                          {
                            condition = "trigger";
                            id = "subtitles";
                          }
                        ];
                        sequence = [
                          {
                            variables.on = "{{ sentence is search('\\b(on|show)\\b') }}";
                          }
                          {
                            service = "kodi.call_method";
                            target.entity_id = "{{ kodi }}";
                            data = {
                              method = "Player.SetSubtitle";
                              playerid = 1;
                              subtitle = "{{ 'on' if on else 'off' }}";
                            };
                          }
                          (reply "Subtitles {{ 'on' if on else 'off' }}.")
                        ];
                      }
                      {
                        conditions = [
                          {
                            condition = "trigger";
                            id = "whats_on";
                          }
                        ];
                        sequence = [
                          (reply ''
                            {%- set title = state_attr(kodi, 'media_title') or 'something without a title' -%}
                            {%- set series = state_attr(kodi, 'media_series_title') -%}
                            {%- if series -%}
                              {{ series }}, season {{ state_attr(kodi, 'media_season') }}, episode {{ state_attr(kodi, 'media_episode') }}: {{ title }}.
                            {%- else -%}
                              {{ title }}.
                            {%- endif -%}'')
                        ];
                      }
                      {
                        conditions = [
                          {
                            condition = "trigger";
                            id = "stop_video";
                          }
                        ];
                        sequence = [
                          {
                            service = "media_player.media_stop";
                            target.entity_id = "{{ kodi }}";
                            continue_on_error = true;
                          }
                          (reply "Stopped.")
                        ];
                      }
                      {
                        conditions = [
                          {
                            condition = "trigger";
                            id = "episode";
                          }
                        ];
                        sequence = [
                          {
                            variables.next = "{{ 'next' in sentence }}";
                          }
                          {
                            service = "media_player.media_{{ 'next' if next else 'previous' }}_track";
                            target.entity_id = "{{ kodi }}";
                            continue_on_error = true;
                          }
                          (reply "{{ 'Next' if next else 'Previous' }} episode.")
                        ];
                      }
                    ];
                  }
                ]
              )
            ];
          }
          {
            conditions = [
              {
                condition = "trigger";
                id = "open";
              }
            ];
            sequence = [
              # The box in the room that has the app, else any box in it.
              {
                variables.box = ''
                  {%- set ns = namespace(box="") -%}
                  {%- for b in androids -%}
                    {%- if not ns.box and app in (state_attr(b, 'source_list') or []) -%}{%- set ns.box = b -%}{%- endif -%}
                  {%- endfor -%}
                  {{ ns.box or (androids | first | default("")) }}'';
              }
              (ifThen "{{ not box }}"
                [ (reply "There's no TV {{ place }} that can open apps.") ]
                [
                  {
                    "if" = [
                      {
                        condition = "template";
                        value_template = "{{ is_state(box, 'off') }}";
                      }
                    ];
                    "then" = [
                      {
                        service = "media_player.turn_on";
                        target.entity_id = "{{ box }}";
                        continue_on_error = true;
                      }
                    ];
                  }
                  {
                    service = "media_player.select_source";
                    target.entity_id = "{{ box }}";
                    data.source = "{{ app }}";
                    continue_on_error = true;
                  }
                  (reply "Opening {{ app }}.")
                ]
              )
            ];
          }
        ];
      }
    ];
  };

  # The music steps, after a video branch when there is a Kodi to play on.
  voiceWithVideo =
    musicSteps:
    if hasKodi then [ (ifThen "{{ video_kind != '' }}" voiceVideoSteps musicSteps) ] else musicSteps;

  kodiAutomations = [
    {
      alias = "Kodi: a library call for a voice request";
      id = "lanbat_kodi_call";
      mode = "parallel";
      trigger = [
        {
          platform = "event";
          event_type = "lanbat_kodi_call";
        }
      ];
      action = [
        {
          service = "kodi.call_method";
          target.entity_id = "{{ trigger.event.data.entity_id }}";
          data = "{{ dict(trigger.event.data.params, method=trigger.event.data.method) }}";
          continue_on_error = true;
        }
      ];
    }
    {
      # Kodi's "turn on/off" in Home Assistant only fires these events; the TV
      # box's companion takes the notification and switches the TV over CEC.
      alias = "Kodi: TV power over CEC";
      id = "lanbat_kodi_power";
      mode = "queued";
      trigger = [
        {
          platform = "event";
          event_type = "kodi.turn_on";
          id = "on";
        }
        {
          platform = "event";
          event_type = "kodi.turn_off";
          id = "off";
        }
      ];
      action = [
        {
          service = "kodi.call_method";
          target.entity_id = "{{ trigger.event.data.entity_id }}";
          continue_on_error = true;
          data = {
            method = "JSONRPC.NotifyAll";
            sender = "lanbat";
            message = "tv.{{ trigger.id }}";
          };
        }
      ];
    }
  ];

  voicePlayAutomations = [
    {
      alias = "Voice: play on the room's speaker";
      id = "lanbat_voice_play";
      mode = "parallel";
      trigger = [
        {
          platform = "conversation";
          command = [
            "play [me] [some] {query}"
            "put on {query}"
            "put {query} on"
            "(I want to|I'd like to|let me|can I) (listen to|hear) {query}"
            "listen to {query}"
            "tune in to {query}"
          ]
          ++ lib.optionals hasKodi [
            "watch {query}"
            "(I want to|I'd like to|let's|let me|can I|can we) watch {query}"
          ];
        }
      ];
      action =
        voicePlaySteps (voicePlayVariables // lib.optionalAttrs hasKodi voiceVideoVariables)
        ++ voiceWithVideo [
          {
            "if" = [
              {
                condition = "template";
                value_template = "{{ not player }}";
              }
            ];
            "then" = [
              { set_conversation_response = "There's no speaker {{ place }}."; }
            ];
            "else" = [
              # What the speaker had before, to tell the new item from it.
              { variables.before = "{{ state_attr(player, 'media_content_id') or '' }}"; }
              {
                choose = [
                  {
                    conditions = [
                      {
                        condition = "template";
                        value_template = "{{ play_artist != '' }}";
                      }
                    ];
                    sequence = [
                      {
                        service = "music_assistant.play_media";
                        target.entity_id = "{{ player }}";
                        continue_on_error = true;
                        data = {
                          media_id = "{{ play_id }}";
                          media_type = "track";
                          artist = "{{ play_artist }}";
                          enqueue = "replace";
                          radio_mode = "{{ similar }}";
                        };
                      }
                    ];
                  }
                  {
                    conditions = [
                      {
                        condition = "template";
                        value_template = "{{ play_type != '' }}";
                      }
                    ];
                    sequence = [
                      {
                        service = "music_assistant.play_media";
                        target.entity_id = "{{ player }}";
                        continue_on_error = true;
                        data = {
                          media_id = "{{ play_id }}";
                          media_type = "{{ play_type }}";
                          enqueue = "replace";
                          radio_mode = "{{ similar }}";
                        };
                      }
                    ];
                  }
                ];
                default = [
                  {
                    service = "music_assistant.play_media";
                    target.entity_id = "{{ player }}";
                    continue_on_error = true;
                    data = {
                      media_id = "{{ play_id }}";
                      enqueue = "replace";
                      radio_mode = "{{ similar }}";
                    };
                  }
                ];
              }
              # Say what actually plays, or that nothing could be found: Music
              # Assistant may fail, or pick something else than was meant.
              {
                wait_template = "{{ is_state(player, 'playing') and (state_attr(player, 'media_content_id') or '') != before }}";
                timeout = "00:00:12";
                continue_on_timeout = true;
              }
              {
                set_conversation_response = ''
                  {%- if wait.completed -%}
                    {%- set title = state_attr(player, 'media_title') or play_id -%}
                    {%- set artist = state_attr(player, 'media_artist') -%}
                    {{ 'Playing something like ' if (similar | string | lower) == 'true' else 'Playing ' }}{{ title }}{{ ' by ' ~ artist if artist and artist | lower not in title | lower else "" }}.
                  {%- else -%}
                    Sorry, I couldn't find {{ play_id }}{{ ' by ' ~ play_artist if play_artist else "" }}.
                  {%- endif -%}'';
              }
            ];
          }
        ];
    }
    {
      alias = "Voice: stop the room's speaker";
      id = "lanbat_voice_stop";
      mode = "parallel";
      trigger = [
        {
          platform = "conversation";
          command = [
            "stop [playing] [the] [(music|radio|podcast|audiobook|song|playback|player|speaker)] [please] [in {query}]"
            "stop it [please]"
            "turn off the (music|radio|podcast|audiobook|player|speaker|playback) [please] [in {query}]"
            "turn the (music|radio|podcast|audiobook|player|speaker) off [please] [in {query}]"
            "(be quiet|silence|enough) [please]"
            # Stray words before or after the command: a transcript that also
            # caught the speaker's own audio ("... two sides obviously turn off
            # the radio", "stop playing ... I can't help it", lyrics). Only for
            # stopping: a stray stop is harmless, a stray play is not.
            "{noise} (stop|turn off) the (music|radio|podcast|audiobook|player|speaker)"
            "{noise} turn the (music|radio|podcast|audiobook|player|speaker) off"
            "{noise} (stop|pause) [the] music"
            "{noise} stop playing"
            "stop playing {rest}"
            "stop the (music|radio|player) {rest}"
          ];
        }
      ];
      action = voicePlaySteps voicePlayStopVariables ++ [
        {
          "if" = [
            {
              condition = "template";
              value_template = "{{ players | length == 0 }}";
            }
          ];
          "then" = [
            { set_conversation_response = "There's no speaker {{ place }}."; }
          ];
          "else" = [
            {
              service = "media_player.media_stop";
              target.entity_id = "{{ players }}";
            }
            { set_conversation_response = "Okay."; }
          ];
        }
      ];
    }
  ];

  zigbeeAutomations = [
    {
      alias = "Zigbee bridge offline";
      id = "lanbat_zigbee_bridge_offline";
      trigger = [
        {
          platform = "state";
          entity_id = "binary_sensor.zigbee2mqtt_bridge_connection_state";
          to = "off";
        }
      ];
      action = [
        {
          service = "persistent_notification.create";
          data = {
            notification_id = "zigbee_bridge_offline";
            title = "Zigbee bridge offline";
            message = "Zigbee2MQTT lost its MQTT connection.";
          };
        }
      ];
    }
    {
      alias = "Zigbee bridge online";
      id = "lanbat_zigbee_bridge_online";
      trigger = [
        {
          platform = "state";
          entity_id = "binary_sensor.zigbee2mqtt_bridge_connection_state";
          to = "on";
        }
      ];
      action = [
        {
          service = "persistent_notification.dismiss";
          data.notification_id = "zigbee_bridge_offline";
        }
      ];
    }
  ];

in
{
  options.lanbat.homeAssistant = {
    ssoUsers = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "akadmin" ];
      description = ''
        Authentik usernames to provision as Home Assistant users. Each logs in
        through Authentik (auth_oidc), which links the Authentik username to
        the HA user of the same name; usernames must match Authentik exactly.
      '';
    };
  };

  config = {
    # The loopback is this host's own: an LLM there is a service on this host.
    assertions = [
      {
        # A host that names its services gets only those; the agent would
        # talk to a closed port.
        assertion =
          !llmRouter
          || (config.lanbat.hasService "assistant-router" && config.lanbat.hasService "llm-gateway");
        message =
          "lanbat: lanbat.deployment.haLlm points at the assistant router, but this host's services"
          + " don't include assistant-router and llm-gateway; add both to hosts.<key>.services.";
      }
      {
        assertion = !llmLocal || config.lanbat.hasService "llama-cpp";
        message = "lanbat: lanbat.deployment.haLlm.baseUrl is on the loopback (${
          if llm == null then "" else llm.baseUrl
        }), but this host has no llama-cpp service; add it to the server's services, or point haLlm at another endpoint.";
      }
      {
        # The apple-silicon profile moves the model to the Mac; a model on the
        # loopback would take back the cores that profile gives speech-to-text.
        assertion = voiceComputeProfile != "apple-silicon" || (llm != null && !llmLocal);
        message = "lanbat: lanbat.deployment.voiceCompute.profile is \"apple-silicon\", so lanbat.deployment.haLlm.baseUrl must be the Mac's OpenAI-compatible API on the LAN, not ${
          if llm == null then "null" else llm.baseUrl
        }.";
      }
    ];

    # The schema is merged into lanbat.services.home-assistant.settings;
    # checks.nix rejects any key it does not declare.
    lanbat.settingsSchema.home-assistant = homeAssistantSettings;

    lanbat.services.home-assistant = {
      # Home Assistant connects to each voice satellite, which normally runs on
      # another host.
      #
      # The predicate has to come from deploy data, not from the resolved
      # services: consumes is part of the description, and lib/ builds the
      # endpoint table from a first pass in which that table is still empty.
      # Reading either lanbat.hasService or lanbat.endpoints here made the two
      # passes disagree, so the table recorded no edge and the satellite's host
      # generated a drop with no accept. voiceRooms is static, so both passes
      # see the same answer.
      consumes =
        lib.optional voiceRooms "voice-satellite"
        # Kodi's web server/JSON-RPC and its notification port, on the TV host.
        ++ lib.optionals hasKodi [
          "kodi"
          "kodi-events"
        ];
      # home-assistant-post-setup configures MQTT with the password that
      # mosquitto.nix declares for Home Assistant.
      readsSecrets =
        lib.optional (config.lanbat.hasService "mosquitto") "mosquitto-ha-pass"
        # The Echo Shows' keys (services/techo5-bridge.nix), for their ESPHome entries.
        ++ lib.optionals hasTechos (map (key: "techo5-${key}-api-key") (lib.attrNames techo5Devices))
        # auth_oidc's client secret, copied into secrets.yaml before HA starts.
        ++ [ "authentik-oidc-secrets" ];
      subdomain = "ha";
      port = 8123;
      # Sign-in is HA's own login, which hands over to Authentik over OpenID
      # Connect (auth_oidc). Forward auth would break the companion apps: they
      # start the login in a WebView and finish it in the browser, which never
      # has the outpost's session cookie (docs/auth.md).
      auth = "app";
      apiClients = true; # companion apps and REST clients call HA directly
      # Who may log in at all: auth_oidc would create an HA user for anyone
      # Authentik lets through. Add people to this group in Authentik.
      access.groups = [ "home-assistant" ];
      oidc = {
        redirectPaths = [ "/auth/oidc/callback" ];
        secretVariable = "AUTHENTIK_HA_CLIENT_SECRET";
      };
      secrets = {
        hass-bootstrap-env.owner = "hass";
        # The API key of the conversation agent's LLM.
        ha-llm-api-key = {
          enable = llmKey;
          owner = "hass";
        };
        # The record of the voice satellites' token, for home-assistant-post-setup.
        # The Kodi web server's password (the TV host's Kodi sets the same one).
        kodi-web-password = {
          enable = hasKodi;
          owner = "hass";
        };
        ha-voice-refresh-token = {
          # The Echo Shows get the voice satellites' token too (techo5-bridge).
          enable = voiceRooms || hasTechos;
          owner = "root";
        };
        # Xiaomi BLE bind keys, read by home-assistant-post-setup (runs as root).
        ha-xiaomi-ble = {
          enable = xiaomiBle;
          owner = "root";
        };
        # The iCal feed URL of the house's Google Calendar (one line), for the
        # remote_calendar entry home-assistant-post-setup provisions.
        ha-gcal-url = {
          enable = haCalendar;
          owner = "root";
        };
      };
      caddy.proxyOptions = ''
        # Long-lived websockets for HA's live updates.
        transport http {
          keepalive 24h
        }
      '';
      dashboard = {
        group = "Automation";
        name = "Home Assistant";
        description = "Home automation";
        widget = {
          type = "homeassistant";
          key = {
            _secret = "HA_LONG_LIVED_TOKEN";
          };
        };
      };
    };

    # The recorder (history) lives in the always-on PostgreSQL. HA logs in as
    # its system user over the socket, so no password is needed.
    lanbat.postgresql.databases.hass.instance = "always-on";

    # BlueZ for the USB Bluetooth adapter.  HA talks to it over system D-Bus;
    # the "bluetooth" component below grants the matching systemd permissions.
    hardware.bluetooth = {
      enable = true;
      powerOnBoot = true;
    };

    systemd.services.home-assistant = {
      after = [
        (config.lanbat.postgresql.instance "always-on").unit
        "mosquitto.service"
        "bluetooth.service"
      ];
      requires = [
        (config.lanbat.postgresql.instance "always-on").unit
        "mosquitto.service"
      ];
      # Bluetooth is a soft dependency: a missing or failed adapter must not
      # stop HA, matching its always-on posture.
      wants = [ "bluetooth.service" ];
      # auth_oidc reads its client secret as !secret auth_oidc_client_secret.
      # Root ("+") reads the Authentik client-secret file, which only root can;
      # the script writes secrets.yaml as hass.
      serviceConfig.ExecStartPre = [
        "+${oidcSecret}/bin/ha-oidc-secret ${config.lanbat.secrets.authentik-oidc-secrets.path} ${config.services.home-assistant.configDir}/secrets.yaml hass"
      ];
    };

    systemd.services.home-assistant-bootstrap = {
      description = "Complete Home Assistant onboarding and provision SSO users";
      wantedBy = [ "multi-user.target" ];
      after = [ "home-assistant.service" ];
      wants = [ "home-assistant.service" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "hass";
        Group = "hass";
        EnvironmentFile = config.lanbat.secrets.hass-bootstrap-env.path;
      };

      path = [ bootstrap ];

      script = ''
        set -a
        . ${config.lanbat.secrets.hass-bootstrap-env.path}
        set +a
        export INTERNAL_URL="${internalUrl}"
        export EXTERNAL_URL="https://ha.${domain}"
        export SSO_USERS="${lib.concatStringsSep " " config.lanbat.homeAssistant.ssoUsers}"
        exec home-assistant-bootstrap
      '';
    };

    systemd.services.home-assistant-post-setup = {
      description = "Configure Home Assistant integrations (MQTT, Frigate, Wyoming, Music Assistant, voice)";
      wantedBy = [ "multi-user.target" ];
      after = [
        "home-assistant.service"
        "home-assistant-bootstrap.service"
        "music-assistant-setup.service"
        "mosquitto.service"
        # bluetooth_adapters reads BlueZ over D-Bus, so BlueZ must be up.
        "bluetooth.service"
      ];
      wants = [
        "music-assistant-setup.service"
        "bluetooth.service"
      ];
      requires = [ "mosquitto.service" ];

      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        User = "root";
      };

      path = [ postSetup ];

      script = ''
        ${lib.optionalString (config.lanbat.hasService "mosquitto") ''
          export MQTT_BROKER="127.0.0.1"
          export MQTT_PORT="${toString config.lanbat.services.mosquitto.endpoint.port}"
          export MQTT_USERNAME="homeassistant"
          export MQTT_PASSWORD="$(cat ${config.lanbat.secrets.mosquitto-ha-pass.path})"
        ''}
        ${lib.optionalString (config.lanbat.hasService "frigate") ''export FRIGATE_URL="${localUrl "frigate" "/"}"''}
        ${lib.optionalString (config.lanbat.hasService "music-assistant") ''export MUSIC_ASSISTANT_URL="${localUrl "music-assistant" ""}"''}
        export PI_HOST="${piHost}"
        export EXTRA_SATELLITES="${lib.concatStringsSep " " extraSatellites}"
        export SERVER_HOST_KEY="${config.lanbat.hostKey}"
        export PRIMARY_STORAGE_KEY="${if storageKey == null then "" else storageKey}"
        export VOICE_SATELLITE_REGISTRATIONS=${lib.escapeShellArg voiceSatelliteRegistrationsEnv}
        ${lib.optionalString hasAndroidTv ''
          export ANDROID_TVS=${lib.escapeShellArg androidTvsJson}
          export ANDROID_ADBKEY="${config.services.home-assistant.configDir}/.android/adbkey"
        ''}
        ${lib.optionalString hasKodi ''
          export KODI_HOSTS=${lib.escapeShellArg kodiHostsEnv}
          export KODI_PASSWORD_FILE="${config.lanbat.secrets.kodi-web-password.path}"
        ''}
        ${lib.optionalString satellite.enable ''
          export LOCAL_SATELLITE_PORT="${
            if satellite.backend == "lva" then
              toString satellite.lva.port
            else
              lib.last (lib.splitString ":" satellite.uri)
          }"
        ''}
        export PIPELINE_STT_LANGUAGE="${config.services.wyoming.faster-whisper.servers.main.language}"
        ${lib.optionalString (config.lanbat.hasService "voice-id") ''
          # Speech-to-text through the speaker-identification proxy
          # (services/voice-id.nix); faster-whisper stays as its upstream.
          export VOICE_ID_PORT="${toString (lib.head config.lanbat.services.voice-id.extraPorts)}"
          export PIPELINE_STT_ENGINE="stt.voice_id"
        ''}
        export PIPELINE_TTS_LANGUAGE="${lib.head (lib.splitString "-" piper.voice)}"
        export PIPELINE_TTS_VOICE="${piper.voice}"
        export PIPELINE_WAKE_WORD="hey_nabu"
        ${lib.optionalString (llm != null) ''
          export LLM_BASE_URL="${llm.baseUrl}"
          export LLM_MODEL="${llm.model}"
          ${lib.optionalString llmKey ''
            export LLM_API_KEY_FILE="${config.lanbat.secrets.ha-llm-api-key.path}"
          ''}
          export LLM_MAX_TOKENS="${toString (if llmRouter then 300 else voiceCompute.llmMaxTokens)}"
          export LLM_USE_TOOLS="${if llmRouter then "true" else "false"}"
          export LLM_ROUTER="${if llmRouter then "1" else "0"}"
        ''}
        ${lib.optionalString (voiceRooms || hasTechos) ''
          export VOICE_TOKEN_RECORD_FILE="${config.lanbat.secrets.ha-voice-refresh-token.path}"
        ''}
        ${lib.optionalString hasTechos ''
          export TECHO5_DEVICES=${lib.escapeShellArg techo5Json}
          export TECHO5_TODO_LISTS=${lib.escapeShellArg (builtins.toJSON techo5TodoLists)}
        ''}
        ${lib.optionalString personalLists ''
          export LISTS_FILE="${routerListsPath}"
        ''}
        ${lib.optionalString xiaomiBle ''
          export XIAOMI_BLE_KEYS_FILE="${config.lanbat.secrets.ha-xiaomi-ble.path}"
        ''}
        ${lib.optionalString haCalendar ''
          export GCAL_URL="$(cat ${config.lanbat.secrets.ha-gcal-url.path})"
        ''}
        # Rooms for devices, and the dashboards generated from the registries.
        export DEVICE_AREAS=${lib.escapeShellArg (builtins.toJSON cfg.deviceAreas)}
        export HA_DASHBOARDS="${lib.getExe haDashboards}"
        export DASHBOARD_LINKS=${lib.escapeShellArg dashboardLinks}
        ${lib.optionalString hasTechos ''
          export DASHBOARD_SCREENS=${
            lib.escapeShellArg (
              builtins.toJSON (
                lib.unique (
                  lib.mapAttrsToList (_: d: {
                    inherit (d) room;
                    path = hostLib.screenDashboard d.room;
                  }) techo5Devices
                )
              )
            )
          }
          export DASHBOARD_PEOPLE=${
            lib.escapeShellArg (
              builtins.toJSON (lib.mapAttrs (_: p: p.name) (config.lanbat.deployment.people or { }))
            )
          }
        ''}
        exec home-assistant-post-setup
      '';
    };

    # The lists people ask the assistant router for (by voice, or on the who
    # page) are made as soon as it writes them: post-setup runs again, which
    # restarts Home Assistant briefly when a list is new.
    systemd.paths.home-assistant-post-setup-lists = lib.mkIf personalLists {
      wantedBy = [ "multi-user.target" ];
      pathConfig = {
        PathChanged = routerListsPath;
        Unit = "home-assistant-post-setup.service";
      };
    };

    # Devices paired since the last deploy reach the dashboards overnight;
    # post-setup restarts Home Assistant only when something changed.
    systemd.timers.home-assistant-post-setup = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "*-*-* 03:30:00";
        RandomizedDelaySec = "10m";
      };
    };

    services.home-assistant = {
      enable = true;
      openFirewall = false; # Caddy handles exposure.

      # PostgreSQL driver for the recorder, and `ical` for the calendar platform
      # that the local to-do and calendar lists sit on: without it local_todo
      # fails with "No module named 'ical'", so reminders and the house calendar
      # show up as restored-but-unavailable.
      extraPackages = ps: [
        ps.psycopg2
        ps.ical
      ];

      # Install extra Python components declaratively.
      customComponents = [
        # 2 upstream test failures in nixpkgs 26.05 packaging; skip checks.
        (pkgs.home-assistant-custom-components.frigate.overridePythonAttrs (_: {
          doCheck = false;
        }))
        authOidcComponent
      ]
      # The conversation agent for the LLM in lanbat.haLlm.
      ++ lib.optional (llm != null) llmComponent;

      extraComponents = [
        "default_config"
        "met" # weather
        "radio_browser"
        "google_translate" # TTS — gtts dependency
        "mqtt" # Zigbee devices arrive via Zigbee2MQTT → MQTT discovery
        "mobile_app"
        "person"
        "history"
        "logbook"
        "recorder"
        "frontend"
        "config"
        "lovelace"
        "network"
        "stream"
        "camera"
        "ffmpeg"
        # Wyoming voice assistant protocol
        "wyoming"
      ]
      ++ lib.optionals hasKodi [ "kodi" ]
      ++ lib.optionals hasAndroidTv [ "androidtv" ]
      ++ lib.optionals (hasLvaSatellite || hasTechos) [
        "esphome"
      ]
      ++ lib.optionals haCalendar [
        # The house's Google Calendar (iCal feed in ha-gcal-url.age). A
        # config-entry-only integration, so the yaml scan never sees it;
        # declaring it here is what pulls its ical dependency into the package.
        "remote_calendar"
      ]
      ++ [
        "music_assistant"
        "qbittorrent"
        # Bluetooth LE sensors.  "bluetooth" also relaxes the module's systemd
        # hardening: it adds AF_BLUETOOTH and CAP_NET_ADMIN/CAP_NET_RAW so HA can
        # talk to BlueZ and the hci device.
        "bluetooth"
        "xiaomi_ble" # Xiaomi BLE sensors on stock firmware (encrypted, bind key)
        "bthome" # Xiaomi sensors reflashed with pvvx firmware (BTHome v2, no key)
      ]
      # extended_openai_conversation depends on these.
      ++ lib.optionals (llm != null) [
        "rest"
        "scrape"
      ];

      config = {
        # Trust Caddy as reverse proxy.
        http = {
          use_x_forwarded_for = true;
          trusted_proxies = [
            "127.0.0.1"
            "::1"
          ];
          ip_ban_enabled = true;
          login_attempts_threshold = 5;
        };

        # The companion apps register their phone through this integration and
        # refuse to continue without it ("The mobile app integration is required
        # to use the app"). extraComponents only installs its dependencies; this
        # line is what sets it up, since the configuration lists no default_config.
        mobile_app = { };

        # follow-me writes every decision to the logbook with logbook.log.
        # extraComponents only installs the integration's dependencies; without
        # this key the service does not exist, and the automations abort at
        # their first action (the log line) before doing anything.
        logbook = { };

        homeassistant = {
          name = "Home";
          latitude = config.lanbat.deployment.haLatitude;
          longitude = config.lanbat.deployment.haLongitude;
          elevation = config.lanbat.deployment.haElevation;
          unit_system = "metric";
          time_zone = config.lanbat.deployment.timezone;
          external_url = "https://ha.${domain}";
          # Music Assistant fetches tts_proxy URLs server-side; use loopback so
          # announcements are not blocked by ip_ban when MA calls 192.168.1.10.
          # LVA satellites fetch each spoken reply from the URL Home Assistant
          # builds from internal_url, so with one in the profile it must be an
          # address other hosts can reach: the HTTPS one through Caddy, where
          # /api/* (the replies' /api/tts_proxy/) skips forward auth. Port 8123
          # stays closed to the LAN. An Echo Show on TECHO5 fetches its replies
          # the same way (techo5-bridge).
          internal_url = if hasLvaSatellite || hasTechos then "https://ha.${domain}" else internalUrl;
        };

        # Voice replies in a room (modules/core/voice-satellite.nix). A satellite
        # with a room calls voice_reply with its reply; voice_reply starts the
        # announcement on the room's Music Assistant players and returns how
        # many there are, so the satellite knows whether to play it itself.
        script = {
          # A message for a person, on whatever body the assistant router
          # places them beside (a robot's caption, a screen's notification):
          # their "Where is" sensor names the room, and the router's caption
          # command reaches its body. Away from every body, a notification in
          # Home Assistant instead.
          lanbat_complete_reminder = lib.mkIf hasTechos {
            alias = "Complete a reminder shown on an Echo Show";
            mode = "single";
            fields = {
              room.description = "The room's name (same as on the MQTT reminder sensors).";
              slot.description = "Reminder slot on the screen (1–5).";
            };
            sequence = [
              {
                variables.slug = "{{ room | lower | regex_replace('[^a-z0-9]+', '_') | regex_replace('^_+|_+$', '') }}";
              }
              {
                "if" = "{{ slot | int(0) >= 1 and slot | int(0) <= 5 }}";
                "then" = [
                  {
                    variables.sensor = "{{ 'sensor.lanbat_screen_' ~ slug ~ '_reminder_' ~ (slot | int) }}";
                  }
                  {
                    variables = {
                      uid = "{{ state_attr(sensor, 'uid') or '' }}";
                      list_entity = "{{ state_attr(sensor, 'list') or '' }}";
                      reminder_state = "{{ states(sensor) }}";
                    };
                  }
                  {
                    "if" =
                      "{{ uid != '' and list_entity != '' and reminder_state not in ['none', 'unavailable', 'unknown', ''] }}";
                    "then" = [
                      {
                        action = "todo.update_item";
                        target.entity_id = "{{ list_entity }}";
                        data = {
                          item = "{{ uid }}";
                          status = "completed";
                        };
                      }
                    ];
                  }
                ];
              }
            ];
          };

          lanbat_notify_person = lib.mkIf llmRouter {
            alias = "Tell a person, where they are";
            mode = "parallel";
            fields = {
              person.description = "The person's key (lanbat.deployment.people).";
              message.description = "What to tell them.";
            };
            sequence = [
              {
                variables = {
                  room = "{{ states('sensor.lanbat_people_where_' ~ (person | lower | regex_replace('[^a-z0-9]+', '_'))) }}";
                  slug = "{{ room | lower | regex_replace('[^a-z0-9]+', '_') | regex_replace('^_+|_+$', '') }}";
                };
              }
              {
                "if" = "{{ room not in ['home', 'away', 'unknown', 'unavailable', ''] }}";
                "then" = [
                  {
                    action = "mqtt.publish";
                    data = {
                      topic = "lanbat/stackchan/{{ slug }}/cmd/caption";
                      payload = "{{ message }}";
                    };
                  }
                ];
                "else" = [
                  {
                    action = "persistent_notification.create";
                    data = {
                      title = "For {{ person }}";
                      message = "{{ message }}";
                    };
                  }
                ];
              }
            ];
          };

          voice_reply = {
            alias = "Voice reply in a room";
            mode = "parallel";
            fields = {
              message.description = "The reply to speak.";
              room.description = "Name of the area the voice satellite is in.";
            };
            sequence = [
              {
                variables.players = "{{ area_entities(room) | select('in', integration_entities('music_assistant')) | select('match', 'media_player[.]') | reject('is_state', ['unavailable', 'unknown']) | list }}";
              }
              {
                "if" = "{{ players | count > 0 }}";
                "then" = [
                  {
                    action = "script.turn_on";
                    target.entity_id = "script.voice_reply_announce";
                    data.variables = {
                      players = "{{ players }}";
                      message = "{{ message }}";
                    };
                  }
                ];
              }
              { variables.result.players = "{{ players | count }}"; }
              {
                stop = "Reply handed to the room";
                response_variable = "result";
              }
            ];
          };

          voice_reply_announce = {
            alias = "Voice reply announcement";
            mode = "queued";
            fields = {
              players.description = "Music Assistant players to announce on.";
              message.description = "The reply to speak.";
            };
            sequence = [
              {
                # An announcement: Music Assistant turns the music down meanwhile.
                action = "tts.speak";
                target.entity_id = "tts.piper";
                data = {
                  media_player_entity_id = "{{ players }}";
                  message = "{{ message }}";
                  language = lib.head (lib.splitString "-" piper.voice);
                  options.voice = piper.voice;
                };
              }
            ];
          };
        };

        # Login through Authentik over OpenID Connect. An Authentik username
        # links to the HA user of the same name (the bootstrap creates
        # ssoUsers); linking stays on only until each of them has logged in
        # once (docs/auth.md). auth_oidc creates an HA user for any other
        # identity it is handed, so only the home-assistant group gets that
        # far: Authentik binds it to the application (access.groups below) and
        # roles.user refuses anyone outside it. HA's password login remains at
        # ?skip_oidc_redirect=true.
        auth_oidc = {
          client_id = "home-assistant";
          client_secret = "!secret auth_oidc_client_secret";
          discovery_url = "https://auth.${domain}/application/o/home-assistant/.well-known/openid-configuration";
          display_name = "Authentik";
          features = {
            default_redirect = true;
            automatic_user_linking = true;
          };
          roles.user = "home-assistant";
          # Authentik is served with the internal Caddy CA.
          network.tls_ca_path = "/var/lib/caddy-local-ca/ca-certificates.crt";
        };

        # Recorder — keep 30 days in the always-on PostgreSQL.
        recorder = {
          purge_keep_days = 30;
          db_url =
            let
              pg = (config.lanbat.postgresql.instance "always-on");
            in
            "postgresql://@/hass?host=${pg.socket}&port=${toString pg.port}";
          exclude = {
            entity_globs = [
              "*.linkquality"
              "*.rssi"
              "select.*switch_type"
            ];
          };
        };

        automation =
          lib.optionals cfg.zigbee2mqttBridge zigbeeAutomations
          ++ lib.optionals (config.lanbat.hasService "music-assistant" && voiceSatelliteHosts != [ ]) (
            voicePlayAutomations ++ [ voiceVolumeAutomation ]
          )
          ++ lib.optionals (voiceSatelliteHosts != [ ] && (hasKodi || hasAndroidTv)) [ voiceTvAutomation ]
          ++ lib.optionals hasKodi kodiAutomations;
      };

      # No lovelaceConfig: the dashboards are in storage mode, written by
      # home-assistant-post-setup from the registries
      # (pkgs/home-assistant-dashboards, docs/dashboards.md).
    };

    # Keeps a model elsewhere warm between voice commands: a scale-to-zero API
    # (RunPod) cold-starts, and a Mac's server (Ollama, LM Studio) unloads an
    # idle model after a few minutes, either of which costs seconds on the
    # next question.
    systemd.services.ha-llm-keepalive = lib.mkIf (llm != null && !llmLocal) {
      description = "Keep the conversation agent's LLM warm";
      serviceConfig = {
        Type = "oneshot";
        User = "root";
      };
      path = [
        pkgs.curl
        pkgs.coreutils
      ];
      script = ''
        auth=()
        ${lib.optionalString llmKey ''
          auth=(-H "Authorization: Bearer $(cat ${config.lanbat.secrets.ha-llm-api-key.path})")
        ''}
        curl -sS --max-time 45 "''${auth[@]}" \
          -H "Content-Type: application/json" \
          -d '{"model":"${llm.model}","messages":[{"role":"user","content":"ping"}],"max_tokens":1,"chat_template_kwargs":{"enable_thinking":false}}' \
          "${llm.baseUrl}/chat/completions" >/dev/null || true
      '';
    };

    systemd.timers.ha-llm-keepalive = lib.mkIf (llm != null && !llmLocal) {
      description = "Keep the conversation agent's LLM warm";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "3min";
        OnUnitActiveSec = "4min";
        AccuracySec = "1min";
      };
    };

    # HA state lives entirely on server-local storage — resilient to Pi loss.
    # /var/lib/hass is managed by the NixOS module.
  };
}
