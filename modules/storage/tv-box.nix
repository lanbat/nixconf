# modules/storage/tv-box.nix
#
# Raspberry Pi based TV box: Kodi and a games frontend on the Pi's HDMI output,
# with the films, music and ROMs on the storage drives (loaded via the lanbat-tv
# plugin, for the storage-pi role).
#
# Three sessions take turns on the screen. Each is a systemd service that runs
# as the "media" user on tty1, and each conflicts with the others, so starting
# one stops the others:
#   tv-kodi.service    Kodi, drawing directly to the display (GBM)
#   tv-games.service   ES-DE (EmulationStation) in the cage kiosk compositor;
#                      the emulators it starts run in the same session
#   tv-chiaki.service  chiaki-ng (PS4/PS5 remote play) in the cage compositor
#
# Switching sessions: `tv-switch kodi|games|chiaki|toggle` starts one and
# remembers it in /var/lib/tv-session/current, so the Pi boots back into it.
#   - In Kodi: the power key opens the TV power menu (script.tv-power), which
#     switches to EmulationStation or chiaki-ng, restarts Kodi, turns the screen
#     off over CEC, or reboots. Favourites also reach both switches.
#   - In ES-DE: the "Kodi" system. Quitting ES-DE or chiaki-ng returns to Kodi.
#   - On any controller: hold the Guide button for 2 seconds, or Select and
#     Start for 3 (tv-hotkey.service; works even when an emulator hangs); this
#     cycles Kodi → EmulationStation → chiaki-ng.
#   - In a game, Select + Start quits back to ES-DE, and holding Start opens
#     the RetroArch menu.
#
# ROMs use ES-DE's layout, one directory per system (snes, psx, mame, ...), with
# the other media on drive B: /mnt/storage-b/media/roms/<system>.
# Emulator BIOS files go in roms/bios.
#
# Remote control: Kodi's web server (8080, JSON-RPC over HTTP, user "kodi" and
# the kodi-web-password secret) and its TCP notification port (9090) are on,
# for Home Assistant's Kodi integration. They are described as the "kodi" and
# "kodi-events" endpoints, which Home Assistant consumes, so the firewall admits
# only Home Assistant's host. Kodi rewrites guisettings.xml when it exits, so
# tv-kodi sets these settings in it before every start, as root, while Kodi is
# not running.
#
# The Invidious add-on (settings.invidious) watches YouTube through the
# deployment's own instance (youtube.<domain>, from the service table) instead
# of a public one, and the box trusts that instance's internal-CA certificate
# so both the add-on and inputstream.adaptive reach it.
#
# Audio: Kodi plays PCM into the system PipeWire (passthrough off), so with a
# voice satellite on the box its films and music meet the satellite's ducking
# and echo canceller. The satellite's companion (pkgs/lva-kodi-companion)
# pauses a film while you talk, shows what you said and the reply on screen,
# and switches the TV on and off over CEC when Home Assistant asks.
#
# Controllers: wired Xbox pads use the kernel's xpad driver, Bluetooth Xbox
# pads use xpadneo (pair once with bluetoothctl), and USB arcade encoders are
# generic HID joysticks. RetroArch maps them with its autoconfig profiles.
{
  config,
  lib,
  pkgs,
  ...
}:

