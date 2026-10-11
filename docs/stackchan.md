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
  reply: happy, sad ("Sorry, I couldn't…"), or curious with a head tilt for a question.
  An error gets a head shake and a red flash.
- **Touch.** Tap its head to talk, with no wake word; tap again to stop. Stroking it from
  front to back just makes it happy. Any touch silences a ringing timer.
- **Timers.** The soonest timer counts down under its face, and the LEDs drain as it
  runs. When it rings, the robot wiggles and flashes amber.
- **A nap when nobody is around.** After 5 minutes without a face it dozes; after 15
  (`lanbat.stackchan.napAfter`) it naps: screen dark, eyes closed, LEDs off, head down,
  the camera slowed to a frame a second. Someone walking in, a tap or the wake word wakes
  it with a stretch, and Nabu knows it was napping.
- **Battery.** The robot reads its battery every 30 s. Below 15 % and not charging it
  stops moving its head (a servo's current surge on a weak supply makes its power chip
  cut it off, and then only the power button brings it back), pulses amber and asks to
  be charged; Nabu knows too. It moves again once it charges or is back above 25 %.
- **Night.** From 23:00 to 07:00 it sleeps, dimmed, still and with its camera off. The
  wake word or a tap wakes it for the conversation.
- **Status.** One dim red LED on each side means the microphone is muted. A slow red
  pulse means Home Assistant is unreachable. "No Pi" means the bridge has stopped
  talking to it.

Nothing leaves the host, with one narrow exception. Frames are analysed on the robot and
dropped; only a face's position is used, to move the head. The one thing that does leave
the robot is a photo of someone who asks to be remembered, and it goes straight to
Frigate's recognition library on the server (see [people.md](people.md)); a remembered
face only personalises the assistant and is never used to unlock, disarm, open or allow
anything. The bridge on the Pi reaches LVA on the loopback and the robot over USB.

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
- **Body commands, answered at once** without a model: "nod", "shake your head", "look
  at me", "look around", "do a little dance", "go to sleep" and "wake up". "Go to sleep"
  by day lasts until the night hours start or end.
- **It remembers people.** Ask it "remember me as <name>" and, if the robot can see you,
  it takes a photo and registers your face with Frigate, so the assistant knows your name
  in this room. The photo only goes to Frigate's recognition library and is never used to
  unlock, disarm, open or allow anything.
- **It knows who is talking.** When the router has a face library (Frigate), the robot
  captures the speaker's face, right after it turns to them, and the router names it, so
  the reply can be addressed. See [people.md](people.md) for how that becomes a name.

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
  robot's right, one per mic in channel order. The default is the PlayStation Eye's array.
  With a different four-mic card, set its points here; two mics closer than 5 mm apart
  make an axis that can't be read.
- **Channel order.** The four channels must line up with the four geometry points. The
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

One JSON object per line, 115200 baud over the CoreS3's USB port.

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
| `{"config": {"brightness", "notice"}}` | Screen brightness; whether to greet newcomers |
| `{"ping": 1}` | Heartbeat every 3 s. After 10 s without any line the robot shows "No Pi" |
| `{"yaw": -700…700}` | Turn the head to that angle, in 0.1° units (0 straight ahead, negative to its right) |
| `{"capture": true}` | Take a photo of whoever is in front and send it back as `face_image` |

| Robot → Pi | Meaning |
|---|---|
| `{"hello": {"fw", "proto"}}` | At boot and when the Pi comes back; the bridge answers with the whole state |
| `{"touch": "tap\|stroke", "zone": "front\|middle\|back\|screen"}` | Head tap or stroke, or a tap on the face |
| `{"face": "new\|lost"}` | Someone came into view, or left it |
| `{"rest": "nap\|awake"}` | It started or ended a nap |
| `{"battery": {"level", "mv", "charging", "low"}}` | On a change, and every 5 minutes |
| `{"log": "..."}` | Shown in the bridge's journal |
| `{"face_image": "<base64>"}` | A captured photo, answering `capture`; `null` if it couldn't take one |

`face_image` is the only line that can be large (a full-frame JPEG, base64). The robot
only sends it in answer to a `capture`, never on its own. The bridge sends it on to the
router: as `face_image` when the router asked (enrolment), or as a `face_probe` after the
robot has turned to the speaker, which the router runs against Frigate.

## Troubleshooting

- **No `/dev/stackchan`.** Check `udevadm info /dev/ttyACM0 | grep -E 'ID_VENDOR_ID|ID_MODEL_ID'`
  for `303a`/`1001`. The stock firmware (or a board in download mode) may enumerate
  differently: flash the firmware first.
- **The bridge warns about the protocol.** The robot runs older firmware than the bridge
  expects. Flash `firmware/stackchan` from the same commit as the Pi's configuration.
- **It never looks at anyone.** The robot logs `no camera: not looking at people` when
  the camera didn't start; everything else still works. Power-cycle it with the button.
- **The mouth doesn't move.** The bridge records the default speaker's monitor through
  PipeWire. `journalctl -u lva-stackchan` shows `pw-record` errors, and
  `pw-cli ls Node | grep lva-stackchan-mouth` shows the stream while a reply plays.
- **The head jitters or a servo is limp.** The servos lose torque when they rest, to
  stay quiet and cool, and come back on the next move. A limp servo that never moves
  again points at power: check the cable and `dmesg` for undervoltage.
