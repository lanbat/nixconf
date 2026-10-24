# Features

What a lanbat site can run. Services and plugins are opt-in per host. The
[README](../README.md) lists every built-in service and how to deploy; the
pages linked below are the design notes.

## Home

- **Home Assistant** at `ha.<domain>`, with OpenID Connect sign-in. Its
  dashboards rebuild themselves from the devices in each room: lights, climate,
  media, cameras and energy. [Dashboards](dashboards.md).
- **Frigate** for local camera detection, and **Zigbee2MQTT** with Mosquitto
  bridged into Home Assistant.
- **Music Assistant** as the library and player controller, with **Snapcast**
  keeping speakers in sync. A Raspberry Pi 3 can be a speaker and a voice
  satellite. [Pi 3 satellite](pi3-satellite.md).
- **Xiaomi clock sync** sets the time on Bluetooth thermometers that have no
  clock of their own.

## Voice

The server runs wake word, speech-to-text and text-to-speech. Each Pi can be a
satellite. Home Assistant's conversation agent can be a small local model, an
external API, or the assistant router.

- The **assistant router** filters noise, lets the local model handle clear
  device commands, and sends the rest to cloud models through an **LLM
  gateway** with fallbacks and a monthly budget.
- **Speaker identification** matches a voice to someone on the people list.
- **Who is nearby**, from a phone on the home Wi-Fi, a phone seen over
  Bluetooth in the room, or a face on the robot. [People](people.md).
- A **Stack-chan** on a satellite's USB port is the assistant's face: it turns
  toward people, shows the conversation, and sleeps when the room is empty.
  [Stack-chan](stackchan.md).

## Media and files

- **Jellyfin** for video, **Immich** for photos, **Audiobookshelf** for
  audiobooks, and **RomM** for retro games (it starts on the first request).
- **Media on the Echo Show** by voice: search YouTube and Music Assistant
  together, pick from the results (videos, playlists, radio, music, podcasts)
  and play them — video on the screen (remuxing a 720p one when YouTube offers
  only 360p), audio on the room's speaker.
  [Echo Shows](techo5.md#playing-video-music-radio-and-podcasts-on-the-screen).
- **qBittorrent**, **Jackett** for indexers, and **Bitmagnet** as an on-demand
  DHT index. Media files live on the Pi's encrypted drives.
- **Nextcloud**, **Syncthing** and **Samba** for files, **Vaultwarden** for
  passwords, and **SearXNG** for search on the LAN.
- **Kodi** and **EmulationStation** on the storage Pi's HDMI output.
- **Android TV boxes** provisioned from the server over ADB: pinned apps, the
  internal CA and device-owner policy. [Android devices](android-devices.md).

## Identity, DNS and access

- **Authentik** is the identity provider. Each service's description generates
  its provider and application, including forward-auth where the app has no
  login of its own. [Authentication](auth.md).
- An **internal CA**. Caddy serves `https://<service>.<domain>` on the LAN;
  each device trusts the CA once. [Security model](security.md).
- Optional **LAN DNS** answers those names, and short names such as
  `torrent.lan`, from the profile. It can run on more than one host, so DNS
  survives a reboot. [Architecture](architecture.md#lan-dns-lanbat-dns).
- **Homepage** builds its tiles from the same service descriptions.

## The two machines

- An **x86_64 server** runs the compute, databases, single sign-on and reverse
  proxy. A **Raspberry Pi** holds the encrypted bulk storage, exports it over
  NFS, and can be the TV and a voice satellite. [Architecture](architecture.md).
- **Two disk tiers.** Always-on services boot with no passphrase. Photos,
  passwords, files and media sit on a LUKS volume you unlock after boot; the
  Pi's drives unlock through Clevis once the server's Tang is up.
  [Secure layers](secure-layers.md).
- Services that need the Pi's drives stop when NFS drops and start again when
  it returns. [Failure modes](failure-modes.md).
- **Backups** of databases and service state onto the Pi. [Backup](backup.md).
- **Metrics**: Telegraf on each machine, InfluxDB and Grafana on the server.

## How it is built

- **One NixOS flake.** A profile is a site, a host takes a role, and a plugin
  (built-in, or another flake) adds services. [Extensibility](extensibility.md).
- **One file per service.** Subdomain, port, auth, tier, storage, account and
  secrets are declared once. Wiring generates the vhost, startup order,
  account, secrets and dashboard tile, and evaluation rejects clashes.
- Disks with [disko](https://github.com/nix-community/disko), secrets with
  [agenix](https://github.com/ryantm/agenix), deploys with
  [deploy-rs](https://github.com/serokell/deploy-rs).
- **Checked in CI.** Assertions and VM tests run on every pull request; a
  nightly job boots the full server.