let
  romDirectory = "/mnt/storage-b/media/roms";
  # Kodi waits for every storage drive this host has; a TV on a host with none
  # (a voice Pi) waits for nothing.
  unlockUnits = map (drive: "storage-${drive}-unlock.service") (
    lib.attrNames (config.lanbat.hosts.${config.lanbat.hostKey}.storage.drives or { })
  );
  home = config.users.users.media.home;

  # Kodi's library sources (kodi-bootstrap writes them into its databases on
  # every boot). The defaults mirror the libraries jellyfin-bootstrap creates,
  # folder for folder and content type for content type (except Games and Misc,
  # which Jellyfin mixes and Kodi scans as movies), so both clients show the
  # same media from the same NFS mounts; a profile sets only what differs,
  # e.g. lanbat.services.kodi.settings.videoSources.tv.path.
  scrapers = {
    movies = "metadata.themoviedb.org.python";
    tvshows = "metadata.tvshows.themoviedb.org.python";
    musicvideos = "metadata.local";
  };
  videoSourceDefaults = {
    movies = {
      path = "/mnt/storage-a/media/movies/";
      content = "movies";
    };
    tv = {
      path = "/mnt/storage-a/media/tv/";
      content = "tvshows";
    };
    music-videos = {
      path = "/mnt/storage-a/media/music-videos/";
      content = "musicvideos";
    };
    documentaries = {
      path = "/mnt/storage-b/media/documentaries/";
      content = "movies";
    };
    documentary-series = {
      path = "/mnt/storage-b/media/documentary-series/";
      content = "tvshows";
    };
    gym = {
      path = "/mnt/storage-b/media/gym/";
      # Jellyfin's Gym library is a TV library, so Kodi scans it the same way.
      content = "tvshows";
    };
    games = {
      path = "/mnt/storage-b/media/games/";
      content = "movies";
    };
    misc = {
      path = "/mnt/storage-b/media/misc/";
      content = "movies";
    };
  };
  musicSourceDefaults = {
    Music = "/mnt/storage-b/media/music/";
    Audiobooks = "/mnt/storage-b/media/audiobooks/";
  };
  kodiSettings = {
    options = {
      videoSources = lib.mkOption {
        type = lib.types.attrsOf (
          lib.types.submodule (
            { config, ... }:
            {
              options = {
                path = lib.mkOption {
                  type = lib.types.strMatching "/.*/";
                  description = "Directory of the source, with a trailing slash.";
                };
                content = lib.mkOption {
                  type = lib.types.enum (lib.attrNames scrapers);
                  description = ''
                    What the source holds. Shows are one folder each, directly in
                    the source's directory; films and music videos may be nested.
                  '';
                };
                scraper = lib.mkOption {
                  type = lib.types.str;
                  default = scrapers.${config.content};
                  defaultText = lib.literalExpression "the content's usual scraper";
                  description = "Kodi scraper add-on for the source.";
                };
              };
            }
          )
        );
        default = { };
        example = lib.literalExpression ''{ tv.path = "/mnt/storage-a/media/tv/shows/"; }'';
        description = ''
          Kodi's video library sources by name. The repository's layout is set
          by default; set a field to change one, or add a source.
        '';
      };
      musicSources = lib.mkOption {
        type = lib.types.attrsOf (lib.types.strMatching "/.*/");
        default = { };
        example = lib.literalExpression ''{ Music = "/mnt/storage-b/media/music/"; }'';
        description = ''
          Kodi's music library sources, name to directory (with a trailing
          slash). The repository's layout is set by default. Each is scanned
          once after Kodi starts if Kodi has never scanned it.
        '';
      };
      resumeRewindSeconds = lib.mkOption {
        type = lib.types.ints.unsigned;
        default = 3;
        example = 0;
        description = ''
          How far back a film paused for a question resumes, in seconds; 0
          resumes where it stopped. Kodi seeks to the keyframe at or before that
          point, so a file with few keyframes replays more than asked: 6 to 10
          seconds on a 1080p H.264 file with a keyframe every ten seconds
          (measured on a Pi 5, 2026-10-22). Needs a satellite on the same host
          (the voice plugin).
        '';
      };
      youtube = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Install the YouTube add-on (plugin.video.youtube) and seed its
          credentials from the kodi-youtube-env secret on every start.
        '';
      };
      twitch = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Install the Twitch add-on (plugin.video.twitch) and seed its token
          from the kodi-twitch-token secret once; the add-on then manages its
          own login and token refresh in its own settings file.
        '';
      };
      opensubtitles = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Install the a4kSubtitles add-on (service.subtitles.a4ksubtitles) and
          seed its OpenSubtitles.com credentials from the
          kodi-opensubtitles-env secret once; the add-on then manages its own
          settings file.
        '';
      };
      invidious = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Install the Invidious add-on (plugin.video.invidious) and point it
          at this deployment's Invidious instance instead of a public one.
          Playback is proxied through that instance (the add-on's "local"
          setting), and the box trusts its internal-CA certificate. Needs
          Invidious in the deployment.
        '';
      };
      privateLibrary = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Let this box's Kodi read media/adult, the folder the storage Pi keeps
          for the private group alone (drive b, mode 2770). Puts the TV account
          in that group and scans the folder as a movies source with Kodi's
          local scraper, so titles come from the filenames rather than a
          metadata lookup. Off by default: anyone at the TV is this account.
        '';
      };
      adulthideout = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Install AdultHideout (plugin.video.adulthideout), the third-party
          add-on that streams adult videos from public sites. It needs no
          account or secret.
        '';
      };
    };
  };
  kodiCfg = config.lanbat.services.kodi.settings;
  # The add-on's instance comes from the profile-wide service table, not a
  # hard-coded hostname: Invidious runs on the server while Kodi runs on the
  # Pi.  Forcing it keeps the add-on from answering a public instance.
  invidiousService = config.lanbat.endpoints.invidious or null;
  invidiousUrl =
    if invidiousService != null && invidiousService.web != null then
      "https://${invidiousService.web.subdomain}.${config.lanbat.deployment.domain}"
    else
      throw "lanbat: lanbat.services.kodi.settings.invidious is on, but this deployment has no Invidious web service.";
  # name|path|content|scraper|recursive|useFolderNames, one per line. Shows
  # are a folder each and not recursive; the rest are scanned recursively.
  # useFolderNames stays off: for shows it is Kodi's "Selected folder
  # contains a single TV show", which made the source itself one show.
  videoSourcesEnv = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (
      name: v:
      lib.concatStringsSep "|" [
        name
        v.path
        v.content
        v.scraper
        (if v.content == "tvshows" then "0" else "1")
        "0"
      ]
    ) kodiCfg.videoSources
  );
  musicSourcesEnv = lib.concatStringsSep "\n" (
    lib.mapAttrsToList (name: path: "${name}|${path}") kodiCfg.musicSources
  );

  kodiWebPort = 8080;
  kodiEventsPort = 9090;
  # YouTube: rewrite the add-on's api_keys.json on every start (plugin.video.
  # youtube v7.x). The credentials come from the kodi-youtube-env secret, three
  # KEY=value lines.
  youtubeSetup = ''
    ytdir=${home}/.kodi/userdata/addon_data/plugin.video.youtube
    mkdir -p "$ytdir"
    . ${config.lanbat.secrets.kodi-youtube-env.path}
    ${lib.getExe pkgs.jq} -n \
      --arg api_key "$YOUTUBE_API_KEY" \
      --arg client_id "$YOUTUBE_OAUTH_CLIENT_ID" \
      --arg client_secret "$YOUTUBE_OAUTH_CLIENT_SECRET" \
      '{keys:{developer:{},user:{api_key:$api_key,client_id:$client_id,client_secret:$client_secret}}}' \
      > "$ytdir/api_keys.json"
    chmod 0600 "$ytdir/api_keys.json"
  '';
  # Twitch: seed the add-on's settings.xml once (plugin.video.twitch v3.0.3).
  # The add-on rewrites the file after a device-code login or token refresh, so
  # seed only when it is absent; a changed secret is applied by deleting the
  # file first, then restarting tv-kodi.
  twitchSetup = ''
    twdir=${home}/.kodi/userdata/addon_data/plugin.video.twitch
    mkdir -p "$twdir"
    twf="$twdir/settings.xml"
    if [ ! -e "$twf" ]; then
      twtoken="$(head -n1 ${config.lanbat.secrets.kodi-twitch-token.path})"
      printf '<settings version="2">\n<setting id="oauth_token_helix">%s</setting>\n</settings>\n' "$(xmlenc "$twtoken")" > "$twf"
    fi
    chmod 0600 "$twf"
  '';
  # Subtitles: seed the a4kSubtitles add-on's OpenSubtitles.com credentials once
  # (service.subtitles.a4ksubtitles v3.3). The add-on rewrites the file when its
  # settings change, so seed only when it is absent; a changed secret is applied
  # by deleting the file first, then restarting tv-kodi.
  opensubtitlesSetup = ''
    subdir=${home}/.kodi/userdata/addon_data/service.subtitles.a4ksubtitles
    mkdir -p "$subdir"
    subf="$subdir/settings.xml"
    if [ ! -e "$subf" ]; then
      . ${config.lanbat.secrets.kodi-opensubtitles-env.path}
      printf '<settings version="2">\n<setting id="opensubtitles.username">%s</setting>\n<setting id="opensubtitles.password">%s</setting>\n</settings>\n' \
        "$(xmlenc "$OPENSUBTITLES_USERNAME")" "$(xmlenc "$OPENSUBTITLES_PASSWORD")" > "$subf"
    fi
    chmod 0600 "$subf"
  '';
  # Invidious: seed the add-on's settings once (plugin.video.invidious 0.2.9).
  # auto_instance off plus instance_url is what keeps it off the public
  # instance list; local makes the instance proxy the streams, so the box
  # talks to the instance alone.  The add-on rewrites the file when its
  # settings change, so seed only when it is absent; a changed instance is
  # applied by deleting the file first, then restarting tv-kodi.
  invidiousSetup = ''
    invdir=${home}/.kodi/userdata/addon_data/plugin.video.invidious
    mkdir -p "$invdir"
    invf="$invdir/settings.xml"
    if [ ! -e "$invf" ]; then
      printf '<settings version="2">\n<setting id="auto_instance">false</setting>\n<setting id="instance_url">%s</setting>\n<setting id="local">true</setting>\n</settings>\n' \
        "$(xmlenc "$INVIDIOUS_URL")" > "$invf"
    fi
    chmod 0600 "$invf"
  '';
  # Settings tv-kodi puts in guisettings.xml before Kodi starts (see the top).
  kodiRemote = pkgs.writeShellScript "kodi-remote-control" ''
    set -euo pipefail
    xml=${lib.getExe pkgs.xmlstarlet}
    f=${home}/.kodi/userdata/guisettings.xml
    mkdir -p "$(dirname "$f")"
    [ -s "$f" ] || printf '<settings version="2">\n</settings>\n' > "$f"
    set_setting() {
      local id="$1" value="$2"
      if $xml sel -t -c "/settings/setting[@id='$id']" "$f" >/dev/null 2>&1; then
        $xml ed -L -u "/settings/setting[@id='$id']" -v "$value" \
          -d "/settings/setting[@id='$id']/@default" "$f"
      else
        $xml ed -L -s /settings -t elem -n lanbatnew -v "$value" \
          -i //lanbatnew -t attr -n id -v "$id" -r //lanbatnew -v setting "$f"
      fi
    }
    # Kodi's v2 add-on settings store the value as element text, not in a
    # value attribute; escape credentials for the XML.
    xmlenc() {
      printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' -e 's/"/\&quot;/g'
    }
    set_setting services.webserver true
    set_setting services.webserverport ${toString kodiWebPort}
    set_setting services.webserverauthentication true
    set_setting services.webserverusername kodi
    set_setting services.webserverpassword "$(cat ${config.lanbat.secrets.kodi-web-password.path})"
    set_setting services.webserverssl false
    set_setting services.esenabled true
    set_setting services.esallinterfaces true
    # Kodi plays through the system PipeWire, as PCM, so the music, films and
    # the assistant's replies share the speakers and the echo canceller.
    set_setting audiooutput.audiodevice ${kodiAudioDevice}
    set_setting audiooutput.passthrough false
    ${lib.optionalString kodiCfg.youtube youtubeSetup}
    ${lib.optionalString kodiCfg.twitch twitchSetup}
    ${lib.optionalString kodiCfg.opensubtitles opensubtitlesSetup}
    ${lib.optionalString kodiCfg.invidious invidiousSetup}
    chown -R media:media ${home}/.kodi/userdata
    chmod 0600 "$f"
  '';
  # A Linux Voice Assistant satellite on this host: Kodi pauses its video and
  # shows captions while you talk, ducks its music, and switches the TV over
  # CEC for Home Assistant (pkgs/lva-kodi-companion). With the satellite's
  # echoCancellation.includeMusic, Kodi plays into the echo canceller's sink so
  # films and music are taken out of the microphone too.
  satellite = config.lanbat.voiceSatellite;
  lvaHere = satellite.enable && satellite.backend == "lva";
  kodiAudioDevice =
    if lvaHere && satellite.echoCancellation.enable && satellite.echoCancellation.includeMusic then
      "PIPEWIRE:${satellite.echoCancellation.pulseSinkName}"
    else
      "PIPEWIRE:Default";
  lvaKodiCompanion = pkgs.callPackage ../../pkgs/lva-kodi-companion { };
  kodiTvConfig = pkgs.callPackage ../../pkgs/kodi-tv-config { };
  # Estuary with its Power buttons opening script.tv-power; installed as a user
  # add-on over the bundled skin.
  estuaryPowerSkin = pkgs.callPackage ../../pkgs/kodi-skin-estuary-power { };
  kodiBootstrap = pkgs.callPackage ../../pkgs/kodi-bootstrap { };

  # Tuned for the Raspberry Pi 5: emulators that run well on it. Another board
  # would want its own list.
  cores = with pkgs.libretro; [
    fceumm # NES
    nestopia
    snes9x # SNES
    genesis-plus-gx # Master System, Game Gear, Genesis, Sega CD
    picodrive # 32X
    gambatte # Game Boy, Game Boy Color
    mgba # Game Boy Advance
    beetle-pce-fast # PC Engine
    beetle-ngp # Neo Geo Pocket
    beetle-wswan # WonderSwan
    beetle-lynx # Lynx
    stella # Atari 2600
    prosystem # Atari 7800
    pcsx-rearmed # PlayStation
    swanstation
    mupen64plus # Nintendo 64
    melonds # Nintendo DS
    flycast # Dreamcast
    fbneo # arcade
    mame # arcade, current MAME romsets
  ];

  retroarch = pkgs.retroarch-bare.wrapper {
    inherit cores;
    # Applied on every start, over the user's retroarch.cfg.
    settings = {
      input_joypad_driver = "udev";
      input_autodetect_enable = "true";
      joypad_autoconfig_dir = "${pkgs.retroarch-joypad-autoconfig}/share/libretro/autoconfig";
      input_quit_gamepad_combo = "4"; # Select + Start
      input_menu_toggle_gamepad_combo = "7"; # hold Start
      video_fullscreen = "true";
      system_directory = "${romDirectory}/bios";
    };
  };

  # ES-DE's launch commands name the cores with underscores
  # (genesis_plus_gx_libretro.so), nixpkgs with hyphens.
  esdeCoreNames = pkgs.runCommand "es-de-core-names" { } (
    ''
      mkdir -p $out/lib/retroarch/cores
    ''
    + lib.concatMapStrings (
      core:
      lib.optionalString (lib.hasInfix "-" core.core) ''
        ln -s ${core}${core.libretroCore}/${core.core}_libretro.so \
          $out/lib/retroarch/cores/${lib.replaceStrings [ "-" ] [ "_" ] core.core}_libretro.so
      ''
    ) cores
  );

  es-de = pkgs.callPackage ../../pkgs/es-de { };

  # A frontend that is quit returns the screen to Kodi: the kiosk sessions have
  # no window manager to fall back to, and nothing returns to tv-kodi on its
  # own, so quitting ES-DE or chiaki-ng left the screen blank (2026-11-01).
  # tv-switch writes the state file before it starts the session it was asked
  # for, so a switch to another session (or to Kodi) does not bounce through
  # Kodi here. Only a clean exit returns; a crash is left to the unit's
  # restart.
  returnToKodi = pkgs.writeShellApplication {
    name = "tv-return-to-kodi";
    runtimeInputs = [ pkgs.systemd ];
    text = ''
      [ "$(cat /var/lib/tv-session/current 2>/dev/null || true)" = "$1" ] || exit 0
      exec systemctl start --no-block tv-kodi.service
    '';
  };

  # cage modesets the output to its EDID-preferred mode: 3840x2160@60 on the
  # Epson projector, which shows nothing on that signal, while Kodi has always
  # run at 1920x1080. wlr-randr talks to the compositor cage starts (the child
  # inherits its WAYLAND_DISPLAY); both kiosk sessions run this before their
  # frontend creates its window.
  tvDisplay = pkgs.writeShellApplication {
    name = "tv-display";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.gnused
      pkgs.wlr-randr
    ];
    text = ''
      # cage forks the frontend once the Wayland socket exists, which can be a
      # moment before the compositor advertises the output: wait for it.
      try=0
      names=""
      while [ -z "$names" ] && [ "$try" -lt 20 ]; do
        names=$(wlr-randr 2>/dev/null | sed -n 's/^\([^ ]*\) ".*/\1/p' || true)
        try=$((try + 1))
        [ -n "$names" ] || sleep 0.2
      done
      [ -n "$names" ] || echo "tv-display: no Wayland outputs found" >&2
      while IFS= read -r output; do
        [ -n "$output" ] || continue
        wlr-randr --output "$output" --mode 1920x1080 ||
          echo "tv-display: $output: could not set 1920x1080" >&2
      done <<< "$names"
    '';
  };

  # --no-update-check keeps the AppImage's update prompt ("NEW RELEASE
  # AVAILABLE") off the screen; the store copy cannot update itself anyway.
  tvGames = pkgs.writeShellApplication {
    name = "tv-games";
    runtimeInputs = [ ];
    text = ''
      ${lib.getExe tvDisplay}
      set +e
      ${lib.getExe es-de} --no-update-check
      status=$?
      [ "$status" -eq 0 ] && ${lib.getExe returnToKodi} games
      exit "$status"
    '';
  };

  tvChiaki = pkgs.writeShellApplication {
    name = "tv-chiaki";
    runtimeInputs = [ ];
    text = ''
      ${lib.getExe tvDisplay}
      set +e
      ${lib.getExe pkgs."chiaki-ng"}
      status=$?
      [ "$status" -eq 0 ] && ${lib.getExe returnToKodi} chiaki
      exit "$status"
    '';
  };

  # ES-DE uses the first emulator listed for a system. Where its default isn't
  # installed or runs poorly on a Pi, list this one first.
  preferredEmulators = {
    nes = "FCEUmm";
    pcengine = "Beetle PCE FAST";
    psx = "PCSX ReARMed";
    psp = "PPSSPP (Standalone)";
    nds = "melonDS";
  };

  switchToKodi = pkgs.writeTextDir "Kodi.sh" ''
    exec /run/current-system/sw/bin/tv-switch kodi
  '';

  esdeSystems =
    pkgs.runCommand "es_systems.xml"
      {
        nativeBuildInputs = [ pkgs.python3 ];
        preferences = builtins.toJSON preferredEmulators;
        passAsFile = [ "preferences" ];
      }
      ''
        python3 ${../../pkgs/tv-session/es-de-systems.py} \
          ${es-de.systems} "$preferencesPath" ${switchToKodi} $out
      '';

  # Seeded once: ES-DE rewrites its settings file.
  esdeSettings = pkgs.writeText "es_settings.xml" ''
    <?xml version="1.0"?>
    <string name="ROMDirectory" value="${romDirectory}" />
  '';

  # The Kodi add-on packages (pkgs/kodi-gbm's passthru), used to build the
  # Twitch add-on (which nixpkgs does not carry) and the newer Invidious
  # add-on than nixpkgs pins.
  kodiPkgs = pkgs.kodi-gbm.packages;
  pythonTwitchAddon = pkgs.callPackage ../../pkgs/kodi-addon-python-twitch {
    buildKodiAddon = kodiPkgs.buildKodiAddon;
    six = kodiPkgs.six;
    requests = kodiPkgs.requests;
  };
  twitchAddon = pkgs.callPackage ../../pkgs/kodi-addon-twitch {
    buildKodiAddon = kodiPkgs.buildKodiAddon;
    requests = kodiPkgs.requests;
    pythonTwitch = pythonTwitchAddon;
  };
  invidiousAddon = pkgs.callPackage ../../pkgs/kodi-addon-invidious {
    buildKodiAddon = kodiPkgs.buildKodiAddon;
    infotagger = kodiPkgs.infotagger;
    requests = kodiPkgs.requests;
    inputstream-adaptive = kodiPkgs.inputstream-adaptive;
    inputstreamhelper = kodiPkgs.inputstreamhelper;
  };
  # The TV power menu (which replaces Kodi's shutdown menu) and the Global
  # Search add-on, both installed regardless of the options above.
  tvPowerAddon = pkgs.callPackage ../../pkgs/kodi-addon-tv-power {
    buildKodiAddon = kodiPkgs.buildKodiAddon;
  };
  globalsearchAddon = pkgs.callPackage ../../pkgs/kodi-addon-globalsearch {
    buildKodiAddon = kodiPkgs.buildKodiAddon;
  };
  adulthideoutAddon = pkgs.callPackage ../../pkgs/kodi-addon-adulthideout {
    buildKodiAddon = kodiPkgs.buildKodiAddon;
    requests = kodiPkgs.requests;
  };
  # withPackages takes the add-ons to install and resolves their dependencies
  # (requiredKodiAddons), so listing the Twitch add-on also pulls in its python
  # module and its six/requests dependencies.
  kodi = pkgs.kodi-gbm.withPackages (
    addons:
    [
      addons.joystick
      tvPowerAddon
      globalsearchAddon
    ]
    ++ lib.optionals kodiCfg.youtube [ addons.youtube ]
    ++ lib.optionals kodiCfg.twitch [ twitchAddon ]
    ++ lib.optionals kodiCfg.opensubtitles [ addons.a4ksubtitles ]
    ++ lib.optionals kodiCfg.invidious [ invidiousAddon ]
    ++ lib.optionals kodiCfg.adulthideout [ adulthideoutAddon ]
  );

  # Seeded once: Kodi rewrites its favourites.
  kodiFavourites = pkgs.writeText "favourites.xml" ''
    <favourites>
      <favourite name="Power menu">RunScript(script.tv-power)</favourite>
      <favourite name="Global Search">RunScript(script.globalsearch)</favourite>
      <favourite name="Games (EmulationStation)">System.Exec(&quot;/run/current-system/sw/bin/tv-switch-games&quot;)</favourite>
      <favourite name="chiaki-ng">System.Exec(&quot;/run/current-system/sw/bin/tv-switch-chiaki&quot;)</favourite>
    </favourites>
  '';

  tv-switch = pkgs.writeShellApplication {
    name = "tv-switch";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.systemd
    ];
    text = builtins.readFile ../../pkgs/tv-session/tv-switch.sh;
  };

  # One-word entries for the power menu and the favourites. Kodi's System.Exec
  # does not use a shell: it splits its parameter on commas and calls execvp,
  # so "tv-switch games" is looked up as a program name containing a space and
  # fails silently (no output, no log). These wrappers take the argument.
  tvSwitchTo =
    session:
    pkgs.writeShellScriptBin "tv-switch-${session}" ''
      exec ${lib.getExe tv-switch} ${session}
    '';

  tv-hotkey = pkgs.writers.writePython3Bin "tv-hotkey" {
    libraries = [ pkgs.python3Packages.evdev ];
    flakeIgnore = [ "E501" ];
  } (builtins.readFile ../../pkgs/tv-session/tv-hotkey.py);

  session =
    {
      extraAfter ? [ ],
      description,
      other,
      command,
      extraConflicts ? [ ],
    }:
    {
      inherit description;
      conflicts = [ other ] ++ extraConflicts;
      after = [
        "systemd-user-sessions.service"
        "systemd-logind.service"
        "sound.target"
        "pipewire.service"
      ]
      ++ extraAfter;
      # Kodi's PipeWire output does not reconnect: after PipeWire restarted
      # (any deploy that changes the audio setup does that) every film played
      # in silence until Kodi was restarted too ("CActiveAESink::OpenSink - no
      # sink was returned", silent from 2026-10-13 to 2026-10-22). The session
      # restarts with PipeWire, so it comes back with sound.
      partOf = [ "pipewire.service" ];
      # Emulators and tv-switch are looked up in the system profile.
      path = [ "/run/current-system/sw" ];
      startLimitBurst = 5;
      startLimitIntervalSec = 60;
      serviceConfig = {
        ExecStart = command;
        User = "media";
        # A logind session on tty1 gives the session the display and input
        # devices. Audio goes to the system-wide PipeWire (modules/pi/audio.nix).
        PAMName = "login";
        # pam_systemd gives the session an ambient CAP_WAKE_ALARM (so rtcwake
        # works unprivileged). bubblewrap refuses to start when it inherits a
        # capability but is not setuid ("Unexpected capabilities but not
        # setuid"), which broke ES-DE: its AppImage runs in an FHS environment
        # built on bubblewrap. No session needs a capability, so bound them all
        # away.
        CapabilityBoundingSet = "";
        TTYPath = "/dev/tty1";
        TTYReset = true;
        TTYVHangup = true;
        TTYVTDisallocate = true;
        StandardInput = "tty-fail";
        StandardOutput = "journal";
        StandardError = "journal";
        UtmpIdentifier = "tty1";
        UtmpMode = "user";
        Restart = "on-failure";
        RestartSec = "2s";
      };
    };
