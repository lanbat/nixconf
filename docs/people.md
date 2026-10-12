# People: who the assistant is talking to

The assistant can know who it is talking to, as a **hint** for what it says ("Good
morning, Alex!"), from up to three signals:

| Signal | Where | Status |
|---|---|---|
| **Face, cameras** | Frigate names faces on the cameras and the **person mapper** relays each room to the router | planned |
| **Face, robot** | A robot turns to the voice and captures the speaker's face; the router names it against Frigate ([stackchan.md](stackchan.md)) | live |
| **Phone** | A voice Pi sees a person's phone over Bluetooth (`pkgs/room-presence`) | live |
| **Voice** | `voice-id` matches the speaker's voice on the server (`services/voice-id.nix`) | planned |

This page describes the part they share: the people list and how the assistant router
combines what it hears into one line for the cloud model.

## The people list

```nix
lanbat.deployment.people = {
  alex.name = "Alex";
  sam.name = "Sam";
};
```

The keys (`alex`, `sam`) are the only identities that travel between the satellites,
the robot and the router: a robot reports `{"person": "alex", "confidence": 0.9}`, never a
name or free text, and anything not on the list is dropped. Names stay in `deploy.nix`;
voiceprints and face templates never leave where they are made (the server's encrypted
workload layer, the robot's own flash).

The same keys are what Frigate's face library is enrolled against: a face-library entry
is a person's key, so a recognised face comes back as `{"person": "alex", ...}` like any
other signal, and a display name never has to match a label.

## Faces on the cameras

Rather than recognise on the robot, the cameras feed **Frigate**, which runs a local
face-recognition model (no cloud) against its per-person face library and announces each
match on MQTT (`frigate/#`). The **person mapper** (`services/person-mapper.nix`) reads
those matches, keeps who is in which room (its `settings.cameras` maps room → Frigate
camera) and tells the router over the same body socket the robots use. Enrolling a person
means adding photos to their entry in Frigate's face library; the person mapper only
*names* the faces Frigate reports, it never stores a face itself.

## Faces on the robot

A robot can name the speaker without a room camera. When it turns its head toward the
voice ([stackchan.md](stackchan.md)), it captures a frame and sends it to the router; the
router runs it against the **same Frigate face library** and reports the result back. The
robot never recognises anything itself — it only captures and transmits — and a frame is
sent only when the router says it has a face library to check against, so a robot on a
router without Frigate just stays quiet.

The recognised key lands in the router exactly like a camera face does (a `face` signal
for the room), so the combining rules below are the same. Enrolling a person the robot can
name is the same as enrolling for the cameras: add photos to their entry in Frigate's face
library.

## Phones in the room

The `room-presence` plugin (`modules/pi/room-presence.nix`) scans Bluetooth LE on a
satellite Pi that has a room, and every 15 s tells the router whose phones it heard in the
last minute, with their signal strength. The router counts a phone as *nearby* for two
minutes; a phone never names the speaker. Enable it next to the voice plugin:

```nix
plugins = [ inputs.self.lanbatPlugins.voice inputs.self.lanbatPlugins.room-presence ];
modules = [ { lanbat.roomPresence.irks = true; } ];   # only if iPhones are in the list
```

Phones change their Bluetooth address every few minutes, so each needs one of:

- **Android: the Home Assistant app's beacon.** In the Companion app, *Settings → Companion
  app → Manage sensors → BLE transmitter*: turn it on, keep it in iBeacon mode, and copy
  its UUID into the person's entry:
  ```nix
  people.alex = { name = "Alex"; bleBeacon = "a1b2c3d4-e5f6-0718-293a-4b5c6d7e8f90"; };
  ```
  It is not secret: a beacon only says "this is Alex's phone" to whoever listens.
- **iPhone (or any phone without the beacon): its IRK.** The Identity Resolving Key lets
  the Pi recognise the phone's rotating addresses. Pair the phone with the Pi once
  (`bluetoothctl`, then `scan on`, `pair <address>`, accept on the phone), then
  `sudo room-presence --paired` prints each paired phone's key. Put the keys in the
  `room-presence-irks` secret, one `<person key> <key>` line per phone
  ([secrets/README.md](../secrets/README.md)). It is a secret: anyone with it can follow
  that phone.

The Pi 3's own Bluetooth chip works but is flaky (it shares a UART and sometimes starts
without a controller; `hosts/pi3/hardware.nix` retries). The scanner restarts on its own;
if `journalctl -u room-presence` keeps showing `scan stopped`, a USB Bluetooth adapter
fixes it. Phones and their signal strengths go to the router and nowhere else, and only
as person keys.

## How the router combines it

For a request from a room (`pkgs/assistant-router/assistant_router/people.py`):

- **The speaker** is named when the voice of the request matches someone, or a face is
  recognised in the room, or both agree. When they disagree, or two faces are in view
  with no voice match, nobody is named: unknown beats wrong.
- **Nearby** are the other faces in the room (last 10 s) and phones in the room (last
  2 minutes).

The cloud model gets one line after Home Assistant's context, for example: *"You are
probably talking to Alex (recognised by voice and face). Sam is nearby. Recognition is a
guess: use names to be friendly, never to decide or allow anything."* The local model
never sees it, and device actions are the same whoever asks.

## Never authority

Recognition is never used to unlock, disarm, open or allow anything, and never picks the
target of an action. A voice can be imitated, a face can be a photo, a phone can be
borrowed: it is good enough to greet someone, not to trust them.
