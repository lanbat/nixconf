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
  - the room's person's own screen (see below): their dashboard view, glance chips,
    calendars and Immich photos; the household's for nobody, someone unknown or several
    people;
  - while the screen is on, a fetch every 60 seconds of that view's reminders, to-dos and
    calendar events from Home Assistant (the voice satellite user's token), sent to the
    assistant router as plain item payloads;
  - the sign-in code (the who page's link as a QR code) for someone it can't name, once
    per arrival;
  - the screen off after `screenOffAfter` minutes of an empty, idle room, on again when
    anyone comes back;
  - the household's phones in its Bluetooth adverts, as a room sensor.
- **Dashboards**: `lanbat-screen-<room>`, drawn by the device, a view per person.
- `script.lanbat_notify_person` sends a message to the body beside a person.
- **Jellyfin can play to it** (`techo5Devices.<room>.dlna`, on by default): the bridge turns
  on the device's DLNA, Video and DLNA video switches, and Jellyfin's firewall lets its
  address reach Jellyfin's discovery and event ports. See "Playing Jellyfin on the screen".

## What the person's screen shows

Each profile may name a Home Assistant to-do list for open tasks and one for reminders
(`profiles.<person>.todo` and `.reminders`; when left out they default to
`todo.<person key>_todo` and `todo.<person key>_reminders`). The household profile has
none. Those lists are ordinary Home Assistant entities: anyone with a login can open them
in the apps or edit them by voice. The Echo Show shows them only when the router places
**exactly one** known person in the room; a bystander, an unknown face or several people
get the household view instead, with no reminders or to-dos.

`home-assistant-post-setup` creates the default lists as `local_todo` entries when they
do not exist yet, for each profile's person and each person in `deployment.people`. You may
point a profile at an existing `todo.*` entity instead.

Each person adds to and reads their own lists by voice, and on the who page
([people.md](people.md#your-own-lists)); the screen reads them again within a few seconds of a
change (the router tells the bridge, `items_stale`) instead of waiting for the minute's refresh.

Each person may also choose their screen's **Next up** calendars, to-do list and photos on
the who page (**My settings**, [people.md](people.md#tap-your-phone)); those choices win over
the profile. The device's own glance chips and calendar page keep the profile's values
(`glance`, `calendars`): changing them makes TECHO5 drop every API connection, Home
Assistant's too, which a change at each arrival would do.

When one person is alone, their dashboard view (`lanbat-screen-<room>`, path
`<person key>`) is a stack of sections, top to bottom: a greeting, **Reminders** (hidden
when there are none), **Next up** (today and tomorrow, up to three events), **To-do**
(the first open items and a “more” count), then **Home** (glance chips, the room's lights
off button, weather and who's where). The household view keeps the greeting, **Next up**
and **Home** only. On narrow screens (under 600 px width) the **Home** section hides; the
greeting and reminders never do.

The bridge reads the lists and calendars with `todo.get_items` and
`calendar.get_events`, then sends items to the router. The router publishes MQTT sensors
on the screen's device, named like `sensor.lanbat_screen_<room slug>_reminder_1` …
`_reminder_5`, `_todo_1` … `_todo_5`, `_todo_more`, and `_event_1` … `_event_3`. Reminder
and to-do slots hold text only while that same person is alone in the room; they clear when
someone else arrives, the router restarts, or the bridge disconnects. Calendar events for
the household view publish whenever a screen body is connected, at any occupancy.

Tapping a reminder tile runs `script.lanbat_complete_reminder`, which marks the item done in
Home Assistant (`todo.update_item`). To-do tiles are display-only.

If Home Assistant is down or refuses a service call, the bridge sends no items: the screen
still shows the greeting and home status where it can, but reminder, to-do and event tiles
stay empty until Home Assistant answers again.

### Not yet verified on a live system

- That the voice satellite user's token may call `todo.get_items`, `todo.update_item` and
  `calendar.get_events` (if one is refused, grant that capability to the satellite user in
  `pkgs/home-assistant-post-setup/setup-ha.sh`).
- That `local_todo` lists are created as expected on a live Home Assistant (not only in
  the post-setup script tests).

## Playing Jellyfin on the screen

Jellyfin's DLNA plugin (`lanbat.services.jellyfin.settings.dlna.enable`, on by default;
`jellyfin-bootstrap` installs it) finds DLNA speakers and screens on the LAN, and the Echo
Show is one: in Jellyfin's web or phone app, choose **Play on** and pick the device by its
name. Music and video both work.

- **First cast from Jellyfin.** The device asks on its screen to allow a video from a new
  address (the server's); allow it once. Home Assistant's own `Video` switch also turns on.
- **Video.** The device plays H.264 up to 720p best. In Jellyfin, a client that asks for a
  lower quality makes the server transcode to that.
- **The address Jellyfin gives the device** is its public one, `https://media.<domain>`, so
  the device must resolve that name and trust the CA, which the bridge installs
  (`installCa`). A device with `installCa = false` can't play from Jellyfin.
- **Firewall.** Jellyfin accepts UDP (discovery) and its HTTP port (the device's progress
  reports) from the Echo Shows' addresses and from `settings.dlna.renderers`, and from no
  other address. The plugin has no switch for its media-server side, so what keeps it from the
  rest of the LAN is this firewall. `settings.dlna.server` (off) would open UDP 1900 and
  port 8096 to every address: TVs and apps could then browse the libraries without signing in
  (DLNA has no logins), and reach Jellyfin over plain HTTP without Caddy. Casting doesn't need it.
- **On the dashboard.** The device's own music page shows what is playing, with the controls,
  for a DLNA stream as for any other. `home-assistant-post-setup` adds Home Assistant's DLNA
  player for the device itself (from `http://<address>:8181/dlna/device.xml`, when the device
  is on and its DLNA is on), and puts it in the device's room on the next run after Home
  Assistant has loaded it (the nightly run, or `systemctl restart home-assistant-post-setup`),
  so the screen's Home section lists it with the room's other devices.
- `dlna = false` on a device leaves its three switches as they are; `settings.dlna.enable =
  false` turns the plugin's play-to off and opens no ports.

### Not yet verified on a live system

- That Jellyfin finds the device and plays to it with the stream address above. (The plugin's
  setting `EnablePlayTo` and its other options were read from the plugin package itself, DLNA
  10.0.0.0, not guessed.)

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

## What the logs say

When the screen doesn't do what you expect, `journalctl -u techo5-bridge` and
`journalctl -u assistant-router` say what each side decided:

- **Router:** `{"here": "Kitchen", "people": [["akadmin", 0.94]]}` is who it told the room's body is
  there, whenever that changes. `{"face_probe": "Kitchen", ...}` is each look at a face.
  `{"greet": "no", ..., "why": ...}` is why someone placed in a room wasn't greeted: they were
  already there at the last look (a greeting is for arriving, after `ARRIVAL_S` of absence), they
  were greeted lately (not twice in 30 minutes), or the body was asleep, in night mode or busy.
- **Bridge:** `present` (the camera's person and presence, and whether the screen was on),
  `face_result` (what the router named), `here` (who the router said is in the room), `view` (the
  dashboard chosen, and for whom: a person's own, or the household's) and `screen` (on or off, and
  why). The household's view is shown for nobody, someone it can't name, or two or more people.

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
