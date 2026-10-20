# Stack-chan: a face for the voice satellite

M5Stack's [StackChan](https://docs.m5stack.com/en/StackChan) kit is a small robot: a
CoreS3 (ESP32-S3) head with a touch screen, a camera and a head-touch sensor, two
feedback servos that turn and tilt it, and twelve RGB LEDs in its body. Plugged into a
voice satellite's USB port, it becomes the satellite's face. The Pi keeps the voice
(wake word, microphone, speaker), and the robot shows what the assistant is doing:

- **It notices people.** Its camera finds faces on the robot itself. It turns to whoever
  is in front of it and follows them. When someone appears after nobody was around, it
  glances over, nods and glows warm. With nobody there it looks around now and then, and
  after five minutes it dozes off until someone comes.
- **A conversation shows on it.** On the wake word it looks at you with cyan LEDs. While
  Home Assistant works it looks up, thinking, with violet LEDs going round. The reply
  shows as a caption, its mouth moves with the spoken reply, and its face matches the
  reply: happy, sad ("Sorry, I couldn't…"; not "I can't wait!"), or curious with a head
  tilt for a question.
  An error gets a head shake and a red flash.
- **Touch.** Tap its head to talk, with no wake word; tap again to stop. A tap on its
  screen doesn't count (it's easy to brush). Stroking it from front to back just makes it
  happy. Any touch silences a ringing timer.
- **Never left hanging.** A wake word and then nothing: the "Listening…" goes and it tilts
  its head, a little "hm?".
- **Timers.** The soonest timer counts down under its face, and the LEDs drain as it
  runs. When it rings, the robot wiggles and flashes amber.
- **A nap when nobody is around.** After 5 minutes without a face it dozes; after 15
  (`lanbat.stackchan.napAfter`) it naps: screen dark, eyes closed, LEDs off, head down,
  the camera slowed to a frame a second. Someone walking in, a tap or the wake word wakes
  it with a stretch, and Nabu knows it was napping.
- **A hand near its face.** The proximity sensor beside the camera notices a hand (or a
  fingertip 1-2 cm away): it wakes from a nap or a doze, or perks up. **A wave** across
  it hushes it: a ringing timer stops, or the reply being spoken does.
- **The room's light.** By day the screen dims in a dim room and brightens in a bright
  one (`lanbat.stackchan.autoBrightness`), relative to the brightest light it has seen
  in the last half day: behind its dark front glass the sensor reads only a few lux in a
  lit room, so it compares rather than measures.
- **Battery.** The robot reads its battery every 30 s. Below 15 % and not charging it
  stops moving its head (a servo's current surge on a weak supply makes its power chip
  cut it off, and then only the power button brings it back), pulses amber and asks to
  be charged; Nabu knows too. It moves again once it charges or is back above 25 %.
- **Night.** From 23:00 to 07:00 it sleeps, dimmed, still and with its camera off. The
  wake word or a tap wakes it for the conversation. Told "go to sleep" (or "good night")
  by day, it sleeps until the next wake word or tap on its head: no "wake up" needed.
- **Status.** One dim red LED on each side means the microphone is muted. A slow red
  pulse means Home Assistant is unreachable. "No Pi" means the bridge has stopped
  talking to it, over USB and over Wi-Fi.

Frames are analysed on the robot and dropped; only a face's position is used, to move
the head. With the assistant router, three things leave the robot, each to the server
and nowhere else:

- **A photo to remember someone**, when they ask: it goes to Frigate's recognition
  library ([people.md](people.md)).
