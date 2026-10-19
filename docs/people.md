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

A robot can name people without a room camera. It captures a frame and sends it to the
router, which runs it against the **same Frigate face library** and reports the result
back. It does so at two moments:

- **After turning toward the voice** ([stackchan.md](stackchan.md)): the face it then sees
  is most likely the speaker's, so it can name the speaker ("aimed", 10 s).
- **As someone comes into view, and every minute while they stay:** that face is someone
  in the room ("seen", 90 s), as a room camera's would be, without saying who is
  speaking. Over Wi-Fi the frames are a minute apart at most; they are 320×240 either way.

The robot never recognises anything itself — it only captures and transmits — and a frame
is sent only when the router says it has a face library to check against, so a robot on a
router without Frigate just stays quiet. Nothing is sent while it is asleep. The combining
rules below are the same as for the cameras. To enrol a face the robot can name, tap your
phone on it or say "remember me" ([Meeting Nabu](#meeting-nabu)), or add photos to your
entry in Frigate's face library.

## Phones in the room

The `room-presence` plugin (`modules/pi/room-presence.nix`) scans Bluetooth LE on any
host whose satellite has a room (a Pi, or the server), and every 15 s tells the router whose phones it heard in the
last minute, with their signal strength. The router counts a phone as *nearby* for two
minutes; a phone never names the speaker. A phone heard by sensors in several rooms (a
wall is no barrier to Bluetooth) counts only in the room where it is loudest, or in each
room within 6 dB of that, a doorway say. Bluetooth and Wi-Fi strengths are on different
scales, so each is compared only with its own kind. Enable it next to the voice plugin:

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
- **Any phone, no app: its IRK.** The Identity Resolving Key lets a sensor recognise
  the phone's rotating addresses. A phone paired once with any host hands it over. The
  router keeps the keys per person (`/var/lib/assistant-router/irks.json`, readable by it
  alone) and gives the full set to every room sensor when it connects and whenever it
  changes; the sensors keep them in memory only. So one pairing places the phone in
  every room, the host it paired with included: there BlueZ resolves the addresses
  itself and reports the phone's fixed identity address, which the sensor knows from
  the bond's key. *Forget my phone* and *Forget me* drop its keys everywhere. It is a secret:
  anyone with it can follow that phone in Bluetooth range. The `room-presence-irks`
  secret (`irks = true`, one `<person key> <key>` line per phone,
  [secrets/README.md](../secrets/README.md)) adds keys the router doesn't have;
  `sudo room-presence --paired` prints the keys of the phones paired with a host.

A **Stack-chan** scans too (`lanbat.stackchan.phones`, on by default): the bridge hands
it the same beacons, and the iPhone keys where room-presence has `irks` on that Pi, over
USB only, and the robot reports the phones near it every 15 s like a room sensor. That
places phones where the robot stands, also when it is away from its Pi on Wi-Fi. It
listens a slice of every second, so Wi-Fi keeps the radio most of the time.

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

## Tap your phone

With a Stack-chan and Authentik, there is nothing to list: hold your phone, unlocked, flat
on top of the robot's head. The robot is an NFC tag holding a link to
`https://who.<domain>`, and the phone opens it (NFC on; iPhones read tags too). The page
asks Authentik's login, once per phone, and then:

- you are the likely speaker in that room for two minutes, as strongly as a card;
- you are a person the assistant knows by your Authentik username (the profile's person
  when it has that key, else added, with your Authentik name). Your name follows
  Authentik's: rename yourself there, and the next tap or visit to the page renames you
  here too (in Home Assistant, the greeting and what the assistant calls you). A person
  the profile lists keeps the profile's name;
- the server looks up the phone's Wi-Fi address for the address it came from, and
  `wifi-presence` counts you home whenever that phone is on the network (above). That
  needs the phone on the home Wi-Fi, in the server's subnet, over IPv4.

The robot perks up and glows when a phone reads it. The link changes every minute and the
page only acts on this minute's or the last one's, so a photographed link does nothing
later. The page has **Forget my phone** and **Forget me** (your phones, face and voice; and
you, unless the profile lists you). Faces, voices and Bluetooth still come from their own
setup: a browser can't read a phone's Bluetooth keys.

The phone has to trust the site's certificate: install the home CA from
`https://ca.<domain>` first ([deployment checklist](deployment-checklist.md)). Turn it
off with `lanbat.services.assistant-router.settings.tap = false`.

## Meeting Nabu

Nobody needs a phone, an app or an account to be known: just talk to Nabu.

- **Nabu asks once.** After answering a voice it doesn't know, in a room with a robot, it
  adds *"By the way, I don't think we've met. What's your name?"*: a room at most once a
  day, and never when it already recognises someone there.
  - Answering is consent: *"I'm Jane"* (or just *"Jane"*) → *"Nice to meet you, Jane!"*.
  - *"No thanks"*, *"I'm just visiting"*, *"not now"* → *"No problem!"*, and nothing is
    kept.
  - Turn it off with `lanbat.services.assistant-router.settings.offerToMeet = false`.
- **Or ask:** *"Okay Nabu, remember me"*. Someone it knows gets a top-up (a new face
  picture, the voice refined, a card window); someone it doesn't is asked their name. *"My
  name is Jane"* from an unknown voice is an introduction too. Someone it knows only by a
  tap or a card has no voice yet: theirs is enrolled then, under the same person. So
  you'd tap your phone first, then ask.
- **One moment binds everything it can:**
  - **the voice,** from that conversation's sentences. If voice-id wants one more, Nabu
    asks *"Tell me a little about your day?"*;
  - **the face,** from the robot's camera, if someone is in front of it;
  - **a card:** *"If you have a card, hold it on my head now."* For 30 s the robot reads
    cards instead of being a tag for phones, and the card tapped becomes theirs. From then
    on it names them on any robot, for two minutes, unless their voice clearly says
    otherwise.
- **Phones and Authentik are optional.** A tap by someone Nabu just recognised (they
  spoke in that room in the last two minutes) links their Authentik login to the person
  they already are, instead of making a second one. From then on their name follows
  Authentik's, and the phone's Wi-Fi address comes along.
- **Renaming and forgetting:**
  - *"call me Kiki"* renames someone met by voice; a linked person's name follows their
    account;
  - *"forget my card"* drops the cards;
  - *"forget me"* removes everything: voice, faces, cards, phones and the link.

Bank cards and phones show a new random UID (four bytes starting `08`) on every tap, so
they can't be used as cards. A card's UID is easy to copy: it personalises, it never allows
anything. People met this way are kept in `/var/lib/assistant-router/people.json`, their
login links in `people-aliases.json`, cards in `cards.json`.

## Voices

Every sentence said to any satellite passes through `voice-id` on its way to
speech-to-text. As soon as the audio ends it computes a speaker embedding (a CAM++ model,
on the server's CPU, in parallel with speech-to-text, so it adds no delay) and scores it
against every enrolled person's voiceprint. The router gets all the scores, not just the
best one, and weighs them with who is in the room (below). A sentence with less than about
a second of speech gives no scores: "lights off" says too little about who said it.

- **Remember me.** Meeting Nabu (above) enrols a voice from the conversation where
  someone gives their name, and asks for a sentence or two more if it needs them ("Tell
  me a little about your day…"). Where a robot can see, it registers the face at the same
  time. "Stop" ends it.
- **It gets better with use.** When the router is sure who spoke (a voice match well
  ahead of everyone else, the face the robot turned to, or their tap or card), that
  sentence refines the person's voiceprint: at most 40 kept per person, and a sentence too
  unlike the rest is refused, so one mistake cannot snowball. Other voices are kept in
  memory for ten minutes, for a "remember me", and never stored.
- **A tap teaches.** For two minutes after you tap your phone or card on a robot, what you
  say to that room's satellite is yours: it refines your voiceprint, or, if you have none
  yet, starts one once there are three sentences. Meanwhile, if the robot sees someone
  in front of it, it adds up to three pictures, 10 s apart, to your faces
  ([Frigate's library](#faces-on-the-cameras)). Someone else talking or standing in front
  of it in those two minutes would be taken for you, so tap when it's you at the robot.
  The page your phone opens says so, and "Forget me" removes both.
- **Forget me.** "Okay Nabu, forget me" removes the voiceprint and the faces of the
  person it recognises as speaking, and their name if they enrolled themselves; people
  listed in `deploy.nix` keep their name. It does nothing when it isn't sure who is
  asking.
- **A second ear.** A Stack-chan on a satellite's USB also records each sentence, from
  the wake word to the transcript, with its own microphone, which is often nearer the
  speaker than the satellite's. Not over Wi-Fi: a sentence's audio is more than the
  robot's Wi-Fi link keeps up with, so there the satellite's microphone alone is the voice. The bridge hands that recording to the router, the
  router to voice-id (its loopback `/embed`), and voice-id scores it like the
  satellite's audio and drops it. Of the two reports for a sentence the router keeps
  the one that matches someone better, waiting up to 0.8 s for both. The robot's
  recordings are never used to enrol or refine a voiceprint, so voiceprints come from
  one kind of microphone. The robot's microphone is off except during a sentence
  (15 s at most); the recording exists only in memory, on the robot, the Pi and the
  server, until it is scored.
- **Where it is kept.** Voiceprints are embeddings, never audio, in
  `/mnt/workload/voice-id` on the encrypted workload layer. Until the layer is unlocked
  after a reboot, voice-id recognises nobody and stores nothing. They are not backed up
  ([backup.md](backup.md)).

## How the router combines it

For a request from a room (`pkgs/assistant-router/assistant_router/people.py`):

Not by ruling people out, but by weighing them: every enrolled person is a candidate,
and an extra one stands for someone not enrolled.

- **A tap** of their card or phone on the room's robot (last 2 minutes) names them unless
  the voice clearly disagrees.
- **Presence** raises a candidate's weight: their phone in the room (last 2 minutes),
  their face on a room camera (last 10 s), their voice clearly heard in the room (a match
  of 0.7 or more, well ahead of anyone else's, in the last 5 minutes), having been the
  speaker earlier in this conversation, and, less, their phone on the home network (last
  3 minutes). Absence lowers it but never rules anyone out, so a phone left in another
  room doesn't overrule a clear voice.
- **Evidence** multiplies in: the voice scores of this sentence, and above all the face
  the robot saw after turning to the voice.
- **The speaker** is named only when one candidate clearly leads (80 % and three times
  the next); otherwise nobody is: unknown beats wrong. Presence is what makes a close
  voice call decidable: two people who sound alike, only one of them in the room.
- **Nearby** are the others in the room (below).

**Who is in a room** puts the same evidence together per person, rather than per
sentence. Each source is a chance that they are there: a tap or card 98 %, a face on a
camera, the robot's look or their voice its own confidence, their phone placed in the room
80 %, their phone on the home network 20 % (for the whole house). The chances combine as
independent ones (two sources that each say 80 % make 96 %). Someone counts as in the
room from 50 %, and only with something more than the home network. The room a person is
most likely in is where they are; with nothing but the home network they are "home", and
with nothing at all "away".

The weights are in one place (`people.py`), tested on these situations.

The cloud model gets one line after Home Assistant's context, for example: *"You are
probably talking to Alex (recognised by voice and face). Sam is nearby. Recognition is a
guess: use names to be friendly, never to decide or allow anything."* The local model
never sees it, and device actions are the same whoever asks.

## In Home Assistant

The router publishes its guesses as sensors, named "Probably …": who spoke last to each
robot, who else is nearby it, who is in its room (**Probably here**, with each person's
confidence and sources as attributes), and who is home
([stackchan.md](stackchan.md#in-home-assistant)). The People device has a **Where is
<name>** sensor per person: the room they are most likely in, "home" or "away", with the
confidence and sources. It is added, renamed (when you rename yourself in Authentik) and
removed with the person. When a robot greets someone arriving
([stackchan.md](stackchan.md#4-nabu-with-a-body)), its **Recognized** event says who and by
what, for automations. They are as uncertain as the line the cloud model gets, and for
the same uses.

Behind them, each robot has a diagnostic sensor per source, so you can see what led to a
guess: **Seen by the cameras**, **Seen by the robot** (its own camera, after turning to
the voice), **Heard by voice** (the last sentence's best match and its score, for five
minutes), **Phones here (Bluetooth)**, **Phones here (Wi-Fi)** (once the satellites
listen on Wi-Fi) and **Tapped (phone or card)**. Each names who that source sees in the
room now, with its confidences as attributes. They hold the same people and names as the
"Probably" sensors, nothing more.

## Never authority

Recognition is never used to unlock, disarm, open or allow anything, and never picks the
target of an action. A voice can be imitated, a face can be a photo, a phone can be
borrowed: it is good enough to greet someone, not to trust them.
