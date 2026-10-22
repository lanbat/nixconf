# Raspberry Pi 3 as a Snapcast speaker and voice satellite

A Pi 3 (B or B+) with a speaker and a USB microphone. It plays the Snapcast stream
(`modules/pi/snapclient.nix`) and is a voice satellite for Home Assistant's Assist
(`lanbatPlugins.voice`, `modules/core/voice-satellite.nix`). It uses the `voice-pi` role and `platform = "raspberry-pi-3"`
(`hosts/pi3/hardware.nix`): the stock NixOS aarch64 SD image, with U-Boot and the
mainline kernel from cache.nixos.org. The Pi 5 images from nixos-raspberrypi are not
used.

## Requirements

- **Raspberry Pi 3 B or B+**, 1 GB of RAM, wired Ethernet.
- **microSD card, 32 GB recommended (the size this setup was built and tested on).** It
  holds the whole system, since the Pi has no other storage. The root filesystem grows to
  fill the card on first boot. After the configuration was switched in and several
  generations had accumulated, about 8 GB was in use (the Nix store with the voice
  satellite, PipeWire, Bluetooth and the old generations; the capped journal adds up to
  64 MB), so 16 GB is the practical floor, and a smaller card leaves no room for
  generations and rollbacks. Use a quality card (A1/A2, a known brand): the system and
  the journal write to it all the time.
- **A 5 V / 2.5 A power supply** with a short, thick cable. A weak one makes the Pi log
  `Undervoltage detected!` and crash at random; `cat /sys/class/hwmon/hwmon*/in0_lcrit_alarm`
  reads 1 while the supply is too weak. The USB ports draw from the same supply (1.2 A for
  all four), so give a USB hub a power supply of its own. A crash shows up as a short boot in
  `journalctl --list-boots`: the hardware watchdog restarts a Pi that has stopped within
  15 s. If the card stops completing writes instead, the Pi keeps running and answering
  pings, but its log just ends. The kernel restarts it once a task has waited 5 minutes on
  the card. `journalctl -k -g Undervoltage` lists the warnings that reached the card, and one
  just before a crash points at the supply. A crash in the middle of a deploy can leave
  empty files in the Nix store, though not since `hosts/pi3/hardware.nix` flushes each
  path before registering it. `sudo nix-store --verify --check-contents` finds them. It
  reads the whole store for several minutes, so run it on a good supply.
- **A USB microphone.** The PlayStation Eye (USB 1415:2000) is the default; its speech is
  quiet, so the deploy entry sets `lanbat.voiceSatellite.microphone.volumeMultiplier`.
- **A speaker**: the 3.5 mm jack, a USB speaker or a Bluetooth speaker (section 5).

## Deploy entry

```nix
pi-voice = {
  role = "voice-pi";
  system = "aarch64-linux";
  platform = "raspberry-pi-3";
  networking = {
    ip = "192.0.2.12";
    interface = "eth0"; # pinned by hosts/pi3/hardware.nix
    hostname = "pi3";
  };
  plugins = [ inputs.self.lanbatPlugins.voice ];
  modules = [
    {
      lanbat.voiceSatellite = {
        name = "Pi 3 Satellite"; # what Home Assistant calls the device
        # backend = "wyoming";   # default — Wyoming on port 10700
        # backend = "lva";       # Linux Voice Assistant (ESPHome, port 6053, local wake word)
      };
      # lanbat.speakers.output = "usb";   # "analog", "usb", "bluetooth" or "auto"
      # lanbat.speakers.bluetooth.address = "AA:BB:CC:DD:EE:FF";
    }
  ];
};
```

The `voice-pi` role bundles the Snapcast client (`snapclient`) and the speaker choice
(`speakers`); `roleModules.snapclient = null` drops the client for a satellite that
should not play the stream. `deployments/example/deploy.nix` has the same host, which CI
evaluates.

The server's firewall admits the Pi for Snapcast and InfluxDB, and the Pi admits only the
server on the satellite's TCP port (10700 for Wyoming, 6053 for LVA), all generated from
the declared service endpoint.

## 1. Flash the card

The official image is `nixos-image-sd-card-<version>-aarch64-linux.img.zst`. **This erases
the card**; check the device with `lsblk`.

```bash
zstd -dc nixos-image-sd-card-*-aarch64-linux.img.zst | sudo dd of=/dev/sdX bs=4M conv=fsync status=progress
```

Then, on the card:

- mount the `NIXOS_SD` partition and put your key in `root/.ssh/authorized_keys` (mode 0600,
  directory 0700), so root can log in on first boot;
