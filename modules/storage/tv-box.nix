# modules/storage/tv-box.nix
#
# Raspberry Pi based TV box: Kodi and a games frontend on the Pi's HDMI output,
# with the films, music and ROMs on the storage drives (loaded via the lanbat-tv
# plugin, for the storage-pi role).
#
# Two sessions take turns on the screen. Each is a systemd service that runs
# as the "media" user on tty1, and each conflicts with the other, so starting
# one stops the other:
#   tv-kodi.service   Kodi, drawing directly to the display (GBM)
#   tv-games.service  ES-DE (EmulationStation) in the cage kiosk compositor;
#                     the emulators it starts run in the same session
#
# Switching sessions: `tv-switch kodi|games|toggle` starts one and remembers
# it in /var/lib/tv-session/current, so the Pi boots back into it.
#   - In Kodi: Favourites → "Games (EmulationStation)".
#   - In ES-DE: the "Kodi" system.
#   - On any controller: hold the Guide button for 2 seconds, or Select and
#     Start for 3 (tv-hotkey.service; works even when an emulator hangs).
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
  # every boot). The defaults are the repository's media layout; a profile
  # sets only what differs, e.g. lanbat.services.kodi.settings.videoSources.tv.path.
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
      content = "movies";
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
        example = lib.literalExpression ''{ tv.path = "/mnt/storage-a/media/tv/misc/"; }'';
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
    };
  };
  kodiCfg = config.lanbat.services.kodi.settings;
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

  kodi = pkgs.kodi-gbm.withPackages (addons: [ addons.joystick ]);

  # Seeded once: Kodi rewrites its favourites.
  kodiFavourites = pkgs.writeText "favourites.xml" ''
    <favourites>
      <favourite name="Games (EmulationStation)">System.Exec(&quot;/run/current-system/sw/bin/tv-switch games&quot;)</favourite>
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
    }:
    {
      inherit description;
      conflicts = [ other ];
      after = [
        "systemd-user-sessions.service"
        "systemd-logind.service"
        "sound.target"
      ]
      ++ extraAfter;
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
      videoSources = lib.mapAttrs (_: lib.mapAttrs (_: lib.mkDefault)) videoSourceDefaults;
      musicSources = lib.mapAttrs (_: lib.mkDefault) musicSourceDefaults;
    };

    # Kodi's remote control, for Home Assistant (see the top).
    lanbat.services.kodi = {
      endpoint = {
        scheme = "http";
        port = kodiWebPort;
      };
      secrets.kodi-web-password.owner = "root";
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
        # Not "private": media/adult is for that group alone, and anyone at the
        # TV is this account.
      ];
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
      tv-switch
    ];
    # ES-DE looks for the cores in /run/current-system/sw/lib/retroarch/cores.
    environment.pathsToLink = [ "/lib/retroarch" ];

    systemd.services = {
      # tty1 belongs to the TV sessions; consoles remain on tty2 and up.
      "getty@tty1".enable = false;
      "autovt@tty1".enable = false;

      tv-kodi = lib.recursiveUpdate (session {
        description = "Kodi on the TV";
        other = "tv-games.service";
        command = "${kodi}/bin/kodi-standalone";
        extraAfter = [ "kodi-bootstrap.service" ] ++ unlockUnits;
      }) { serviceConfig.ExecStartPre = [ "+${kodiRemote}" ]; };
      tv-games = session {
        description = "EmulationStation on the TV";
        other = "tv-kodi.service";
        command = "${lib.getExe pkgs.cage} -s -- ${lib.getExe es-de}";
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
            kodi | games) ;;
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
      # Kodi never writes advancedsettings.xml: a link keeps it the
      # repository's (a copy made once never saw a later change).
      "L+ ${home}/.kodi/userdata/advancedsettings.xml - - - - ${kodiTvConfig}/advancedsettings.xml"
      "C ${home}/.kodi/userdata/sources.xml - - - - ${kodiTvConfig}/sources.xml"
      "C ${home}/.kodi/userdata/favourites.xml - - - - ${kodiFavourites}"
      "z ${home}/.kodi/userdata/favourites.xml 0644 media media -"
    ];

    # The media user may switch TV sessions, reboot and power off, and nothing
    # else.
    security.polkit.enable = true;
    security.polkit.extraConfig = ''
      polkit.addRule(function (action, subject) {
        if (subject.user != "media") {
          return polkit.Result.NOT_HANDLED;
        }
        if (action.id == "org.freedesktop.systemd1.manage-units") {
          var unit = action.lookup("unit");
          var verb = action.lookup("verb");
          if ((unit == "tv-kodi.service" || unit == "tv-games.service") &&
              (verb == "start" || verb == "restart")) {
            return polkit.Result.YES;
          }
        }
        if (action.id == "org.freedesktop.login1.power-off" ||
            action.id == "org.freedesktop.login1.reboot") {
          return polkit.Result.YES;
        }
        return polkit.Result.NOT_HANDLED;
      });
    '';
  };
}
