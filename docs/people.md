# People: who the assistant is talking to

The assistant can know who it is talking to, as a **hint** for what it says ("Good
morning, Alex!"), from these signals:

| Signal | Where | Status |
|---|---|---|
| **Face, cameras** | Frigate names faces on the cameras and the **person mapper** relays each room (or a place outside) to the router | live, for the cameras in `settings.cameras` |
| **Face, robot** | A robot turns to the voice and captures the speaker's face; the router names it against Frigate ([stackchan.md](stackchan.md)) | live |
| **Phone** | A voice Pi sees a person's phone over Bluetooth (`pkgs/room-presence`) | live |
| **Phone, Wi-Fi** | A host serving Wi-Fi measures the phones among its access point's clients (`pkgs/room-presence`, [wifi.md](wifi.md)) | live |
| **Home** | The server hears a person's phone on the home network (`services/wifi-presence.nix`) | live |
| **Face, screen** | An Echo Show on TECHO5 sends a frame when someone comes into view; the router names it against Frigate ([techo5.md](techo5.md)) | live |
| **Phone, screen** | An Echo Show's Bluetooth hears a person's phone; its bridge recognises it as a room sensor does | live |
| **Voice** | `voice-id` matches the speaker's voice on the server (`services/voice-id.nix`) | live |

This page describes the part they share: the people list and how the assistant router
combines what it hears into one line for the cloud model.

## How Nabu learns who you are

One sequence, with a phone: **tap, and say hi.**

1. Hold your phone to a robot's head. The page has your name: your account's, or the one
   you type there, with no account ([Tap your phone](#tap-your-phone)).
2. For the next two minutes, just talk to Nabu. It learns your phone's Wi-Fi address at
   once, your face from up to three frames of whoever is in front of it, and your voice
   from what you say. Nobody needs to say "remember me" (and doing so then only tops up,
   without asking your name).

The name is always typed, never heard: speech-to-text spells names its own way. Learning
happens only in a short window something explicit opened (a tap, a card, "remember me"),
in that robot's room, and each source teaches only when it is unambiguous: a face only
when exactly one is in view, a voice not when voice-id says it is clearly someone else
already known, Wi-Fi only for the phone that opened the page.

Without a phone (a child, a guest), "remember me" still works by voice: the name as Nabu
heard it, shown on the robot's screen, waits on the [admins' list](#people-nabu-knows) to
be spelled right.

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

A "room" can be a place with no robot, such as a camera outside:

```nix
lanbat.services.person-mapper.settings.cameras = { "Driveway" = "c1"; };
```

"Where is" then says Driveway while the face is there. The library is the same one the
robots' frames are checked against, so a person is one entry inside and out: their frames
from a tap or "remember me" help the camera, and faces assigned to them in Frigate's
**Train** tab (from the camera's own detections) help the robots. Any name in the library
counts, not only the profile's people: the router knows the people it added (accounts,
guests, people met by voice) and ignores names it doesn't.

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

An Echo Show on TECHO5 does the same "seen" probes with its own camera when its person
detector sees someone, every 20 s until the router names them, and its frame is what
"remember me" enrols. Frames leave it only for those two, and go no further than the
router and Frigate ([techo5.md](techo5.md)).

## The satellite's camera

The PlayStation Eye that is a voice satellite's microphone array also has a camera, and the
`lanbat-satellite-camera` plugin (`modules/pi/satellite-camera.nix`, `pkgs/satellite-camera`)
uses it as a face source for the router. Enable it on a host with a satellite room and the
router, like room-presence:

```nix
plugins = [ inputs.self.lanbatPlugins.voice inputs.self.lanbatPlugins.satellite-camera ];
```

- **Only when something moves.** Every few seconds (`sampleSeconds`, 3) it opens the camera
  for one small grey frame and closes it again; if the picture changed by more than `motion`
  it sends a 320x240 JPEG to the router, at most every `gapSeconds` (20), and every
  `againSeconds` (60) once the router named someone or kept finding nobody it knows. Nothing
  streams and nothing is kept on the Pi. There is no face detector on the Pi (a Pi 3 has no
  room for one): a frame with no face is answered "no face found" by the face library and
  costs nothing else.
- **Always "seen".** The camera is fixed, not aimed at whoever is talking, so what it sees
  is who is in the room (the same evidence as a room's camera), never who is speaking.
- **What it is used for.** Naming who is in the room, sightings (source *satellite*, with
  the picture, on the admin page), Unknown people (a stranger it sees gets a name of their
  own, above), and enrolment: *"remember me"*, and the three frames after a tap or a card,
  ask the camera for a face when the room has no robot or screen to ask.
- **Not a body.** It is its own kind of router client, so a Stack-chan or an Echo Show in
  the same room stays; a robot that can capture is asked first. A room with only a camera
  has no Home Assistant device: its sightings are on the admin page.
- **Where it runs.** The camera is the first video node of USB `1415:2000` (`usbId`, for another
  camera), readable by the `video` group; the unit starts when it is plugged in and stops
  when it is unplugged. Check that the host's kernel has the driver (`gspca_ov534`: `modinfo
  gspca_ov534`), and on a Pi 3 that its single USB port budget (the Ethernet, the microphone
  and now the camera share it) still holds; an under-voltage Pi will show it.
- **Privacy.** A frame of whoever walks past is sent to the router and the face library, so
  it is the same decision as any room camera. Frames go to the router only.

## Phones in the room

The `room-presence` plugin (`modules/pi/room-presence.nix`) scans Bluetooth LE on any
host whose satellite has a room (a Pi, or the server), and every 3 s tells the router whose phones it heard in the
last 20 seconds, with their signal strength. The router counts a phone as *nearby* for 25
seconds; a phone never names the speaker. A phone heard by sensors in several rooms (a
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

A **Stack-chan** scans too (`lanbat.stackchan.phones`, on by default), where its Pi
doesn't: in a room whose Pi runs room-presence the Pi scans, and the robot is told to
forget the phones. Its internal RAM can't hold the Bluetooth controller beside Wi-Fi, TLS,
the camera and face detection (13 KB left, and it panicked). Elsewhere the bridge hands it
the beacons and the router's phone keys, over USB or, with firmware 1.9.7 and later, over
its Wi-Fi link (pinned to its Pi's certificate, with the token). The robot keeps them in
its flash until they change or are forgotten, and reports the phones near it every 15 s
like a room sensor. That places phones where the robot stands, also away from its Pi on
Wi-Fi. It listens a slice of every second, so Wi-Fi keeps the radio most of the time.

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

A host that extends the home Wi-Fi (`lanbat.wifi.accessPoint`, [wifi.md](wifi.md)) does
say which room: its room sensor reads the access point's clients every 15 s and reports
the phones among them, by the same addresses (the profile's `wifiMacs` and those learned
from taps), with the signal it measures. The router compares Wi-Fi signals only with
other Wi-Fi sensors, as Bluetooth with Bluetooth, and the strongest room wins.

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

## Devices nobody has claimed

Off by default: `lanbat.services.assistant-router.settings.evidence.enable = true`. Phones are
known by the addresses in the profile or learned from a tap; any other device on the home
network (a visitor's phone, a new tablet, the TV) is on no one's list. With this on, the
Wi-Fi sensor also tells the router the addresses of those devices (it hears the ARP every
device sends anyway, and a sweep brings it out of all of them), and the router works out whose
they probably are:

- **From what arrives together.** A device *arrives* when it turns up after ten minutes away.
  A person arrives when a face, a voice, a tap, a card or a Bluetooth phone identifies them
  after the same absence (an Unknown, from the camera or a robot, arrives too). A device that
  arrives within two minutes of the same person at least four times, and for 80% of its
  arrivals, is probably theirs, provided nobody else is with it more than 30% of the time.
  A device that arrives with different people (a family tablet, a visitor's phone) is never
  suggested.
- **You confirm.** *People Nabu knows → Devices* lists the devices with when they were first
  and last seen and, where there is one, who they arrive with. *It is Alex's* teaches the
  device like a tapped phone (so being home counts for them); *Ignore* hides it. Nothing is
  claimed automatically. A device is claimable once it has been on the network since the router
  last started (the router keeps no address it could read back).
- **Merge hints.** An Unknown and someone else who arrive with the same device are suggested as
  the same person on *Find duplicates*: probably one person met twice.
- **What is kept.** A device nobody claimed is a keyed hash of its address (the key is
  `evidence.key` in the router's state; a stolen `evidence.db` holds no address), arrival counts,
  and who arrived with it. It is dropped after `evidence.days` (30) without being heard. A claimed
  device's address stays, as a tapped phone's does. The admin page shows a short code, never an
  address.
- **What it does not do.** Nothing probes a device: no port scans, no extra traffic beyond the
  sweep that was already there. A phone that uses a different private address on each
  connection looks like a new device each time, and nothing tries to link them: the addresses
  of phones that keep one per network (most Android and iOS defaults) are what this learns. A
  GrapheneOS phone set to per-connection randomisation is learned by tapping it, pairing it
  (Bluetooth key) or using the Home Assistant app's beacon, as before. Bluetooth addresses
  that never change (headphones, watches) are the next step.

It records, however pseudonymously, which devices visit your network, visitors' included:
decide that for your household before turning it on, and it may need approval where your
organisation's policy requires it.

## Tap your phone

With a Stack-chan and Authentik, there is nothing to list: hold your phone, unlocked, flat
on top of the robot's head. The robot is an NFC tag holding a link to
`https://who.<domain>`, and the phone opens it (NFC on; iPhones read tags too). The first
time, the page asks who you are, either way:

- **Sign in with your account** (Authentik's login): you are a person the assistant knows
  by your Authentik username (the profile's person when it has that key, else added, with
  your Authentik name). Your name follows Authentik's: rename yourself there, and the page
  picks it up within a day (it checks an account with Authentik once a day, unseen while
  you are still signed in there), renaming you in Home Assistant, the greeting and what
  the assistant calls you. A person the profile lists keeps the profile's name.
- **Or just type your name**, without an account: a guest. The link being fresh proves
  you are at the robot. Your name is yours to change on the page (**Change my name**). If
  you sign in later on the same phone, your account becomes that person, with everything
  Nabu learned about you.

The page remembers the phone in its own cookie, signed by the router, for a year; it never
believes a name a browser sends it. Then, at every tap:

- you are the likely speaker in that room for two minutes, as strongly as a card;
- the server looks up the phone's Wi-Fi address for the address it came from, and
  `wifi-presence` counts you home whenever that phone is on the network (above). That
  needs the phone on the home Wi-Fi, in the server's subnet, over IPv4.

The robot perks up and glows when a phone reads it. The link changes every minute and the
page only acts on this minute's or the last one's, so a photographed link does nothing
later. The page has **Forget my phone** and **Forget me** (your phones, face and voice; and
you, unless the profile lists you). Faces, voices and Bluetooth still come from their own
setup: a browser can't read a phone's Bluetooth keys.

With Home Assistant on the server, accounts (not guests, who could otherwise pick someone
else's lists to show) also get **My settings**. Each chooses, from what Home Assistant has:

- the calendars of their screen's **Next up**;
- the **to-do list** their screen shows (their own `todo.<key>_todo` unless they pick
  another, such as the household's shopping list);
- whose **photos** the screen's slideshow shows, as Immich names them;
- the calendar their **reminders** go in too.

The choices are kept in the router's state (`prefs.json`) and go over what the profile says
for them (`techo5Devices.<room>.profiles.<person>`). An Echo Show uses them the next time it
sees that person alone in front of it ([techo5.md](techo5.md#what-the-persons-screen-shows)).
The device's own glance chips and calendar page stay as the profile has them: TECHO5 drops
every connection to it, Home Assistant's included, whenever those change, so they don't
change per person. "Forget me" removes the settings too. The router reads Home Assistant
with the voice satellites' token, an ordinary user's (`settings.personal`, on by default).

### Reminders

"Okay Nabu, remind me to call mum at 5pm" adds a reminder for the person Nabu is sure is
speaking: an item with a due time in their `todo.<key>_reminders` list. It hears "at 5pm",
"at 17:30", "in 10 minutes", "in an hour", "tomorrow at 9", "tonight at 8", "on Monday at
3pm", and the time first ("remind me at 5pm to call mum"). With no time it asks "When should I
remind you?" once; a time it can't read is asked for, never guessed. Without am or pm, today's
time is the next time that hour comes round, and tomorrow's or a named day's is 7 to 11 as
morning, 12 as noon and 1 to 6 as afternoon. Times are the server's time zone. Accounts also
have **My reminders** on the who page: add one with a date and time, mark one done, or remove it.

The reminder shows on that person's screen, and **chimes there when it comes due, if they are
alone in front of it** (docs/techo5.md). Nothing is ever spoken, and a reminder is never shown
to a bystander: it waits until its person is alone in front of a screen again.

A **Stack-chan** has no chime, so the router does the equivalent: when exactly one person, surely
recognised, is in a room with a robot that is awake and not in a conversation, a reminder of theirs
that has come due is shown as the robot's caption, with a happy mood, a perk and the amber
notification light. The caption stays 15 seconds, so it is shown again two minutes later, three
times in all, while they stay alone; marking it done (on the Echo, on the who page, or in Home
Assistant) stops it. What was shown is kept (`rung.json`), so a restart doesn't begin again.

Reminders go to the person's reminders list: `todo.<key>_reminders`, or the one their screen
profile names (`techo5Devices.<room>.profiles.<person>.reminders`), by voice and on the who page.

### Your own lists

Everyone Nabu knows has a to-do list of their own in Home Assistant (`todo.<key>_todo`, a
`local_todo` list; the profile's people and everyone in `deployment.people` get theirs when
Home Assistant is set up, anyone else's is made the first time they use it), and may have more.
By voice, to whoever Nabu is sure is speaking:

- "add milk to my list", "put batteries on my hardware store list";
- "take milk off my list";
- "what's on my list";
- "make a list called hardware".

Nabu only ever touches the speaker's own lists, never one named by what was said, and says
"I'm not sure who you are" rather than guess. Accounts also have **My lists** on the who page:
the items (add, done, remove) and **A new list**. A new list is written to the router's
`lists.json`; `home-assistant-post-setup` runs when that file changes and makes it, so it
appears in about a minute, after Home Assistant's brief restart. Nothing deletes a list, here
or in Home Assistant: an administrator does, in Home Assistant. An Echo Show reads the lists
again at once when its person changes one. Lists are ordinary Home Assistant entities, so anyone
with a login can open anyone's; the screen shows only the person in front of it. "Forget me"
drops the requests (`lists.json`), not the lists. Home Assistant answers "add milk to my
shopping list" itself when a list has that name (`prefer_local_intents`); these commands
cover "my list" and a list Nabu made for you.

The phone has to trust the site's certificate: install the home CA from
`https://ca.<domain>` first ([deployment checklist](deployment-checklist.md)). Turn it
off with `lanbat.services.assistant-router.settings.tap = false`.

## Meeting Nabu

Nobody needs a phone, an app or an account to be known: just talk to Nabu.

- **Nabu asks once.** After answering a voice it doesn't know, in a room with a robot, it
  adds *"By the way, I don't think we've met. What's your name?"*: a room at most once a
  day, and never when it already recognises someone there.
  - Answering is consent: *"I'm Jane"* (or just *"Jane"*) → *"Nice to meet you, Jane!"*,
    and that's all it says: the rest is learned quietly (below).
  - *"No thanks"*, *"I'm just visiting"*, *"not now"* → *"No problem!"*, and nothing is
    kept.
  - A word or two that is neither (*"pardon?"*) → *"Sorry, what's your name?"*, once. A
    request instead of a name is just answered.
  - Turn it off with `lanbat.services.assistant-router.settings.offerToMeet = false`.
- **Or ask:** *"Okay Nabu, remember me"* (or *"remember me as Jane"*). Someone it knows
  gets a top-up (a new face picture, the voice refined); someone it doesn't is asked their
  name. *"My name is Jane"* from an unknown voice is an introduction too. Someone it knows
  only by a tap or a card has no voice yet: theirs is enrolled then, under the same person.
  So you'd tap your phone first, then ask. All of it works without the cloud model.
- **One moment binds what it can, quietly:**
  - **the voice,** from that conversation's sentences, and from the next ones if voice-id
    wants more: they are ordinary requests, answered as usual, nobody is asked to keep
    talking;
  - **the face,** from the robot's camera, if someone is in front of it.
- **A card, when you ask:** *"remember my card"* (or *"this is my card"*) → *"hold your card
  on top of my head"*. For 30 s the robot reads cards instead of being a tag for phones,
  and the card tapped becomes yours. From then on it names you on any robot, for two
  minutes, unless your voice clearly says otherwise.
- **Phones and Authentik are optional.** A tap by someone Nabu just recognised (they
  spoke in that room in the last two minutes) links their Authentik login to the person
  they already are, instead of making a second one. From then on their name follows
  Authentik's, and the phone's Wi-Fi address comes along.
- **Renaming and forgetting:**
  - *"call me Kiki"* renames someone met by voice; a linked person's name follows their
    account;
  - *"forget my card"* drops the cards;
  - *"forget me"* removes everything: voice, faces, cards, phones and the link (without
    the cloud model too).

Bank cards and phones show a new random UID (four bytes starting `08`) on every tap, so
they can't be used as cards. A card's UID is easy to copy: it personalises, it never allows
anything. People met this way are kept in `/var/lib/assistant-router/people.json`, their
login links in `people-aliases.json`, how each was met (an account, a guest, or heard) in
`people-kinds.json`, cards in `cards.json`.

## People Nabu knows

The home's admins (Authentik's `authentik Admins` group, or the groups in
`lanbat.services.assistant-router.settings.tapAdmins`) have a page with everyone Nabu
knows: `https://who.<domain>/admin`, linked from the who page once signed in. Each person
is labelled with how Nabu knows their name, and with what it has learned (voice, face
pictures, Wi-Fi, Bluetooth, card):

- **Account:** signed in on the who page, or in the home's profile. The name comes from
  there, so it isn't changed here.
- **Guest:** a name typed on the who page, with no account (or fixed by an admin).
- **Heard:** met by voice, the name as Nabu heard it.
- **Unknown:** a face that matched no one, with a name Nabu made up (below).

For guests, names heard and Unknowns, an admin can:

- **fix the name** (a heard name is then confirmed, a guest);
- **forget** them: voice, faces, cards and phones;
- **merge them into someone else**, when they turn out to be the same person: their voice,
  face pictures, phones, cards, sightings and settings move to that person, and the entry
  goes. The other can be an account, a guest, a heard name or another Unknown, but an
  Unknown never absorbs someone who has been identified. It can't be split apart again,
  so the page asks first.

Anyone with face pictures also has a **Face pictures** page: each picture the library
holds for them, with a delete button (it asks first). Deleting one stops Nabu using it to
recognise them; deleting the last means it can't recognise them at all. Frigate's own
`train` folder of unclassified attempts is never a person, and `train` can't be a name.

**Find duplicates** (shown while there are Unknowns) asks the face library who each
Unknown's later pictures look like, up to 12 Unknowns and 3 pictures each, and lists the
pairs it is at least 0.6 sure of, with the picture and a *Merge them…* button. It runs
only when you press the button (every picture is a call to Frigate), and the answer is kept
ten minutes. The picture an Unknown was first enrolled with isn't tried: it would only say it
is itself. It is a suggestion: look at the faces before merging.

On **Sightings**, each row can be moved to someone else (*"that wasn't them"*), or to *Nobody
known*; *add this picture to them* also teaches the library that face under the new person.

Nobody else sees the list: the page is behind the login, and checks the groups the
provider passes on.

## Sightings

Every face a camera sees becomes a **sighting**: who (or nobody known, or an Unknown, below), where, when, by
what and how sure. That covers a robot's or an Echo Show's frame with a face in it, and a
face a Frigate camera names as it comes into view (through the person mapper). The admins
search them from **People Nabu knows → Sightings**, by person, place and the last days,
with a picture:

- **indoors:** the frame that was checked, kept `pictureDays` (7) on the encrypted
  workload layer. While the layer is locked, the sighting is kept without its picture;
- **outside:** Frigate's own thumbnail of the event, as long as Frigate keeps it.

The sightings themselves (no pictures) are kept `days` (30) in the router's state. Each
also appears in Home Assistant's logbook, as the robot's or screen's **Sighting** event.
"Forget me" and an admin's "Forget" delete a person's sightings and pictures, and "Link
to…" moves them. Settings: `lanbat.services.assistant-router.settings.sightings = { days =
30; pictureDays = 7; }`.

## Unknown people

Off by default. With `lanbat.services.assistant-router.settings.unknownPeople.enable = true`,
a face the library matches to no one is no longer "nobody known": each distinct one gets a
name of their own, **Unknown Wobbly Noodle** (always "Unknown", then two random, mild words),
and is a person like any other: on the who page, in sightings and in Home Assistant.

- **How they are told apart.** The router enrols the face into Frigate's face library under a
  generated key (`unknown_wobbly_noodle`), so the next time that face turns up (a robot's or
  screen's frame, or a Frigate camera) the library names them. Pictures seen in the same room
  in the following minutes are added to that person (up to 8) rather than starting another.
  A camera's face is enrolled from the thumbnail of its Frigate event, once per event; this
  needs the person mapper (`person-mapper.settings.cameras`), which passes them on.
- **They are strangers, not household.** An Unknown is never greeted by name, never counted as
  present, never in the assistant's idea of who is in the room, and a robot is told nobody it
  knows. They show on the admin page as **Unknown**, where an admin can give them a real
  name, link them to an account, or forget them.
- **Until they identify themselves.** Saying who they are in the room where they were seen
  (*"I'm Jane"*, *"remember me as Jane"*) or tapping a phone signed in to an account there makes
  that Unknown *them*: the name and kind change (Heard, Account), and the key stays, so their
  face pictures, sightings and Home Assistant entities stay where they are.
- **Nothing is deleted.** An Unknown is kept with its sightings until someone says "forget me"
  or an admin forgets them. Past `unknownPeople.maxPeople` (500) no new ones are made; none are
  dropped. They are kept on the host root with the other face pictures (Frigate's library),
  not on the encrypted layer.
- **The words** are the router's own list. A site replaces them with `unknownPeople.adjectives`
  and `unknownPeople.nouns` (letters only, 2 to 14 each).

This keeps the face of anyone a camera or robot sees, including visitors who never agreed to
it, the opposite of Meeting Nabu's *"a decline keeps nothing"*. Decide that for your household
before turning it on; it may also need approval where your organisation's policy requires it.

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
- **Presence** raises a candidate's weight: their phone in the room (last 25 seconds),
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

**Last face check** (on each robot, and on a screen's presence device) is what the face
library said to the body's last frame: the person's name, "nobody known" (a face, matched
to no one), "not anyone known" (a library name that is nobody here), "no face found", or
"face library unavailable", with the confidence, why the frame was sent ("seen" or after a
"turn") and when, as attributes. The router also logs every check as one JSON line
(`face_probe`). Frigate's own pages don't list these: it shows only what its cameras
detect, and a frame checked through its API is answered and not kept.

Each Echo Show on TECHO5 also has item sensors on its screen device
(`sensor.lanbat_screen_<room>_reminder_*`, `_todo_*`, `_todo_more`, `_event_*`): plain
text the dashboard tiles read, filled by the assistant router from what the bridge fetched
from Home Assistant. They mirror each person's `todo.*` lists and calendars configured on
the profile (`docs/techo5.md`); the lists themselves stay ordinary Home Assistant to-do
entities any household member can open. The screen shows reminders and to-dos only while
exactly that one person is placed in the room.

## Acting on where people are

The `follow-me` plugin (`lanbatPlugins.follow-me`, `modules/server/follow-me.nix`) turns
the "Where is <name>" sensors into actions, through Home Assistant automations it
generates (`lib/follow-me.nix`). It is off until `lanbat.homeAssistant.followMe.enable`
is set, and starts as a **dry run**: every decision is written to the logbook as
"Follow-me would ...", and nothing is switched. Turn `dryRun` off once the log looks right.

```nix
modules = [ {
  lanbat.homeAssistant.followMe = {
    enable = true;
    # lightsOffAny = true;
    rooms."Office" = { lights = [ "light.office_lamp" ]; illuminance = "sensor.office_lux"; };
    rooms."Kitchen" = { lights = [ "light.kitchen_strip" ]; music = false; };
  };
} ];
```

A room must be one the router can place people in (a key of `voiceRooms`, or a TECHO5
screen's room); the build rejects any other. A room's `lights` are the only lights follow-me ever switches;
leave out a robot's or a screen's own. People are `lanbat.deployment.people` unless `people`
lists some; someone not listed (a guest) neither moves music nor keeps a room occupied.

| What | When (defaults) |
|---|---|
| **Lights on** | someone arrives in a room (stays 10 s), it is dark (the room's light sensor under 30 lx, or the sun below -1 degree) and it is evening (17:00 to 22:30: 100 %) or night (22:30 to 06:30: 20 %, warm). Never by day, and not if a light is already on. |
| **Lights off** | the room has been empty 5 minutes, and follow-me turned its lights on. A light you switched on, or off, by hand is left alone unless **Hand-switched lights off** is on. |
| **Hand-switched lights off** | optional (`lightsOffAny`, default off): the room has been empty `lightsOffAnyMinutes` (10), and any of the room's listed `lights` is on, including ones you switched on by hand. Only those listed lights; a screen's or robot's own light is never touched unless you list it. Respects dry run, pause and guests like the other empty-room rules. |
| **Music follows** | a person's last room changes and holds 8 s, they were the only one in the room they left, music is playing there, and the new room has a Music Assistant player that is free. The queue moves (`music_assistant.transfer_queue`) and the old room stops. Once per 45 s per person at most. |
| **Music pauses** | a room with music on has been empty 15 minutes. Paused, not stopped, so "resume" works. TVs are never touched. |

The first time a person is placed in a room after a fresh install, the music does not follow:
follow-me has no earlier room to take it from. From then on it is remembered, across restarts.

"Empty" is judged from the sensor with care, because one person's sensor goes room, `home`,
room within a minute or two as their phone drops out of Bluetooth. A person the house still
hears but cannot place keeps their last room occupied for 20 minutes
(`unlocatedMinutes`). So the lights of a room someone left silently go off about
`unlocatedMinutes` plus `lightsOffMinutes` later, and in 5 minutes if they were seen in
another room. `unknown` and `unavailable` presence (a router or Home Assistant restart) is never
"empty": the rules do nothing.

In Home Assistant, **System → Follow-me** has two switches: **Pause follow-me** (off means
running; it stops every action, though the last-room bookkeeping goes on) and **Guests here** (stops the "empty room" rules, since guests
carry no tracked phone), each room's occupied sensor, and what the rules did.

Like everything here it is a hint: presence is never authority, and a wrong guess only ever
costs a light left on or a song that moved.

## Never authority

Recognition is never used to unlock, disarm, open or allow anything, and never picks the
target of an action. A voice can be imitated, a face can be a photo, a phone can be
borrowed: it is good enough to greet someone, not to trust them.