- mount the `FIRMWARE` partition and add `dtparam=audio=on` to the end of `config.txt`, which
  switches on the 3.5 mm jack.

The image grows its root partition to fill the card on first boot.

## 2. First boot

Connect Ethernet and power. The Pi takes a DHCP address; find it in the router, or with
`ping nixos.local`.

```bash
ssh root@<pi-ip>
lsusb                  # the microphone should be listed (PlayStation Eye is 1415:2000)
ssh-keyscan -t ed25519 <pi-ip>   # the Pi's host key, for the next step
```

## 3. Add the Pi's host key to the secrets

Add the key to `secrets/secrets.nix` as `pi-voice`, add it to `allKeys` (this Pi reads
`telegraf-token.age`), and re-encrypt:

```bash
(cd secrets && agenix -r)
```

If the Pi has a room in `lanbat.voiceRooms` it also reads `ha-voice-token.age`. With no
room, replies play on the Pi's own speaker (`alwaysPlayLocally`) and no token is needed.

## 4. Switch to the configuration

Build on the Pi (nothing here emulates aarch64); nearly everything comes from
cache.nixos.org. Use `boot` rather than `switch`, because the address changes from DHCP to
the static one in the middle of the session:

```bash
nix run nixpkgs#nixos-rebuild -- boot --flake path:.#homelab-pi-voice \
  --target-host root@<pi-ip> --build-host root@<pi-ip>
ssh root@<pi-ip> reboot
```

After the reboot, log in as `admin` on the static address. From then on:
`deploy --skip-checks path:.#homelab-pi-voice`.

## 5. Speakers

PipeWire plays to its default output, and `lanbat.speakers.output` raises one kind above the
others. Check what the Pi sees:

```bash
wpctl status           # needs the pipewire group; the default output has a *
aplay -l               # the analog jack is "Headphones", a USB speaker "USB Audio"
```

- **Analog**: plug in the jack. No setup.
- **USB**: plug it in. No setup.
- **Bluetooth**: pair once by hand, then set `lanbat.speakers.bluetooth.address` so the Pi
  reconnects whenever the speaker is switched off and on.

  ```bash
  bluetoothctl
  > scan on
  > pair AA:BB:CC:DD:EE:FF
  > trust AA:BB:CC:DD:EE:FF
  > connect AA:BB:CC:DD:EE:FF
  ```

  Bluetooth through a system-wide PipeWire is the least tested path. If the speaker pairs
  but no `bluez_output` node appears in `wpctl status`, use the analog or USB output.

  The Pi 3's onboard Bluetooth chip is unreliable under the mainline kernel: the kernel log
  fills with `Frame reassembly failed` and `unknown connection handle`, the Pi reports the
  speaker as connected while the speaker has dropped the pairing, and it is sensitive to an
  under-spec power supply. Prefer the analog or USB output, or add a USB Bluetooth adapter.

  Whichever output is used, the wake chime is padded with silence on purpose: the satellite
  mutes its microphone for the length of the chime file, and a chime the microphone still
  hears makes the assistant end the command before you speak ("No text recognized").

## 6. Voice backends (Wyoming vs Linux Voice Assistant)

`lanbat.voiceSatellite.backend` chooses the implementation (`modules/core/voice-satellite.nix`):

| | **wyoming** (default) | **lva** (Linux Voice Assistant) |
|---|---|---|
| Protocol | Wyoming (`wyoming-satellite`, port 10700) | ESPHome API (port 6053, UDP 5353 mDNS) |
| Wake word | On the server (openWakeWord, "hey nabu" in the Voice pipeline) | On the device (`lva.wakeModels`, default **`okay_nabu`**, a microWakeWord model; up to two) |
| Follow-up after "?" | No (Wyoming satellite limitation in HA 2026.3) | Yes — HA `continue_conversation`; tune `lva.continueConversationDelay` (default 0.65 s) |
| Barge-in / "stop" | Stop Wyoming TTS via HA only | Local stop word (`lva.stopWord.model`, default `stop`) during TTS and timers |
| Echo / wake during playback | Mic muted for Wyoming chime | Optional PipeWire WebRTC AEC (`echoCancellation.enable`; **off by default on Pi 3**). LVA captures `echoCancellation.pulseSourceName` and plays TTS via `pulseSinkName` + `listenDuringWakeSound` |
| HA registration | Wyoming integration (`home-assistant-post-setup`) | ESPHome integration |
| Pi microphone | ALSA capture; WirePlumber **disables** the USB card for PipeWire | Pulse capture; WirePlumber **keeps** the USB card enabled; mono + gain/noise options on `lva.*` |
| Room replies via `voice_reply` / Music Assistant | Yes, when `alwaysPlayLocally` is false | No — TTS plays on the satellite; see `room` option docs |
| Snapcast ducking during assist | Yes (`modules/pi/audio.nix`, Wyoming `--detection-command`) | Yes when `lva.snapcastDucking.enable` (default) — peripheral API on port 6055 |

