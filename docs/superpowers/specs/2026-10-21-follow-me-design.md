# Follow-me: music, lights and empty rooms

## Intent

People walk around the house. Music started in one room should be playing where they
are, and a room nobody is in should stop using electricity and sound. In the evening a
dark room should light up when someone walks in. Today the house senses where people are
(`docs/people.md`) and reacts to nothing: no automation reads a person's room.

## What exists (checked on the server, 2026-10-21)

- **Presence.** The assistant router publishes `sensor.lanbat_people_where_<key>` per
  person (`pkgs/assistant-router/assistant_router/mqtt.py`). Its state is a room name
  (the keys of `lanbat.deployment.voiceRooms`: `Office`, `Bedroom 1`, plus the Kitchen's
  Echo Show), `home` or `away`. A phone counts as in a room two minutes after it was last
  heard, a face on a camera ten seconds. Only rooms with a sensor can be reported: today
  Office, Bedroom 1 and Kitchen. Living Room, Bathroom, Landing and Hall have none.
- **People.** `lanbat.deployment.people` has one entry, `akadmin`. Follow-me is only
  meaningful with more; their beacons are a separate step (`docs/people.md`,
  "Phones in the room").
- **Players.** Music Assistant exposes one player per Snapcast client, placed in an area
  through its device: Office (`core`, `pi3`), Bedroom 1 (`binturong`), Living Room
  (`box_r_4k_plus`), Kitchen (`echo_show_8`). `media_player.snapweb_client` has no area.
  The voice code already finds a room's player with `integration_entities('music_assistant')`
  and `area_entities(area)` (`services/home-assistant.nix`); follow-me reuses that lookup.
  Music Assistant is 2.7.11. Whether Home Assistant's integration for it offers a
  queue-move action (`music_assistant.transfer_queue`) on this version was checked by the
  spike (see Spike results).
- **Lights.** Zigbee lights in the Living Room (spots, shelf, poster) and Bedroom 1,
  some bulbs with no area. The Stack-chan and Echo Show `light.*` entities are their
  screens and LEDs: follow-me never touches them.
- **Light levels.** Illuminance sensors exist for Office, Bedroom 1 and Kitchen
  (Stack-chan, Echo Show). The Living Room has none.
- **Sun and helpers.** The `sun` integration is not set up (no `sun.sun`) and there are
  no `input_boolean` helpers. Both are added.

## Behaviour

Per room, per person tracked. Defaults are the author's choice; every number is a setting.

1. **Lights on arrival.** A tracked person's room becomes R and stays R for 10 s; R has
   lights configured, they are all off, it is dark in R and the time is in the evening or
   night window. Dark means the room's illuminance sensor reads under 30 lx, or, with no
   sensor, the sun is below -1 degree. Evening (17:00 to 22:30): lights on, 100 %. Night
   (22:30 to 06:30): 20 %, warm. Daytime: nothing.
2. **Lights off when empty.** R has had no tracked person for 5 minutes and follow-me
   itself turned its lights on. Lights someone switched on by hand are never touched.
3. **Music follows.** A person moves from room A to room B (stable 20 s) while A has a
   Music Assistant player playing, that person was the only tracked person in A, and B
   has a Music Assistant player that is not playing. The queue moves to B and A stops.
   With someone else still in A, nothing moves: A keeps playing. A 60 s cooldown per
   person stops it from bouncing between two adjacent rooms.
4. **Music pauses when empty.** A room with a covered presence sensor has had no tracked
   person (see "Occupied" below) for 15 minutes: its Music Assistant players are paused, not stopped, so
   "resume" works. TVs (Kodi, Android TV video) are never paused by follow-me.
5. **Dry run first.** With `dryRun = true` (the default when enabled), every decision is
   written to the logbook as "Follow-me would ..." and nothing is switched.
6. **Switches.** A global "Follow-me" switch and a "Guests here" switch (suspends 2 and 4,
   since guests carry no tracked phone) in Home Assistant. Unknown or unavailable
   presence is never read as "empty": the automations then do nothing.

### Occupied

A room is occupied when a tracked person's where sensor equals its name, or when that
sensor says `home` (a person the house hears but cannot place) and the room is the last
covered room the person was in, for up to 20 minutes after the sensor changed to `home`.
`away` and a different room free it at once. `unknown` and `unavailable` make it
`unavailable`, which no rule treats as empty. This exists because of the spike below: one
person's sensor goes room, `home`, room within a minute or two.

## Spike results (2026-10-21)

- **Queue move.** `music_assistant.transfer_queue` exists in Home Assistant 2026.3.4
  (target: the destination player; fields `source_player`, `auto_play`). Music Assistant
  2.7.11's `transfer_queue` copies the queue, shuffle and repeat, resumes at the source's
  elapsed time, and **clears the source queue, which stops the source player**. With
  `auto_play` omitted it plays only if the source was playing. A destination that is
  grouped is ungrouped first, with a 3 s wait. So follow-me needs no separate stop, and
  allows a few seconds of gap.
- **Where states.** The recorder holds exactly `Office`, `Bedroom 1`, `Kitchen`, `home`,
  `away`, `unknown`, `unavailable` for `sensor.lanbat_people_where_akadmin`. Rooms hold
  for 90 s to a few minutes, then fall to `home` while the person may still be there;
  people reach the next room through a `home` gap of 30 to 60 s.
- **Restarts.** Every deploy or router restart shows `unavailable`, `unknown`, `away`,
  `home` within about 3 s. `away` is therefore not trusted alone: the timers (minutes) ride
  over it, and `unknown`/`unavailable` do nothing.
- **Not measured.** The audible hand-over (gap length on a Snapdroid client) and walking
  timings: both are in the dry-run walkthrough (plan, Task 8).

## Goals

- Music, lights and an empty room react to who is where, using only signals the system
  already produces.
- Everything is declared in `deploy.nix` (rooms, lights, thresholds); no entity id is
  hard-coded in the module.
- It can be watched before it acts (dry run), and stopped with one switch.

## Non-goals

- Playing music in several rooms at once (a group) so that the person is covered while
  walking. A later mode; the Snapcast groups support it.
- Rooms with no presence sensor. Adding a scanner there is hardware and a separate step
  (a Pi or an ESP32 running `room-presence`). A room in `rooms` with no sensor is
  rejected at evaluation.
- Deciding who started a track. Attribution by "the only tracked person in the room" is
  enough for a household; voice or app attribution is out of scope.
- Locks, alarms, heating, cameras. Follow-me switches only the lights and players it is
  given.
- Turning lights on by day.

## Anti-goals

- Never let a wrong presence guess lock someone out, silence a party or leave them in the
  dark with their own switch overridden. Presence is a hint (`docs/people.md`, "Never
  authority"); a failure must look like "nothing happened", not "everything turned off".
- Never fight a person: a light they switch off stays off until they leave and come back.

## Constraints

- Built as a plugin like `plugins/android`, enabled per host in `deploy.nix`
 , configuring Home Assistant only.
- No new service and no secrets. The logic is Home Assistant automations, template sensors
  and helpers generated from Nix, as the voice automations are in `services/home-assistant.nix`.
- Deployments are by hand from `origin/master`. It must be merged before it is deployed,
  or the next deploy reverts it (as happened to #233 on 2026-10-21).
