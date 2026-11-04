# tests/pkgs-build.nix
#
# Build custom derivations under pkgs/ so CI catches packaging errors before deploy.
{ pkgs }:

let
  domain = "home.example.com";
  scripts = pkgs.callPackage ../pkgs/scripts { inherit domain; };
  pages = pkgs.callPackage ../pkgs/service-unavailable-page { inherit domain; };
  caPage = pkgs.callPackage ../pkgs/ca-landing-page { };
  dashboards = pkgs.callPackage ../pkgs/grafana-dashboards { };
  kodiTvConfig = pkgs.callPackage ../pkgs/kodi-tv-config { };
  kodiBootstrap = pkgs.callPackage ../pkgs/kodi-bootstrap { };
  kodiKeyboardLayout = pkgs.callPackage ../pkgs/kodi-keyboard-layout { };
  kodiVolumeKeys = pkgs.callPackage ../pkgs/kodi-volume-keys { };
  xkbPhoneticRoot = pkgs.callPackage ../pkgs/xkb-phonetic-root { };
  # The bootstrap adds a video source the seeded sources.xml predates (the
  # documentary series) and leaves the rest of the file alone.
  kodiBootstrapSources =
    pkgs.runCommand "kodi-bootstrap-sources-xml"
      {
        nativeBuildInputs = [ kodiBootstrap ];
      }
      ''
        mkdir -p $TMPDIR/.kodi/userdata $TMPDIR/media/documentary-series $TMPDIR/media/music-videos $TMPDIR/media/adult
        cat > $TMPDIR/.kodi/userdata/sources.xml <<EOF
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <sources>
          <video>
            <default pathversion="1"></default>
            <source>
              <name>old-name</name>
              <path>$TMPDIR/media/music-videos/</path>
            </source>
          </video>
        </sources>
        EOF
        KODI_HOME=$TMPDIR \
          KODI_VIDEO_SOURCES="documentary-series|$TMPDIR/media/documentary-series/|tvshows|metadata.tvshows.themoviedb.org.python|0|0
        music-videos|$TMPDIR/media/music-videos/|musicvideos|metadata.local|1|0" \
          KODI_FILE_SOURCES="Private|$TMPDIR/media/adult/" \
          kodi-bootstrap sources-xml
        grep -q "<name>Documentary Series</name>" $TMPDIR/.kodi/userdata/sources.xml
        grep -q "$TMPDIR/media/documentary-series/" $TMPDIR/.kodi/userdata/sources.xml
        grep -q "<name>Music Videos</name>" $TMPDIR/.kodi/userdata/sources.xml
        grep -q "<name>Private</name>" $TMPDIR/.kodi/userdata/sources.xml
        grep -q "$TMPDIR/media/adult/" $TMPDIR/.kodi/userdata/sources.xml
        ! grep -q "old-name" $TMPDIR/.kodi/userdata/sources.xml
        touch $out
      '';
  kodiInvidious = pkgs.callPackage ../pkgs/kodi-addon-invidious {
    buildKodiAddon = pkgs.kodi-gbm.packages.buildKodiAddon;
    infotagger = pkgs.kodi-gbm.packages.infotagger;
    requests = pkgs.kodi-gbm.packages.requests;
    inputstream-adaptive = pkgs.kodi-gbm.packages.inputstream-adaptive;
    inputstreamhelper = pkgs.kodi-gbm.packages.inputstreamhelper;
  };
  kodiTvPower = pkgs.callPackage ../pkgs/kodi-addon-tv-power {
    buildKodiAddon = pkgs.kodi-gbm.packages.buildKodiAddon;
  };
  kodiGlobalsearch = pkgs.callPackage ../pkgs/kodi-addon-globalsearch {
    buildKodiAddon = pkgs.kodi-gbm.packages.buildKodiAddon;
  };
  kodiAdulthideout = pkgs.callPackage ../pkgs/kodi-addon-adulthideout {
    buildKodiAddon = pkgs.kodi-gbm.packages.buildKodiAddon;
    requests = pkgs.kodi-gbm.packages.requests;
  };
  kodiPrivateMedia = pkgs.callPackage ../pkgs/kodi-addon-private-media {
    buildKodiAddon = pkgs.kodi-gbm.packages.buildKodiAddon;
  };
  androidProvision = pkgs.callPackage ../pkgs/android-provision { };
  xiaomiClockSync = pkgs.callPackage ../pkgs/xiaomi-clock-sync { };
  feedBridge = pkgs.callPackage ../pkgs/media-feed-bridge { };
  maSources = pkgs.callPackage ../pkgs/music-assistant-sources { };
  auntieSounds = pkgs.callPackage ../pkgs/auntie-sounds { };
  linuxVoiceAssistant = pkgs.callPackage ../pkgs/linux-voice-assistant { };
  lvaHeyNabu = pkgs.callPackage ../pkgs/lva-wakewords-hey-nabu { };
  lvaWakeupChime = pkgs.callPackage ../pkgs/lva-wakeup-chime { };
  lvaSnapcastDuck = pkgs.callPackage ../pkgs/lva-snapcast-duck { };
  lvaKodiCompanion = pkgs.callPackage ../pkgs/lva-kodi-companion { };
  haDashboards = pkgs.callPackage ../pkgs/home-assistant-dashboards { };
  voiceId = pkgs.callPackage ../pkgs/voice-id { };
  assistantRouter = pkgs.callPackage ../pkgs/assistant-router { };
  ytShow = pkgs.callPackage ../pkgs/yt-show { };
  frigatePersonMapper = pkgs.callPackage ../pkgs/frigate-person-mapper { };