Use **one backend per profile** for every satellite: the endpoint table requires the same
port on all hosts that run a satellite (10700 or 6053). Mixed Wyoming and LVA in one
profile is unsupported.

**Rollout suggestion:** deploy and validate **Pi 3 → Pi 5 → server**, updating Home
Assistant on the server after each host so `home-assistant-post-setup` adds ESPHome
devices. On the Pi 3, check `systemctl status linux-voice-assistant` and that HA sees
`_esphomelib._tcp` / the device under **Settings → Devices**.

For LVA on the Pi 3, keep `lva.audioInputChannels = 1` (2 channels crashes with current
`aioesphomeapi`). Set `lva.networkInterface` to the deploy `networking.interface` when
auto-detection fails (required on the Pi 3). Tune quiet mics with `lva.micVolume` /
`lva.micAutoGain` or the shared `microphone.volumeMultiplier` default for the PlayStation Eye.

**Conversational defaults (backend `lva`):** wake phrase **"okay nabu"**; follow-up
listening after a question; say **"stop"** to interrupt a reply; short wake chime with
`listenDuringWakeSound`; Snapcast fades in 0.2 s when the wake word is heard, to 5% of its
volume while the microphone is open and 25% while the assistant answers, stays down through
follow-up turns, and fades back over 0.8 s
(`lva.snapcastDucking.{listenVolume,volume,fadeDown,fadeUp}`).

**Commands over a speech station.** The microphone hears the speaker. With the radio at a
quarter of its volume, a command came out as "two sides obviously turn off the radio": the
presenter's words and yours in one transcript, which no command sentence matches, so it went to
the LLM. Hence the near silence while listening. Stop commands also match at the end of such a
transcript ("… stop the music", "… turn off the radio"); play commands don't, since a stray play
would be worse than a missed one.

**Every satellite with a speaker plays music.** A host running a Linux Voice Assistant
satellite also runs a Snapcast client (`modules/core/snapclient.nix`), so its speaker is a
Music Assistant player, and post-setup puts the satellite and that player in the host's
`voiceRooms` area. "Play …", "stop the music" and volume commands act on the room that heard
you; a satellite in a room with no player says "There's no speaker in here." The Pi roles run the
client regardless; `lanbat.voiceSatellite.playMusic = false` keeps another host's satellite to
spoken replies.

**Volume by voice.** "Set the volume to 70 percent", "louder", "turn the music down",
"increase the volume by 20 percent", "volume up by 30", "volume down": these set the room's
Music Assistant player, which is the music's volume, or Kodi's while a film or show is on
(playing or paused). Say "set the volume to 70", not just "volume 70". (Home Assistant's own
volume sentences need a player's name; without one they reached the LLM, which can't set a
volume, and a phrasing these sentences miss is answered "Volume set" by Home Assistant's own
handler, which moves some other player.) Ducking never touches that volume: it lowers the Snapcast stream in
PipeWire, and keeps the levels to return to in `/run/lva-snapcast-duck/saved.json`, so a ducker
stopped or restarted while the music is down (a deploy) still puts it back.

**Speaking up in a loud room.** With `lva.adaptiveVolume.enable` (default), the ducker also
takes a one-second level of the microphone every 30 s while nothing is happening (the
echo-cancelled one when there is one), and the median of the last five is how loud the room
is. A reply is then played louder the further the room is above `quietDb` (−55 dBFS): half a
dB per dB, at most `maxBoostDb` (8 dB), and never past full scale. So set the satellite's
volume in Home Assistant for a **quiet** room: that's what replies use when it is quiet, and
the room's noise only adds on top; around 50 % leaves the full 8 dB of headroom. Only the
level is kept, never audio. The journal says what it did: `room -38 dBFS: voice +8.0 dB`.

**Echo cancellation with the music (experiment).** `echoCancellation.includeMusic = true`
(with `echoCancellation.enable`) plays Snapcast through the echo canceller's sink, so the music
is part of its reference and is taken out of the microphone too, not only the assistant's voice.
It costs CPU for every second of music; measure it on a Pi 3 before leaving it on.