in
{
  config = {
    # The schema is merged into lanbat.services.kodi.settings (checks.nix
    # rejects undeclared keys); the defaults are set field by field, so a
    # profile's change to one field keeps the rest.
    lanbat.settingsSchema.kodi = kodiSettings;
    lanbat.services.kodi.settings = {
      videoSources =
        lib.mapAttrs (_: lib.mapAttrs (_: lib.mkDefault)) videoSourceDefaults
        // lib.optionalAttrs kodiCfg.privateLibrary {
          # The folder behind the private group. Its key names it "Adult" in
          # Videos > Files; the fields stay overridable like the defaults.
          adult = lib.mapAttrs (_: lib.mkDefault) {
            path = "/mnt/storage-b/media/adult/";
            content = "movies";
            scraper = "metadata.local";
          };
        };
      musicSources = lib.mapAttrs (_: lib.mkDefault) musicSourceDefaults;
    };

    # Kodi's remote control, for Home Assistant (see the top).
    lanbat.services.kodi = {
      endpoint = {
        scheme = "http";
        port = kodiWebPort;
      };
      secrets.kodi-web-password.owner = "root";
      # The add-on's credentials, written into its addon_data by kodiRemote
      # (root), so both secrets are owned by root like kodi-web-password.
      secrets.kodi-youtube-env = {
        enable = kodiCfg.youtube;
        owner = "root";
      };
      secrets.kodi-twitch-token = {
        enable = kodiCfg.twitch;
        owner = "root";
      };
      secrets.kodi-opensubtitles-env = {
        enable = kodiCfg.opensubtitles;
        owner = "root";
      };
    };
    lanbat.services.kodi-events.endpoint = {
      scheme = "tcp";
      port = kodiEventsPort;
    };

    lanbat.voiceSatellite.lva.peripheralApiUsers = lib.mkIf lvaHere [ "lva-kodi-companion" ];
    lanbat.voiceSatellite.lva.snapcastDucking.programs = lib.mkIf lvaHere (
      lib.mkOptionDefault [ "kodi.bin" ]
    );
    # Kodi opens a new stream for each film, and WirePlumber gave it the last
    # one's volume: a film that ended or a Kodi that restarted while ducked
    # left the next one at the duck level, and each duck took it lower, to
    # 0.000003 measured on the Pi 5, 2026-10-07 (as Snapcast's, in
    # modules/core/snapclient.nix). Kodi's own volume is the one to turn.
    services.pipewire.wireplumber.extraConfig."51-kodi-volume"."stream.rules" = lib.mkIf lvaHere [
      {
        matches = [ { "application.name" = "Kodi"; } ];
        actions.update-props."state.restore-props" = false;
      }
    ];

    users.users.media = {
      uid = 1000;
      isNormalUser = true;
      group = "media";
      extraGroups = [
        "audio"
        "pipewire"
        "video"
        "input"
        "render"
        # Not "private" by default: media/adult is for that group alone, and
        # anyone at the TV is this account. settings.privateLibrary adds it.
      ]
      ++ lib.optionals kodiCfg.privateLibrary [ "private" ];
    };

    hardware.graphics.enable = true;
    hardware.bluetooth = {
      enable = true;
      powerOnBoot = true;
    };
    hardware.xpadneo.enable = true;
    boot.kernelModules = [ "joydev" ];

    environment.systemPackages = [
      kodi
      retroarch
      esdeCoreNames
      pkgs.ppsspp-sdl
      es-de
      pkgs."chiaki-ng"
      tv-switch
      (tvSwitchTo "games")
      (tvSwitchTo "chiaki")
    ];
    # ES-DE looks for the cores in /run/current-system/sw/lib/retroarch/cores.
    environment.pathsToLink = [ "/lib/retroarch" ];

    # The Invidious add-on reaches youtube.<domain> over the internal CA (see
    # tv-kodi's REQUESTS_CA_BUNDLE).  Installing the root here covers
    # inputstream.adaptive's curl, which reads the system bundle.
    security.pki.certificateFiles = lib.mkIf kodiCfg.invidious [
      config.lanbat.deployment.secrets.caCertificate
    ];

    systemd.services = {
      # tty1 belongs to the TV sessions; consoles remain on tty2 and up.
      "getty@tty1".enable = false;
      "autovt@tty1".enable = false;

      tv-kodi =
        lib.recursiveUpdate
          (session {
            description = "Kodi on the TV";
            other = "tv-games.service";
            extraConflicts = [ "tv-chiaki.service" ];
            command = "${kodi}/bin/kodi-standalone";
            extraAfter = [ "kodi-bootstrap.service" ] ++ unlockUnits;
          })
          {
            # kodi-bootstrap rewrites the library sources: stop Kodi before it
            # does, and let a deploy that changes a source bring Kodi back with
            # the new library instead of leaving the old one running.
            partOf = [
              "pipewire.service"
              "kodi-bootstrap.service"
            ];
            serviceConfig.ExecStartPre = [ "+${kodiRemote}" ];
            # Kodi's requests module trusts its bundled certifi, not the system
            # bundle, so the Invidious add-on could not reach the instance's
            # internal-CA certificate. inputstream.adaptive (the DASH stream)
            # uses the system bundle, which the CA above joins.
            environment = lib.optionalAttrs kodiCfg.invidious {
              REQUESTS_CA_BUNDLE = "/etc/ssl/certs/ca-certificates.crt";
              SSL_CERT_FILE = "/etc/ssl/certs/ca-certificates.crt";
              INVIDIOUS_URL = invidiousUrl;
            };
          };
      tv-games =
        session {
          description = "EmulationStation on the TV";
          other = "tv-kodi.service";
          extraConflicts = [ "tv-chiaki.service" ];
          command = "${lib.getExe pkgs.cage} -s -- ${lib.getExe tvGames}";
        }
        // {
          # cage exports DISPLAY=:0 for Xwayland, so SDL would try X11/GLX and
          # fail; the kiosk compositor is the display. The Pi's v3d reports
          # OpenGL 3.1, and ES-DE's renderer asks for 3.3, so Mesa has to be told
          # the driver can do it (ES-DE's INSTALL.md documents this for the
          # Raspberry Pi).
          environment = {
            SDL_VIDEODRIVER = "wayland";
            MESA_GL_VERSION_OVERRIDE = "3.3";
          };
        };
      tv-chiaki = session {
        description = "chiaki-ng (PS4/PS5 remote play) on the TV";
        other = "tv-kodi.service";
        extraConflicts = [ "tv-games.service" ];
        command = "${lib.getExe pkgs.cage} -s -- ${lib.getExe tvChiaki}";
      };

      lva-kodi-companion = lib.mkIf lvaHere {
        description = "Kodi's side of the voice satellite: pause, captions, TV power";
        after = [
          "linux-voice-assistant.service"
          "tv-kodi.service"
        ];
        wants = [ "linux-voice-assistant.service" ];
        partOf = [ "linux-voice-assistant.service" ];
        # Stopped with LVA (partOf) and started with it again.
        wantedBy = [
          "multi-user.target"
          "linux-voice-assistant.service"
        ];
        environment = {
          LVA_PERIPHERAL_URL = "ws://127.0.0.1:${toString satellite.lva.peripheralPort}";
          KODI_PORT = toString kodiEventsPort;
          # A film paused for a question resumes this far back.
          RESUME_REWIND_SECONDS = toString kodiCfg.resumeRewindSeconds;
        };
        serviceConfig = {
          ExecStart = lib.getExe lvaKodiCompanion;
          DynamicUser = true;
          Restart = "on-failure";
          RestartSec = "5s";
          # Loopback only: LVA's WebSocket, Kodi's JSON-RPC and EventServer.
          IPAddressDeny = "any";
          IPAddressAllow = "localhost";
          RestrictAddressFamilies = [
            "AF_INET"
            "AF_INET6"
          ];
          ProtectHome = true;
          PrivateDevices = true;
          NoNewPrivileges = true;
        };
      };

      kodi-bootstrap = {
        description = "Wait for Pi storage before Kodi scans libraries";
        wantedBy = [ "multi-user.target" ];
        after = unlockUnits;
        serviceConfig = {
          Type = "oneshot";
          RemainAfterExit = true;
          User = "root";
        };
        path = [ kodiBootstrap ];
        environment = {
          KODI_HOME = home;
          KODI_VIDEO_SOURCES = videoSourcesEnv;
          KODI_MUSIC_SOURCES = musicSourcesEnv;
        };
        script = "exec kodi-bootstrap";
      };

      # Kodi's startup update rescans only the music folders it has scanned
      # before, so a music source nothing scanned stays empty: this runs the
      # first scan of each, through Kodi's JSON-RPC on the loopback.
      kodi-music-scan = {
        description = "First scan of the music sources Kodi hasn't scanned";
        wantedBy = [ "tv-kodi.service" ];
        after = [ "tv-kodi.service" ];
        partOf = [ "tv-kodi.service" ];
        serviceConfig = {
          Type = "oneshot";
          # Reads the media user's music database.
          User = "root";
        };
        path = [ kodiBootstrap ];
        environment = {
          KODI_HOME = home;
          KODI_PORT = toString kodiEventsPort;
          KODI_MUSIC_SOURCES = musicSourcesEnv;
        };
        script = "exec kodi-bootstrap music-scan";
      };

      tv-session = {
        description = "Start the last TV session";
        wantedBy = [ "multi-user.target" ];
        after = [
          "systemd-user-sessions.service"
          "kodi-bootstrap.service"
        ];
        serviceConfig.Type = "oneshot";
        script = ''
          session=$(cat /var/lib/tv-session/current 2>/dev/null || echo kodi)
          case "$session" in
            kodi | games | chiaki) ;;
            *) session=kodi ;;
          esac
          systemctl start --no-block "tv-$session.service"
        '';
      };

      tv-hotkey = {
        description = "Controller shortcut that switches TV sessions";
        wantedBy = [ "multi-user.target" ];
        environment.TV_SWITCH = lib.getExe tv-switch;
        serviceConfig = {
          ExecStart = lib.getExe tv-hotkey;
          Restart = "always";
          RestartSec = "5s";
        };
      };
    };

    systemd.tmpfiles.rules = [
      "d /var/lib/tv-session 0775 root media -"

      "d ${home}/ES-DE 0755 media media -"
      "d ${home}/ES-DE/custom_systems 0755 media media -"
      "L+ ${home}/ES-DE/custom_systems/es_systems.xml - - - - ${esdeSystems}"
      "d ${home}/ES-DE/settings 0755 media media -"
      "C ${home}/ES-DE/settings/es_settings.xml - - - - ${esdeSettings}"
      "z ${home}/ES-DE/settings/es_settings.xml 0644 media media -"

      "d ${home}/.kodi 0755 media media -"
      "d ${home}/.kodi/userdata 0755 media media -"
      # Estuary whose Power buttons open script.tv-power, overriding the bundled
      # skin (Kodi prefers ~/.kodi/addons over the system add-ons with the same
      # id), so the home-screen Power button shows the box's own menu.
      "d ${home}/.kodi/addons 0755 media media -"
      "L+ ${home}/.kodi/addons/skin.estuary - - - - ${estuaryPowerSkin}"
      # Kodi never writes advancedsettings.xml: a link keeps it the
      # repository's (a copy made once never saw a later change).
      "L+ ${home}/.kodi/userdata/advancedsettings.xml - - - - ${kodiTvConfig}/advancedsettings.xml"
      # The power key opens the TV power menu instead of Kodi's shutdown menu.
      "d ${home}/.kodi/userdata/keymaps 0755 media media -"
      "L+ ${home}/.kodi/userdata/keymaps/power-menu.xml - - - - ${kodiTvConfig}/keymaps/power-menu.xml"
      "C ${home}/.kodi/userdata/sources.xml - - - - ${kodiTvConfig}/sources.xml"
      "C ${home}/.kodi/userdata/favourites.xml - - - - ${kodiFavourites}"
      "z ${home}/.kodi/userdata/favourites.xml 0644 media media -"
    ];

    # The media user may switch TV sessions and reboot, and nothing else. The
    # box is always on: power-off is not granted, and the power menu offers it
    # to no one.
    security.polkit.enable = true;
    security.polkit.extraConfig = ''
      polkit.addRule(function (action, subject) {
        if (subject.user != "media") {
          return polkit.Result.NOT_HANDLED;
        }
        if (action.id == "org.freedesktop.systemd1.manage-units") {
          var unit = action.lookup("unit");
          var verb = action.lookup("verb");
          if ((unit == "tv-kodi.service" || unit == "tv-games.service" || unit == "tv-chiaki.service") &&
              (verb == "start" || verb == "restart")) {
            return polkit.Result.YES;
          }
        }
        if (action.id == "org.freedesktop.login1.reboot") {
          return polkit.Result.YES;
        }
        return polkit.Result.NOT_HANDLED;
      });
    '';
  };
}
