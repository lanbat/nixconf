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

Encrypt the key the installer printed into `techo5-kitchen-api-key.age`, and an SSH key of the
bridge's own (`ssh-keygen -t ed25519 -N ""`) into `techo5-ssh-key.age`.

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
- **Dashboards**: `lanbat-screen-<room>`, drawn by the device, a view per person.
- `script.lanbat_notify_person` sends a message to the body beside a person.

## Trusting Home Assistant's certificate

The device fetches dashboards, replies, pictures and calendars from `https://ha.<domain>`
itself, and checks the certificate. Caddy's comes from the profile's own CA, which TECHO5 has
no setting to trust, and its root filesystem is read-only. So the bridge (`ca.py`), as soon
as the device's SSH is on:

1. gives the device its SSH key and the admin's (the `ssh_keys` action) and turns SSH on;
2. logs in as root and keeps, on the writable `/data/lanbat`, the image's own bundle, the
   profile's root (`deployment.secrets.caCertificate`) and the two together;
3. binds that bundle over `/etc/ssl/certs/ca-certificates.crt` and restarts the daemon, which
   reads its roots once (init starts it again).

A bind is gone at the next boot, so this runs on every connection, and costs one login when
the device has it already. **Renewal:** Caddy renews its leaf certificates (12 hours) and
intermediates (7 days) under the root by itself, so nothing reaches the device for those.
A new root reaches it with the next deploy of the server: the bridge compares bundles and
binds the new one. A TECHO5 update brings a new image bundle, which the next boot picks up.

The cost: the device's SSH is on, root, keys only, the bridge's and the admin's. Set
`installCa = false` to leave the device alone (and `insecureTls = true` to have it skip
the checks, which doesn't cover drawn dashboards).

## Limits

- Its Wi-Fi radio can't sense phones: the device is only a Wi-Fi client and runs no code
  of ours.
- No moods: a Show has no ring and no face. The persona still applies.
- No Snapcast client: it joins multi-room audio as Music Assistant's Sendspin player.
- The CA rides on TECHO5's internals (the bundle's path, the daemon reading it once, init
  starting it again); a TECHO5 release that changes them needs `ca.py` to follow.
- The device signs in to Home Assistant as the voice satellites' user, who is not an
  administrator. TECHO5's music **star** (favourite) asks Home Assistant to render a
  template, which only administrators may, and Home Assistant counts each refusal as a
  failed login: five taps in a row and it bans the device's address (every request then
  gets 403, the dashboard says it can't reach Home Assistant). Don't use the star; to lift
  a ban, take the address out of `/var/lib/hass/ip_bans.yaml` and restart Home Assistant.
  Template-based cards on the device's dashboard stay empty for the same reason (no ban).
- After a TECHO5 update the device restarts: the bridge puts the CA back on its next
  connection, as after any boot ("ca": "installed" in its log, then "unchanged").
