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
    command -v android-provision
    command -v media-feed-bridge
    command -v music-assistant-sources
    test -d ${auntieSounds}/${pkgs.python3.sitePackages}/sounds
    XIAOMI_CLOCK_DEVICES= xiaomi-clock-sync
    test -f ${pages}/offline.html
    test -f ${caPage}/index.html
    test -d ${dashboards}
    test -f ${kodiTvConfig}/sources.xml
    command -v linux-voice-assistant
    test -f ${linuxVoiceAssistant}/bin/linux-voice-assistant
    test -f ${lvaHeyNabu}/hey_nabu.tflite
    test -f ${lvaHeyNabu}/hey_nabu.json
    test -f ${lvaWakeupChime}/wakeup.flac
    command -v lva-snapcast-duck
    command -v lva-kodi-companion
    home-assistant-dashboards --help >/dev/null
    voice-id --help >/dev/null
    assistant-router --help >/dev/null
    command -v frigate-person-mapper
    frigate-person-mapper
    touch $out
  ''