- **A frame to recognise the speaker**, after the robot has turned to a voice (with
  Frigate's face library): Frigate matches it against the library, and the router
  keeps only the name it gets back.
- **The sentence as the robot heard it**, from the wake word to the end of the
  sentence: voice-id scores it as a second opinion on who spoke, and drops it
  ([people.md](people.md#voices)). The robot's microphone is off at all other times.

A recognised face or voice only personalises the assistant and is never used to unlock,
disarm, open or allow anything. The bridge on the Pi reaches LVA on the loopback and the
robot over USB, or, with [Wi-Fi](#unplugged-wi-fi) on, over TLS on the LAN when the robot
isn't plugged in.

## Requirements

- The **StackChan kit** (CoreS3 with the servo body).
- A **voice satellite on the Linux Voice Assistant backend**
  (`lanbat.voiceSatellite.backend = "lva"`; see
  [pi3-satellite.md](pi3-satellite.md#6-voice-backends-wyoming-vs-linux-voice-assistant)).
  Evaluation fails, naming this setting, on the Wyoming backend.
- A **USB-C data cable** from the robot to the Pi. It powers the robot too. A Pi 3's
  1.2 A USB budget can run the servos, but on a weak supply the Pi reports undervoltage
  (`dmesg | grep -i voltage`). In that case use a powered hub, or let the robot's battery
  carry the peaks.

## 1. Flash the firmware

The robot runs `firmware/stackchan` (Arduino and PlatformIO, on top of M5Stack's
[StackChan-BSP](https://github.com/m5stack/StackChan-BSP)) in place of its stock
firmware. Build and flash it from any Linux machine with Nix, or from the Pi itself.

1. **Back up the stock firmware** first, so you can go back:
   ```bash
   nix shell nixpkgs#esptool -c esptool.py --chip esp32s3 -p /dev/ttyACM0 \
     read_flash 0 0x1000000 stackchan-stock.bin
   ```
2. **Build and upload:**
   ```bash
   cd firmware/stackchan
   nix shell nixpkgs#uv -c uvx --from platformio pio run -t upload --upload-port /dev/ttyACM0
   ```
   The firmware is built with pioarduino (Arduino 3.3 on ESP-IDF 5.5), which needs
   PlatformIO 6.2 or newer; `uvx` runs the current one. The first build downloads the
   toolchain and ESP-IDF to `~/.platformio` (a few GB) and compiles ESP-IDF itself with
   the face detector (`espressif/human_face_detect`), about 20 minutes; later builds take
   a few. If the upload can't connect, hold the button on the CoreS3's side for about
   three seconds until its LED turns green (download mode), then upload again.
3. **Press the power button** afterwards. The robot stays off after the reset at the end
   of an upload.

To flash from the Pi, build on another machine, copy
`.pio/build/stackchan/firmware.factory.bin` (bootloader, partition table and firmware in
one image) over, stop the bridge and write it at 0:

```bash
sudo systemctl stop lva-stackchan
esptool --chip esp32s3 -p /dev/stackchan -b 921600 write-flash 0x0 firmware.factory.bin
sudo systemctl start lva-stackchan
```

**Camera debug view.** With the bridge stopped, send `{"debug": "camera"}` to the robot
(one JSON line on `/dev/stackchan`): its screen shows what the camera sees, with a box on
each face it detects (green once it counts as a face), and every 2 s it reports frames,
detections and the best score. `{"debug": "off"}` ends it, and so do two minutes.

To restore the stock firmware:
`esptool.py --chip esp32s3 -p /dev/ttyACM0 write_flash 0 stackchan-stock.bin`.

## 2. Enable the plugin

Add the plugin next to the voice plugin in the satellite's deploy entry:

```nix
pi-voice = {
  role = "voice-pi";
  platform = "raspberry-pi-3";
  # ...
  plugins = [
    inputs.self.lanbatPlugins.voice
    inputs.self.lanbatPlugins.stackchan
  ];
  modules = [
    {
      lanbat.voiceSatellite.backend = "lva";
      # Everything below is optional; these are the defaults.
      lanbat.stackchan = {
        # usbSerial = "...";        # pin one robot when another ESP32-S3 is plugged in
        captions = true;
        touchToTalk = true;
        noticeGuests = true;
        nightHours = { start = "23:00"; end = "07:00"; };   # null keeps it awake
        brightness = 180;
        nightBrightness = 10;
        # sadWords = [ "sorry" "leider" ];  # replies in another language
      };
    }
  ];
};
```

Deploy, and plug the robot in. udev links it as `/dev/stackchan`, and the
`lva-stackchan` unit (`pkgs/lva-stackchan`) connects to it and to LVA's peripheral API.
The robot can be plugged in and out at any time.

## 3. Check it

- `journalctl -u lva-stackchan -f` shows `robot connected on /dev/stackchan`,
  `robot firmware 1.0.0, protocol 1` and `following ws://127.0.0.1:6055`.
- `voice-satellite-diagnostics` lists the unit with the rest of the satellite.
- Say the wake word, then "set a timer for one minute": cyan, then violet, then the
  caption with a moving mouth. The countdown appears under the face, and a tap on its
  head silences the ring.
- Walk out of view for a minute and come back: it should turn to you and nod.

## Unplugged: Wi-Fi

The robot can live away from the Pi, on its own charger, and still be its face: when it
isn't plugged into the Pi it joins the Wi-Fi and dials the bridge. It also keeps the
robot's charger off the Pi's USB, which matters on a Pi 3 with an analog speaker: a
robot on the Pi and a separate charger at once share a ground, and the speaker hisses.

```nix
lanbat.stackchan.wifi = {
  enable = true;
  # address = "192.168.1.12";          # where the robot dials; the host's IP by default
  # allowFrom = [ "192.168.20.0/24" ]; # the Wi-Fi's subnet, when it isn't the LAN's
};
```

1. Add the `stackchan-wifi` secret, encrypted to the Pi:
   ```
   ssid=Home
   password=the Wi-Fi password
   ```
2. Deploy, and plug the robot into the Pi once. The bridge hands it the network, its own
   address and port (8771), its certificate's fingerprint and a token, and the robot
   keeps them in flash. The journal says `giving the robot the Wi-Fi details`.
3. Unplug it (keep its charger). After 10 s without the Pi on USB it joins the network
   and dials; `journalctl -u lva-stackchan` says `robot connected over Wi-Fi from …`,
   and conversations go on as before.

USB always wins: plugged back in, the robot talks over USB at once and switches Wi-Fi
off 10 s later. A robot running on Wi-Fi does less than one on USB in two ways. Its ear
works on USB only. And the head turns toward the voice only on USB, since a robot on
Wi-Fi is taken to be away from the Pi's microphones. A robot that stays beside the
satellite and is unplugged only for its power (a charger of its own, so the Pi's USB
doesn't have to feed it) keeps turning, and calibrating, with
`lanbat.stackchan.wifi.besideMicrophones = true`.

How it is kept safe:

- The Wi-Fi details only ever travel over USB. A robot refuses them over Wi-Fi, and the
  bridge never sends them there.
- The robot checks the bridge's certificate by its SHA-256 fingerprint, so a machine
  pretending to be the Pi gets nothing. The bridge makes the certificate and the token
  on first start (in `/var/lib/private/lva-stackchan`); deleting them makes new ones,
  and the next time the robot is plugged in it gets those.
- The bridge drops a connection whose first line isn't a hello with the token, and its
  port is only open to `allowFrom` (the LAN by default).
- Turning `wifi.enable` off makes the bridge tell the robot to forget the details the
  next time it is plugged in.

## In Home Assistant

With the assistant router (and Mosquitto beside it), every robot is a device in Home
Assistant, found by MQTT discovery; nothing to set up there. Turn it off with
`lanbat.services.assistant-router.settings.homeAssistant = false`. Add the
`mosquitto-assistant-router-pass` secret ([secrets/README.md](../secrets/README.md)).

- **Sensors:** battery, charging, battery low, link (USB or Wi-Fi), mood, someone in
  front of it, a hand near it, light at its sensor (relative, see above), napping,
  asleep for the night, and the **conversation** (idle, listening, thinking or speaking).
- **Diagnostics:** the last voice's direction and where the head points (degrees, +
  to its left), its Wi-Fi signal while on Wi-Fi, free memory and PSRAM, its NFC's mode
  (tag for phones, or reading cards), the bridge's last problem (one short line), its
  Pi's under-voltage (the Pi's own alarm: a supply that sags under load), and why it last
  started (**Last restart**: `panic`, `brownout` or a `watchdog` mean something went
  wrong; `power-on` and `software` don't). The robot sends its part once a minute
  (`health`). A crash's report on USB (`Guru Meditation`, the backtrace) goes to the
  bridge's journal, a few lines a minute at most.
- **Tags:** an NFC tag tapped on the robot fires Home Assistant's own tag scanner
  (*Settings → Tags*; "when a tag is scanned" automations), and a person's card names
  them ([people.md](people.md#meeting-nabu)). It reads from the top of its head; it
  perks up and glows when it has read one, also from a nap. A phone held there opens
  the router's "who tapped" page instead ([people.md](people.md#tap-your-phone)): the
  robot is that page's tag, and reads cards only when the router hasn't given it a
  link, or for 30 s after someone says "remember me"
  ([people.md](people.md#meeting-nabu)).
- **Touch** and **wave** are event entities (`tap` or `stroke` with the zone; `wave`)
  for automations: a stroke on its head could dim the lights, a wave skip a song. So are
  the **wake word**, each **gesture** it plays, a **face** arriving or leaving, a **phone
  tap**, a **card** (waiting after "remember my card", learned, forgotten, with the
  person), a **recognized** person (greeted, with who and by what) and a **servo fault**.
  What was said and replied never goes to Home Assistant.
- **Controls:** gesture buttons (nod, shake, tilt, wiggle, dance, look around), go to
  sleep and wake up, nap now, mirror the voice direction (a switch, for a camera that
  faces the other way), set its mood, where to look (track, you, up, ahead) and a
  head turn (−70…70°), start listening and stop the reply, read a card (30 s, for the
  tag scanner while it is a tag for phones), **Show camera view** (two minutes of what its
  camera sees on its screen, each face it finds boxed: yellow a candidate, green the face
  it follows; the counts every 2 s in the bridge's journal), its screen brightness, a caption on its
  screen, and its LEDs as a light: an RGB notification lamp (a reminder, the washing
  machine) that shows while nothing else is on its LEDs.
- **Who:** "Probably speaking" (who spoke to it last, with how sure and how it knew, for
  five minutes), "Probably nearby" and "Probably here" (everyone in its room, with how
  sure and by what) per robot; "Probably home" and a "Where is <name>" per person on a
  People device ([people.md](people.md#in-home-assistant)). They are guesses, named as
  such: use them to personalise, never to unlock, disarm or allow anything
  ([people.md](people.md#never-authority)).

The router publishes all of it: it is connected to every robot's bridge already, so
only the server has a broker login. Home Assistant's commands reach a robot over the
link it uses, checked against fixed lists; MQTT is never a way to hand a robot Wi-Fi
details or phone keys.

## 4. Nabu with a body

When the profile runs the assistant router (`services/assistant-router.nix`) and the
satellite has a room (`lanbat.deployment.voiceRooms`), the robot connects to the router,
and the assistant knows it has a body:

- **It is someone.** A request from the robot's room goes to the cloud model with a
  persona, `lanbat.services.assistant-router.settings.body.persona`. By default that's
  Nabu, matching the "Okay Nabu" wake word: upbeat and excited about the day, warm and
  gentle when someone is down, like a best friend. Ask it "how old are you?".
- **It picks its own face.** The model starts each reply with a tag, `[mood]` or
  `[mood gesture]`. The router takes the tag off before the reply is spoken and sends it
  to the robot. These moods and gestures replace the guess from the reply's words.
- **It knows who is there.** The robot tells the router whether someone is in front of
  its camera, and since when, so "can you see me?" gets a true answer.
- **Body commands, answered at once** without a model: "nod", "shake (your head)", "look
  at me", "look around", "dance", "go to sleep" (or "good night", "take a nap") and "wake
  up", said as people say them ("nod for me please", "goodnight Nabu"). "Go to sleep" by
  day lasts until the next wake word or tap, or until the night hours start or end.
- **Short answers work.** "Yes", "no" or "sure" after it asked something are answers, not
  noise.
- **It remembers people.** "Remember me" (or "remember me as <name>"): if the robot can see
  you it takes a photo and registers your face with Frigate, and it learns your voice
  from what you say next, so the assistant knows your name in this room. The photo only
  goes to Frigate's recognition library and is never used to unlock, disarm, open or allow
  anything. [people.md](people.md#meeting-nabu).
- **It knows who is talking, and who is here.** When the router has a face library
  (Frigate), the robot captures the speaker's face right after it turns to them, and the
  router names it, so the reply can be addressed. It also shows the router a face as it
  comes into view, and again every minute while someone stays (a minute apart at most over
  Wi-Fi), so the router knows who is in the room without anyone speaking. See
  [people.md](people.md#faces-on-the-robot) for how that becomes a name.
- **It greets you.** When the router is sure you have come into its room (80 % or more
  by any one source: a tap or a card, its own look, a clear voice, your phone placed
  there; [people.md](people.md#how-the-router-combines-it)), the robot wakes from a nap
  or a doze, looks happy, wags and nods with three green breaths of its LEDs, and shows
  "Hi Kiril!" (your first name) for 8 s. Once per arrival: not while you stay, not again
  in that room within half an hour, and not if you have just been talking to it. Not at
  night, and a greeting due in the middle of a conversation waits for its end. Turn it
  off with `lanbat.services.assistant-router.settings.greet = false`.

Only fixed fields pass between the robot and the router (yes/no, seconds, names from
fixed lists), so nothing the robot sends becomes text in a prompt. The router listens
for robots on port 8770, which is opened only to the hosts running `lva-stackchan`.
`lanbat.stackchan.router = false` turns the connection off.

Check it: `journalctl -u lva-stackchan` on the Pi shows `body of Kitchen at
ws://…:8770/v1/body`, and `journalctl -u assistant-router` on the server shows
`{"body": "connected", "room": "Kitchen", …}`.

## Turning toward the voice

Say the wake word and the robot turns its head to where the voice is coming from, so the
camera meets the speaker. It hears you with the satellite's microphone array — the
PlayStation Eye carries four mics — and works out the direction from the few microseconds
between them (TDOA, pure Python on the Pi, no extra hardware). This is what lets it catch
your face for recognition above.

It is on by default and needs only the four-mic source, so the robot still works without
it (the head just stays put). `lanbat.stackchan.soundDirection = false` turns it off.

| Option | Default | What it does |
|---|---|---|
| `lanbat.stackchan.soundDirection` | `true` | Turn the head toward the voice |
| `lanbat.stackchan.soundOffsetDeg` | `"0"` | A fixed nudge added to every angle, in degrees |
| `lanbat.stackchan.soundTurnMinDeg` | `"15"` | The voice must move this far before it turns again |
| `lanbat.stackchan.soundMicGeometry` | the Eye's four mics | Where the four mics sit, four "x,y" points in metres |
| `lanbat.stackchan.soundSource` | the PS Eye capture node | Which PipeWire node to listen on for the four channels |

### Calibrating the direction

The estimate is good to a few degrees once three things are right: the mic **geometry**,
the mic **channel order**, and a per-room **offset**.

- **Geometry.** `soundMicGeometry` is four "x,y" points in metres, y forward and x to the
  robot's right, one per mic in channel order. The default is the PlayStation Eye's: its
  four mics sit in a line across its front, 2 cm apart. A line tells left from right but
  not front from back, so a voice behind the robot reads as its mirror in front, and the
  head turns that way, which is where it should look anyway. With a different card whose
  mics form a square, set its points here and it gets front and back too; two mics closer
  than 5 mm apart make an axis that can't be read.
- **It calibrates itself.** After each wake word it turns to the voice and then looks at
  where the person actually is: the robot reports the face's direction once its head is
  still (`face_at`), and the bridge pairs that with the raw estimate. With six or more
  such turns, spread over at least 30°, a straight line through them becomes the turn:
  its slope fixes turns that are too short and a mirrored array (a negative slope, so
  the "Mirror voice direction" switch isn't needed), its offset where the mics sit
  relative to the robot. Only turns where exactly one face is in view count, and wild
  ones are dropped. Before it has a line, if nobody is where it turned, it looks once the
  other way, which is how a mirrored array is found. If nobody is there either (in the
  dark it sees nobody anywhere), it turns back to the voice and doesn't look away again
  until its camera has seen a face. It learns only from turns where it sees you, so in a
  dark room it keeps its current turn. The samples are kept on the Pi and
  keep refining it. In Home Assistant: *Turn calibration* (learning 3/6, calibrated,
  calibrated and mirrored, with the gain and offset), *Auto-calibrate turning* (on), and
  *Reset turn calibration* after moving the robot or the camera. Until it's calibrated
  (or with the switch off), the settings below apply.
- **Which way round.** The Eye's channel 0 is at one end of the line, and which end that
  is as the robot sees it depends on how the camera faces. Say the wake word from the
  robot's left: the journal should show a positive angle (`voice at 50 degrees`) and the
  head should turn toward you. If it's negative and the head turns away, turn on
  **Mirror voice direction** on the robot's Home Assistant device (kept on the Pi across
  restarts), or reverse the order in the profile:
  `soundMicGeometry = "0.03,0;0.01,0;-0.01,0;-0.03,0";`.
- **Channel order (square arrays).** The four channels must line up with the four geometry points. The
  `doa-order` tool (on the satellite's PATH, from this module) finds it: it records all
  four channels, you clap three times — straight ahead, then to the robot's right, then to
  its left — and it prints the channel-to-corner mapping and a `soundMicGeometry` string
  ready to paste. `doa-order --geometry "$(current)"` also tells you if it disagrees with
  what you have set. A wrong order makes the head turn the wrong way or mirror image.
- **Offset.** Rooms reflect, and the mics sit off the robot's centre. If it consistently
  turns a little short or far, add `soundOffsetDeg` (for example `"-15"` for "always 15°
  too far right"). `soundTurnMinDeg` is the hysteresis: the estimate wobbles a few degrees
  between windows, and the head only moves once the voice has moved on by that much, so a
  little higher (say `"20"`) stops it chasing its own echo.

Check it: `journalctl -u lva-stackchan -f` shows `voice at 25 degrees (confidence 0.83):
turning` as you move around the robot.

## How it fits together

```
 LVA ──ws://127.0.0.1:6055──▶ lva-stackchan (Pi) ──USB serial, JSON lines──▶ robot firmware
  ▲         events: wake, stt_text,     │  decides mood, captions, timers,      │ face, LEDs, head,
  │         thinking, tts_*, timer_*,   │  night; mouth from the speaker's      │ camera tracking,
  └──── start_listening, stop_pipeline, │  PipeWire monitor while a reply plays │ touch
        stop_timer_ringing ◀────────────┘                              ◀── touch, face seen
```

The robot does everything that has to be quick or smooth by itself: drawing the face,
blinking, following a face, gestures, LED animation. If the Pi goes away, it still
behaves like a pet. The bridge holds the decisions in one class with no I/O (`Brain`),
which `nix build .#checks.x86_64-linux.stackchan-bridge` drives with scripted
conversations.

### Serial protocol (version 1)

One JSON object per line, 115200 baud over the CoreS3's USB port, or over TLS when the
robot is on [Wi-Fi](#unplugged-wi-fi). The bridge sends on one link: USB when the
robot is plugged in.

| Pi → robot | Meaning |
|---|---|
| `{"mood": "neutral\|listening\|thinking\|happy\|excited\|sad\|surprised\|sleepy\|confused\|curious"}` | Face and LED scene |
| `{"look": "track\|user\|up\|center"}` | Follow faces, look at the speaker, look up (thinking), straight ahead |
| `{"gesture": "perk\|nod\|shake\|wiggle\|tilt\|dance\|look_around"}` | A head movement |
| `{"mouth": 0.0–1.0}` | Mouth opening, about 20 times a second while a reply plays |
| `{"caption": {"text", "who", "ms"}}` | Caption under the face for `ms` |
| `{"timers": [{"id", "name", "remaining_s", "total_s", "ringing"}]}` | All timers, soonest first |
| `{"sleep": bool}` | Night: dim, still, camera off |
| `{"status": {"muted", "online"}}` | Muted microphone; Home Assistant reachable |
| `{"config": {"brightness", "notice", "nap_after_s", "auto_brightness"}}` | Screen brightness; whether to greet newcomers; when to nap; whether the screen follows the room's light |
| `{"ping": 1}` | Heartbeat every 3 s. After 10 s without any line the robot shows "No Pi" |
| `{"yaw": -700…700}` | Turn the head to that angle, in 0.1° units (0 straight ahead, negative to its right) |
| `{"capture": true}` | Take a photo of whoever is in front and send it back as `face_image` |
| `{"hello": 1}` | Asks for a hello; the bridge sends it when it opens the serial port |
| `{"phones": {"beacons", "irks", "id"}}` | Whose phones to look for (beacon UUIDs and phone keys: the profile's and the router's); `null` forgets them. Over USB, or over Wi-Fi to firmware 1.9.7 and later, when the hello's `phones` differs from `id` |
| `{"led": {"rgb": [r, g, b]}}` | A notification light from Home Assistant, shown while nothing else is on the LEDs; `null` turns it off |
| `{"tap": {"url"}}` | The link it is an NFC tag with, for phones (`https://`, 120 characters at most), from the router; `""` for none, then it reads cards. The bridge gives it again after the robot restarts |
| `{"nfc_read": seconds}` | Read cards instead of being a tag, for up to 120 s ("remember my card") |
| `{"debug": "senses"}` | For 30 s, the light and proximity sensor's raw readings in the journal, 5 a second |
| `{"debug": "phones"}` | For two minutes, scan counts in the bridge's journal every 5 s |
| `{"nap": true\|false}` | Nap now, or wake from a nap (not at night) |
| `{"face_at": ms}` | Within `ms`, report where the face is once the head is still (the turn calibration) |
| `{"greet": true}` | Someone it knows came by: awake, happy, a wag and a nod, three green breaths (not at night) |
| `{"debug": "nfc"}` | For 30 s, what its NFC is doing (tag or reader, reads, field) in the bridge's journal every 2 s |
| `{"record": true\|false}` | Microphone on from the wake word to the sentence's transcript (15 s at most), with the router |
| `{"wifi": {"ssid", "password", "host", "port", "fingerprint", "token", "id"}}` | Wi-Fi details to keep; `null` forgets them. Over USB only, when the hello's `wifi` differs from `id` |

| Robot → Pi | Meaning |
|---|---|
| `{"hello": {"fw", "proto", "link", "wifi", "camera", "ear", "phones", "nfc", "reset", "up_s"}}` | When a link comes up: `link` is `usb` or `wifi`, `wifi` the id of the details it keeps (`""` for none), `camera` whether its camera started, `ear` whether its microphone works, `phones` the id of the phones it looks for (`""` for none), `nfc` whether its NFC started, `reset` why it last started (`panic`, `brownout`, `task-watchdog`, `power-on`, `software`, …) and `up_s` the seconds since (firmware 1.9.6). Over Wi-Fi it opens the connection and carries the `token`. The bridge answers with the whole state |
| `{"touch": "tap\|stroke", "zone": "front\|middle\|back\|screen"}` | Head tap or stroke, or a tap on the face |
| `{"face": "new\|lost"}` | Someone came into view, or left it |
| `{"rest": "nap\|awake"}` | It started or ended a nap |
| `{"battery": {"level", "mv", "charging", "low"}}` | On a change, and every 5 minutes |
| `{"log": "..."}` | Shown in the bridge's journal |
| `{"face_image": "<base64>"}` | A captured photo, answering `capture`; `null` if it couldn't take one |
| `{"audio": "<base64>"}` | While recording, 100 ms of 16-bit 16 kHz mono each |
| `{"senses": {"lux", "prox", "near"}}` | Light at the sensor and the proximity echo, when the hand near it comes or goes or the light changes |
| `{"nfc": "<UID hex>"}` | A tag or card tapped on it, once per tap (phones' random IDs are left out) |
| `{"tapped": true}` | A phone read its link, once per tap |
| `{"face_at": {"yaw", "faces"}}` or `{"face_at": null}` | The answer to `face_at`: the face's direction in the head's degrees and how many faces are in view, or nobody seen in time |
| `{"health": {"rssi", "heap_kb", "psram_kb", "uptime_s", "nfc", "yaw"}}` | Every minute: Wi-Fi signal (on Wi-Fi only), free memory and PSRAM, uptime, NFC mode (`tag` or `reader`), the head's direction in degrees |
| `{"wave": true}` | A hand waved across the proximity sensor (near and gone within 0.6 s) |
| `{"seen": [{"person", "rssi"}]}` | Phones it heard in the last minute (median strength), every 15 s; the bridge passes them to the router |

`face_image` is the only line that can be large (a full-frame JPEG, base64). The robot
only sends it in answer to a `capture`, never on its own. The bridge sends it on to the
router: as `face_image` when the router asked (enrolment), or as a `face_probe`, which the
router runs against Frigate: `"why": "turn"` after the robot has turned to the speaker,
`"why": "seen"` for a face that came into view.

## Troubleshooting

- **No `/dev/stackchan`.** Check `udevadm info /dev/ttyACM0 | grep -E 'ID_VENDOR_ID|ID_MODEL_ID'`
  for `303a`/`1001`. The stock firmware (or a board in download mode) may enumerate
  differently: flash the firmware first.
- **The bridge warns about the protocol.** The robot runs older firmware than the bridge
  expects. Flash `firmware/stackchan` from the same commit as the Pi's configuration.
- **It never looks at anyone.** The bridge logs `the robot has no camera` when the
  camera didn't start; everything else still works. Power-cycle it with the button.
- **It doesn't come back on Wi-Fi.** Plug it into the Pi and check the journal for
  `giving the robot the Wi-Fi details` (or nothing, when it has them already). On the
  robot's next USB session the bridge logs `robot: wifi: the bridge's certificate did not
  match` if something else answered on the Pi's address. `refused a robot on Wi-Fi`
  means a wrong token: plugging it in once gives it the current one. Nothing at all:
  check the robot can reach the Pi's address from its Wi-Fi, and `allowFrom`.
- **The mouth doesn't move.** The bridge records the default speaker's monitor through
  PipeWire. `journalctl -u lva-stackchan` shows `pw-record` errors, and
  `pw-cli ls Node | grep lva-stackchan-mouth` shows the stream while a reply plays.
- **The head jitters or a servo is limp.** The servos lose torque when they rest, to
  stay quiet and cool, and come back on the next move. A limp servo that never moves
  again points at power: check the cable and `dmesg` for undervoltage.
