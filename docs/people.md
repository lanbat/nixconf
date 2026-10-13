# People: who the assistant is talking to

The assistant can know who it is talking to, as a **hint** for what it says ("Good
morning, Alex!"), from these signals:

| Signal | Where | Status |
|---|---|---|
| **Face, cameras** | Frigate names faces on the cameras and the **person mapper** relays each room to the router | planned |
| **Face, robot** | A robot turns to the voice and captures the speaker's face; the router names it against Frigate ([stackchan.md](stackchan.md)) | live |
| **Phone** | A voice Pi sees a person's phone over Bluetooth (`pkgs/room-presence`) | live |
| **Home** | The server hears a person's phone on the home network (`services/wifi-presence.nix`) | live |
| **Voice** | `voice-id` matches the speaker's voice on the server (`services/voice-id.nix`) | live |

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

## Phones at home

`wifi-presence` on the router's host works out who is home from the household's phones
on the LAN. It needs nothing from the router or the access point: it listens on the
server's network interface for the phones' own broadcasts (ARP, DHCP, mDNS), and every
minute asks each phone's last address with ARP, which a sleeping phone still answers
(many ignore ping). Every five minutes it asks the whole subnet, to find a phone that got
a new address. A phone heard in the last 10 minutes is home.

Being home is weaker evidence than a phone in the room: it can tip a close voice call,
it never names anyone by itself, and a phone that is away rules nobody out. It doesn't
say anything about rooms ("nearby" is still Bluetooth, faces and the robot).

Add each phone's Wi-Fi address for the home network to its person, and run the service
on the router's host:

```nix
people.alex = { name = "Alex"; wifiMacs = [ "3a:1f:00:aa:bb:01" ]; };
# hosts.server.services: add "wifi-presence" when the host lists its services
```

Phones use a private address per network, which stays the same for that network, so
that is the one to list:

- **Android:** *Settings → Network & internet → Internet → (your network) → ⚙ →
  Privacy*: keep "Use randomized MAC" and read the address under *Network details*
  (or pick "Use device MAC").
- **iPhone:** *Settings → Wi-Fi → (i)* next to your network: *Private Wi-Fi Address* set
  to **Fixed** (iOS 18; "Rotating" changes it every two weeks), and copy the address
  shown under it.

A kernel filter on the raw socket passes only the listed phones' frames, cut to their
headers: nothing else on the network is read, and nothing is stored. Only person keys
go to the router. Keep it a hint: a MAC address is easy to copy.

## Voices

Every sentence said to any satellite passes through `voice-id` on its way to
speech-to-text. As soon as the audio ends it computes a speaker embedding (a CAM++ model,
on the server's CPU, in parallel with speech-to-text, so it adds no delay) and scores it
against every enrolled person's voiceprint. The router gets all the scores, not just the
best one, and weighs them with who is in the room (below). A sentence with less than about
a second of speech gives no scores: "lights off" says too little about who said it.

- **Remember me.** "Okay Nabu, remember me, I'm Alex" enrols Alex's voice from that
  conversation, and asks for a sentence or two more if it needs them ("Tell me a little
  about your day…"). Where a robot can see, it registers the face at the same time.
  "Stop" ends it.
- **It gets better with use.** When the router is sure who spoke (a voice match well
  ahead of everyone else, or the face the robot turned to), that sentence refines the
  person's voiceprint: at most 40 kept per person, and a sentence too unlike the rest is
  refused, so one mistake cannot snowball. Only enrolled people are ever learned from;
  other voices are kept in memory for ten minutes, for a "remember me", and never stored.
- **Forget me.** "Okay Nabu, forget me" removes the voiceprint and the faces of the
  person it recognises as speaking, and their name if they enrolled themselves; people
  listed in `deploy.nix` keep their name. It does nothing when it isn't sure who is
  asking.
- **Where it is kept.** Voiceprints are embeddings, never audio, in
  `/mnt/workload/voice-id` on the encrypted workload layer. Until the layer is unlocked
  after a reboot, voice-id recognises nobody and stores nothing. They are not backed up
  ([backup.md](backup.md)).

## How the router combines it

For a request from a room (`pkgs/assistant-router/assistant_router/people.py`):

Not by ruling people out, but by weighing them: every enrolled person is a candidate,
and an extra one stands for someone not enrolled.

- **Presence** raises a candidate's weight: their phone in the room (last 2 minutes),
  their face on a room camera (last 10 s), having been the speaker earlier in this
  conversation, and, less, their phone on the home network (last 3 minutes). Absence lowers it but never rules anyone out, so a phone left in another
  room doesn't overrule a clear voice.
- **Evidence** multiplies in: the voice scores of this sentence, and above all the face
  the robot saw after turning to the voice.
- **The speaker** is named only when one candidate clearly leads (80 % and three times
  the next); otherwise nobody is: unknown beats wrong. Presence is what makes a close
  voice call decidable: two people who sound alike, only one of them in the room.
- **Nearby** are the others present.

The weights are in one place (`people.py`), tested on these situations.

The cloud model gets one line after Home Assistant's context, for example: *"You are
probably talking to Alex (recognised by voice and face). Sam is nearby. Recognition is a
guess: use names to be friendly, never to decide or allow anything."* The local model
never sees it, and device actions are the same whoever asks.

## Never authority

Recognition is never used to unlock, disarm, open or allow anything, and never picks the
target of an action. A voice can be imitated, a face can be a photo, a phone can be
borrowed: it is good enough to greet someone, not to trust them.