in
pkgs.runCommand "pkgs-build-smoke"
  {
    nativeBuildInputs = [
      scripts
      pages
      caPage
      dashboards
      kodiBootstrap
      kodiInvidious
      kodiTvPower
      kodiGlobalsearch
      kodiAdulthideout
      kodiPrivateMedia
      kodiKeyboardLayout
      kodiVolumeKeys
      xkbPhoneticRoot
      androidProvision
      xiaomiClockSync
      feedBridge
      maSources
      linuxVoiceAssistant
      lvaSnapcastDuck
      lvaKodiCompanion
      haDashboards
      voiceId
      assistantRouter
      ytShow
      frigatePersonMapper
    ];
  }
  ''
    command -v backup-server
    command -v quota-setup
    command -v kodi-bootstrap
    test -e ${kodiBootstrapSources}
    command -v android-provision
    command -v media-feed-bridge
    command -v music-assistant-sources
    test -d ${auntieSounds}/${pkgs.python3.sitePackages}/sounds
    XIAOMI_CLOCK_DEVICES= xiaomi-clock-sync
    test -f ${pages}/offline.html
    test -f ${caPage}/index.html
    test -d ${dashboards}
    test -f ${kodiTvConfig}/sources.xml
    test -f ${kodiTvConfig}/keymaps/keyboard-layout.xml
    grep -q 'SendClick(309)' ${kodiTvConfig}/keymaps/keyboard-layout.xml
    test -f ${kodiTvConfig}/keymaps/power-menu.xml
    grep -q 'RunScript(script.tv-power)' ${kodiTvConfig}/keymaps/power-menu.xml
    test -f ${kodiInvidious}/share/kodi/addons/plugin.video.invidious/addon.xml
    test -f ${kodiTvPower}/share/kodi/addons/script.tv-power/addon.xml
    test -f ${kodiTvPower}/share/kodi/addons/script.tv-power/default.py
    # Kodi's System.Exec execs its parameter, splitting arguments on commas, so
    # the power menu must call the one-word session wrappers, not "tv-switch X".
    grep -q 'tv-switch-games' ${kodiTvPower}/share/kodi/addons/script.tv-power/default.py
    grep -q 'tv-switch-chiaki' ${kodiTvPower}/share/kodi/addons/script.tv-power/default.py
    ! grep -qE 'System\.Exec\("[^"]* [^"]*"\)' ${kodiTvPower}/share/kodi/addons/script.tv-power/default.py
    test -f ${kodiGlobalsearch}/share/kodi/addons/script.globalsearch/addon.xml
    test -f ${kodiAdulthideout}/share/kodi/addons/plugin.video.adulthideout/addon.xml
    test -f ${kodiAdulthideout}/share/kodi/addons/plugin.video.adulthideout/default.py
    test -f ${kodiPrivateMedia}/share/kodi/addons/script.private-media/addon.xml
    test -f ${kodiPrivateMedia}/share/kodi/addons/script.private-media/default.py
    test -f ${kodiPrivateMedia}/share/kodi/addons/script.private-media/delete.py
    grep -q 'kodi.context.item' ${kodiPrivateMedia}/share/kodi/addons/script.private-media/addon.xml
    command -v linux-voice-assistant
    test -f ${linuxVoiceAssistant}/bin/linux-voice-assistant
    test -f ${lvaHeyNabu}/hey_nabu.tflite
    test -f ${lvaHeyNabu}/hey_nabu.json
    test -f ${lvaWakeupChime}/wakeup.flac
    command -v lva-snapcast-duck
    command -v lva-kodi-companion
    command -v kodi-keyboard-layout
    command -v kodi-volume-keys
    test -f ${xkbPhoneticRoot}/symbols/bg_full
    grep -q 'include "bg_full(phonetic)"' ${xkbPhoneticRoot}/symbols/bg
    test -d ${xkbPhoneticRoot}/rules
    test -e ${xkbPhoneticRoot}/symbols/us
    home-assistant-dashboards --help >/dev/null
    voice-id --help >/dev/null
    assistant-router --help >/dev/null
    command -v frigate-person-mapper
    frigate-person-mapper
    touch $out
  ''