**Wake words.** `lva.wakeModels` lists one or two (LVA listens for at most two at once).
They are written into LVA's `prefs.json` every time it starts, so the Nix setting wins over a
choice made in Home Assistant's "Wake word" selects, which lasts until the next restart.

**False wakes.** `lva.wakeThreshold` is the probability the model must reach
before it counts. Home Assistant shows the same number as Wake Word sensitivity:
higher wakes less often. `okay_nabu` ships at 0.85, which a busy room (speech, a
radio, a keyboard beside the PlayStation Eye) clears often. About a tenth more,
0.95, is the step that quiets that; a quiet room can leave the option unset and
keep the model's own cutoff. The value is written into `prefs.json` on every
start, so it wins over the Home Assistant control.

The default is "okay nabu", Home Assistant's own wake word. "Hey nabu", which the Wyoming
pipeline uses, exists only as an openWakeWord model, which runs several neural networks on every
audio frame. Measured on the Pi 3 (1.2 GHz, 30 s of audio, 2026-10-06):

| Active | CPU (one core) | Speed vs. realtime |
|---|---|---|
| `okay_nabu` (microWakeWord) | 7 % | 13.7x |
| `hey_jarvis` (microWakeWord) | 7 % | 14.7x |
| `okay_nabu` + `hey_jarvis` | 14 % | 7.0x |
| `hey_nabu` (openWakeWord) | 109 % | 0.9x, slower than realtime |
| `okay_nabu` + `hey_nabu` | 117 % | 0.8x |

openWakeWord falls behind the microphone on a Pi 3, so `hosts/pi3/hardware.nix` warns when it is
listed there; a faster CPU (the server, a Pi 5) runs it. LVA's bundled microWakeWord models are
`okay_nabu`, `hey_jarvis`, `hey_mycroft`, `alexa` and others.

LVA needs a PulseAudio server (its `soundcard` library talks to nothing else): every LVA host
gets a system-wide PipeWire with one (`modules/core/voice-satellite-audio.nix`), which also
names the microphone's capture node `lanbat_ps_eye_capture` (mono) and sets up echo
cancellation when `echoCancellation.enable` is on. The Pis already run PipeWire for Snapcast;
the server gets it for its satellite.

**The microphone watchdog.** The PlayStation Eye runs at 16 kHz on its own USB clock and
follows the speaker's. Now and then PipeWire's reads of it start failing, a few a minute at
first, then every one of them, for hours, until it resyncs by itself. Meanwhile the wake word
is never heard. `lanbat-mic-watchdog` follows PipeWire's log and, at 30 failures in a minute,
turns the microphone's card off and on again, which reopens it cleanly. Then it restarts LVA,
because the card change moves LVA's recording off the echo-cancelled microphone. It acts at
most every five minutes. Look for it with `journalctl -u lanbat-mic-watchdog`, and for the
failures with `journalctl -t pipewire | grep snd_pcm_mmap_commit`. Turn it off with
`lanbat.voiceSatellite.microphone.watchdog = false`.

The microphone can also stall without an error: its card stays open and PipeWire hands out
digital silence, so the wake word is never heard and nothing is logged. On the Pi 5 (2026-10-21)
ALSA's `hw_ptr` in `/proc/asound/card3/pcm0c/sub0/status` sat at 0, a direct `arecord` failed
with an I/O error, and the kernel kept logging `current rate N is different from the runtime
rate 16000` with a garbage N. So every 30 seconds the watchdog also reads two seconds from the
capture node `lanbat_ps_eye_capture`; a window of nothing but zero samples is a silent check.
Three in a row reopen the card as above, three more make the kernel enumerate the USB device
again (the `authorized` switch in sysfs, a software unplug and replug), and after that the
journal says every half hour that the microphone has to be unplugged and plugged in again,
or its hub power-cycled. That stall survived both software steps, so expect the last message
to be the one that matters; the first two cost a few seconds of the satellite and are harmless.

`voice-satellite-diagnostics` (run with `sudo`) checks the satellite in one go: its services,
Home Assistant's connection, Snapcast, the audio devices and streams, three seconds of
microphone level, CPU, memory, temperature, undervoltage and recent errors. On the Pi 3,
`echoCancellation.enable` defaults to **false** (CPU cost); turn it on if you need reliable
wake/stop detection while music or TTS is playing. On Pi 5 and the server satellite, AEC
defaults to **on** when PipeWire is used.

Example module overlay:

```nix
lanbat.voiceSatellite = {
  backend = "lva";
  lva = {
    continueConversationDelay = 0.65; # lower = snappier follow-up; raise if the mic catches TTS tail
    listenDuringWakeSound = true;
    snapcastDucking.enable = true;
  };
  echoCancellation.enable = true; # false on Pi 3 unless you accept the CPU cost
};
```

## 7. Home Assistant

With backend **wyoming**, `home-assistant-post-setup` registers the Wyoming satellite of
every `voice-pi` host (as `satellite-pi-voice`) plus the storage Pi and optional server
satellite. With backend **lva**, it adds **ESPHome** config entries titled with each
host's `lanbat.voiceSatellite.name` instead.

Give each device an area. The **Voice** assist pipeline (openWakeWord or local wake,
faster-whisper, Piper, `lanbat.deployment.haLlm`) stays the profile default;
ESPHome satellites use the preferred pipeline when their pipeline select is unset.

Wyoming only: `home-assistant-post-setup` sets **Finished speaking detection** to
**Aggressive** once. LVA does not use that entity.

The conversation agent runs on the server (`lanbat.deployment.haLlm`, see
`services/llama-cpp.nix`), and so do speech-to-text and Piper. Satellites never embed an
LLM: point `lanbat.deployment.haLlm.baseUrl` and `.model` at any OpenAI-compatible API
(local `http://127.0.0.1:8091/v1` from `services/llama-cpp.nix`, or e.g. a Mac running
llama.cpp, LM Studio or Ollama behind `/v1`). External APIs use the `ha-llm-api-key` secret
(`secrets/README.md`).

**The assistant router.** With `haLlm.baseUrl = "http://127.0.0.1:8092/v1"` and
`model = "assistant"`, what Home Assistant's intents miss goes through
`services/assistant-router.nix`:

1. A noise gate, with no model: fragments ("Turn.", "Play."), the assistant's own words
   heard back (from this satellite's last reply or in its usual phrasing), the middle of
   someone else's sentence and several sentences of radio or film speech get "Sorry, I didn't
   catch that." (no question, so the microphone doesn't reopen on it);
   "stop", "cancel" and "never mind" stop. Questions, news, music to find, reminders and
   anything for several devices ("all", "everywhere", "the lights", "the TV and the radio") go
   straight to the cloud. A cloud model's tool calls are checked: only the agent's two
   functions, on exposed devices, with the actions they list.
2. The local model (`lanbat.services.llama-cpp.settings.model`, `qwen3-4b` recommended)
   answers in a few tokens whether it is a clear command for one device ("act"), which it
   then does through Home Assistant's tools, or needs a question, the cloud or nothing.
3. The cloud, through the LLM gateway (`services/llm-gateway.nix`): Claude Haiku by default,
   with fallbacks and a monthly budget (`lanbat.services.llm-gateway.settings`; LiteLLM
   counts in memory and starts again when it restarts, so also set a spend limit in the
   provider's console), keys in `llm-gateway-env`. A reply that claims an action no tool confirmed is replaced by
   "I didn't change anything."

`lanbat.services.assistant-router.settings.mode` picks `local-first` (the above),
`cloud-first` (no local model step) or `local-only` (no cloud: "I can't do that offline").
Every request is a JSON line in `/var/lib/assistant-router/requests-<date>.jsonl` (what was
heard, tier, route, milliseconds), kept `logDays` days (14); `logText = false` keeps no text.
`jq -r '[.tier, .route, .ms, .text] | @tsv'` on a day's file shows what the assistant does
with real speech.

The Pi 3 streams audio and plays replies; with LVA it also runs the
wake word locally (~18–30% CPU idle in testing, more with AEC).

## 8. The same satellite on every machine

The satellite is the same on the server, a Pi 3, a Pi 4 and a Pi 5, and each one is ready
for a PlayStation Eye before it is plugged in. The satellite runs, Home Assistant knows it,
and it waits quietly (one log line, no restart loop) until the camera's USB ID appears;
plug it in at any time, or move it, and the satellite listens. The microphone gain the Eye
needs is the default, so there is nothing to set per host.

| Machine | How it gets a satellite | Speaker |
|---|---|---|
| Server | `lanbat.voiceSatellite.enable = true;` in the server's `modules` (or a place in `voiceRooms`) | the onboard codec, `lanbat.services.wyoming.settings.satellite.speaker` |
| Pi 5 (`storage-pi`) | the `voice` plugin | PipeWire, `lanbat.speakers.output` |
| Pi 4 (`voice-pi`, `platform = "raspberry-pi-4"`) | the `voice` plugin | PipeWire, `lanbat.speakers.output` |
| Pi 3 (`voice-pi`, `platform = "raspberry-pi-3"`) | the `voice` plugin | PipeWire, `lanbat.speakers.output` |

Home Assistant registers a satellite for the server, the storage Pi and every `voice-pi`
host when the server is deployed. Give each satellite its own `name` (`lanbat.voiceSatellite.name`),
and put each in the area it is in.

**Duplicate wake-up:** with Wyoming, two satellites hearing the same server-side wake word
make Home Assistant discard one ("Duplicate wake-up detected"). With LVA, each device runs
its own wake word — two Pis in one room can both fire on `okay_nabu`. Keep one active
satellite per room, or use different `lva.wakeModels` per device.

**After deploy, try:** "okay nabu" → a command; ask something that ends in "?" and speak again
without the wake phrase (follow-up); say "stop" during a long reply (barge-in); play Snapcast
music and repeat (ducking + optional AEC).

## 9. A satellite on the TV box (Kodi)

A host with the `tv` plugin (Kodi on the TV) and an LVA satellite gets more, with nothing
to set:

- **Kodi in Home Assistant.** Kodi's web server and its TCP notification port are on,
  with the `kodi-web-password` secret, and only the Home Assistant host is admitted to
  them (the `kodi` and `kodi-events` endpoints). Home Assistant adds every Kodi and puts
  it in its host's `voiceRooms` room.
- **A film pauses while you talk.** On the wake word, a video Kodi is playing pauses; when
  the conversation ends (after the reply has played) it carries on three seconds back, so
  the line said over the wake word isn't lost, unless you said "pause", "stop" or started
  something else meanwhile. Music Kodi plays fades down like Snapcast's.
- **Captions.** "Listening…", what you said and the reply show on the TV as Kodi
  notifications.
- **TV power over CEC.** "Turn the TV off/on" switches the TV through Kodi's CEC adapter
  (`CECStandby` / `CECActivateSource`), sent through Kodi's EventServer on the loopback.
- **Sound survives a PipeWire restart.** Kodi's PipeWire output never reconnects, so the TV
  sessions (Kodi and EmulationStation) restart with PipeWire. A deploy that restarts the
  audio stops a film for a moment instead of leaving it silent until someone restarts Kodi.
  If a film has no sound, `journalctl -u tv-kodi` and Kodi's `~media/.kodi/temp/kodi.log`
  say `no sink was returned` when Kodi has lost PipeWire; `sudo systemctl restart tv-kodi`
  brings it back.
- **Films and episodes by voice.** "Watch The Matrix", "play the movie Heat", "play the
  next episode of Severance", "play the show The Bear" look in that room's Kodi library:
  a film first, then a show's first unwatched episode.
- **The film or show on the room's Kodi.** "Skip back 30 seconds", "go forward 2 minutes",
  "rewind" (30 seconds), "subtitles on/off", "what am I watching", "stop the film",
  "next episode" / "previous episode". With nothing on, the reply says so.
- **Echo cancellation for everything that plays.** Kodi plays PCM into the system
  PipeWire (passthrough off). With `echoCancellation.includeMusic`, Kodi and Snapcast
  play into the canceller's sink, so films and music are taken out of the microphone too.
  It runs off the microphone, so they play only while the microphone is plugged in, and
  it costs CPU for every second that plays: measure it with a film on before keeping it.

`pkgs/lva-kodi-companion` does the pausing, captions and CEC on the TV box; it follows
LVA's peripheral WebSocket and talks to Kodi on the loopback only.

**Reply volume.** Replies are mastered near full scale, a film's dialogue far below it, so
at the same volume the assistant shouts over the film. Set the satellite's volume for the
host, applied at every start (Home Assistant's volume for the satellite changes it until
the next):

```nix
lanbat.voiceSatellite.lva.volume = 0.4;
```

For the film itself, keep Kodi's volume high and turn the speakers down: Kodi's scale is
steep (50 % is about −30 dB), and a quiet signal turned up in the speaker is noisier.

## 10. A Stack-chan robot as its face

M5Stack's StackChan kit, plugged into the satellite's USB port, shows what the assistant
is doing. It looks at the people in front of it, listens and thinks visibly, moves its
mouth with the reply, shows captions and timers, and starts a conversation when you tap
its head. It needs the LVA backend and the `stackchan` plugin; see [stackchan.md](stackchan.md).
