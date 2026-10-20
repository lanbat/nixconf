# Echo Shows on TECHO5

An Amazon Echo Show running [TECHO5](https://github.com/HuskerMinion/techo5) (Alpine plus
one daemon, `echod`, an ESPHome API device) becomes a room's body for the assistant
router, as a Stack-chan is. Installing TECHO5 is done with its own installer; lanbat
takes over from a device on the network.

```nix
deployment.techo5Devices.kitchen = {
  address = "192.0.2.60";   # a fixed lease
  room = "Kitchen";         # the Home Assistant area; one body per room
  playerName = "Echo Show 8";
  profiles.alex = { glance = [ "sensor.washer_status" ]; immichPerson = "Alex"; };
  household.calendars = [ "calendar.family" ];
};
```

Encrypt the key the installer printed into `techo5-kitchen-api-key.age`, and a random one
(`openssl rand -base64 24`) into `techo5-dashcast-key.age` for the streamed dashboards.

## What it does

- **Home Assistant** adds the device with its key and puts it, and its Music Assistant
  (Sendspin) player, in its room. Voice goes through the preferred "Voice" pipeline, so
  voice-id names the speaker.
- **The bridge** (`services/techo5-bridge.nix`) connects to the device as a second ESPHome
  client and to the router as a `screen` body (`can`: caption, greet, capture, phones, here):
  - a frame when a person comes into view, for Frigate to name ("seen"), and one for
    "remember me";
  - a greeting as a notification;
  - the room's person's own screen: their dashboard view, glance chips, calendars and
    Immich photos; the household's for nobody, someone unknown or several people;
  - the sign-in code (the who page's link as a QR code) for someone it can't name, once
    per arrival;
  - the screen off after `screenOffAfter` minutes of an empty, idle room, on again when
    anyone comes back;
  - the household's phones in its Bluetooth adverts, as a room sensor.
- **Dashboards**: `lanbat-screen-<room>`, a view per person, streamed by default: TECHO5's
  dashcast (`pkgs/techo5-dashcast`, unit `techo5-dashcast`) opens them in a headless Chrome on the
  server, signed in to Home Assistant on the loopback as the voice satellites' non-admin user, and
  sends the device pictures over a Noise link keyed with `techo5-dashcast-key` (port 9555, the
  devices only). Every card type works. `dashboard = "drawn"` has the device draw them itself,
  which needs a certificate it trusts: TECHO5 checks Home Assistant's websocket even with "Skip
  certificate checks", so not with lanbat's internal CA.
- `script.lanbat_notify_person` sends a message to the body beside a person.

## Limits

- Its Wi-Fi radio can't sense phones: the device is only a Wi-Fi client and runs no code
  of ours.
- No moods: a Show has no ring and no face. The persona still applies.
- No Snapcast client: it joins multi-room audio as Music Assistant's Sendspin player.
- `insecureTls` (default on) turns on the device's "Skip certificate checks", since it
  can't be given the internal CA. Encrypted, but a LAN attacker who impersonates the
  server could get the voice satellites' token it holds.
