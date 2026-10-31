# plugins/services/registry.nix
#
# The built-in server services, by name.
#
# lib/ reads this before NixOS evaluation begins, so that a host imports only
# the services its deploy entry names in hosts.<key>.services. That is why it
# is plain data rather than a NixOS module: nothing can be read out of a
# configuration that has not been built yet.
#
# A key names a module, which is not always a single lanbat.services.<name>:
# the postgresql module describes both postgresql and postgresql-always-on.
# voice-satellite is described by modules/core/voice-satellite.nix, which every
# host imports, so it is not selectable here.
{
  assistant-router = ../../services/assistant-router.nix;
  audiobookshelf = ../../services/audiobookshelf.nix;
  authentik = ../../services/authentik;
  bitmagnet = ../../services/bitmagnet.nix;
  caddy = ../../services/caddy.nix;
  dashcast = ../../services/dashcast.nix;
  forgejo = ../../services/forgejo.nix;
  frigate = ../../services/frigate.nix;
  grafana = ../../services/grafana.nix;
  home-assistant = ../../services/home-assistant.nix;
  homepage = ../../services/homepage.nix;
  immich = ../../services/immich.nix;
  influxdb = ../../services/influxdb.nix;
  invidious = ../../services/invidious.nix;
  jackett = ../../services/jackett.nix;
  jellyfin = ../../services/jellyfin.nix;
  llama-cpp = ../../services/llama-cpp.nix;
  mosquitto = ../../services/mosquitto.nix;
  llm-gateway = ../../services/llm-gateway.nix;
  music-assistant = ../../services/music-assistant.nix;
  nextcloud = ../../services/nextcloud.nix;
  person-mapper = ../../services/person-mapper.nix;
  postgresql = ../../services/postgresql.nix;
  qbittorrent = ../../services/qbittorrent.nix;
  redis = ../../services/redis.nix;
  redlib = ../../services/redlib.nix;
  romm = ../../services/romm.nix;
  samba = ../../services/samba.nix;
  searxng = ../../services/searxng.nix;
  snapcast = ../../services/snapcast.nix;
  syncthing = ../../services/syncthing.nix;
  tang = ../../services/tang.nix;
  techo5-bridge = ../../services/techo5-bridge.nix;
  telegraf = ../../services/telegraf.nix;
  vaultwarden = ../../services/vaultwarden.nix;
  voice-id = ../../services/voice-id.nix;
  wifi-presence = ../../services/wifi-presence.nix;
  wyoming = ../../services/wyoming.nix;
  yt-show = ../../services/yt-show.nix;
  zigbee2mqtt = ../../services/zigbee2mqtt.nix;
}
