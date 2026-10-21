# Personalization: a screen that knows who is standing in front of it

## Intent

The kitchen Echo Show 8 (TECHO5, `docs/techo5.md`) already knows who is in front of
it and switches to that person's dashboard view, glance chips, calendars and photos.
That is a thin layer: the per-person view differs from the household's by a greeting
and a "who's where" list. Each person should get a screen that shows what they would
otherwise reach for their phone to check: what needs doing now (reminders), what is
next (calendar), their to-dos, and the home status that matters to them. It appears
once they are recognised, and their music is already playing in the room by then
(follow-me).

The aim is convenience per person, with sensible behaviour when the person is
unknown, several people are present, or nobody is.

## What exists (checked 2026-10-21, `origin/master` b98efec)

- **Recognition.** The assistant router names who is in a room (face, voice, phone, tap
  or card) and tells the screen's bridge (`pkgs/techo5-bridge`, `Brain.profile()`). One
  known person gives their profile; nobody, an unknown person or several people give
  the household's.
- **Per-person profile** (`deployment.techo5Devices.<room>.profiles.<person>`): `view`,
  `glance` (entity ids, 8 at most), `calendars`, `immichPerson`. These are sent to the
  device as its own actions (`dashboard_path`, `home_glance`, `calendar_sources`) and
  the `immich_person` setting.
- **Dashboard generator** (`pkgs/home-assistant-dashboards/generate.py`,
  `screen_sections`): one view per person, drawn with the cards the device draws itself
  (headings, tiles, markdown, pictures, visibility conditions).
- **Greeting and sign-in QR** for people it cannot name (`brain.py`).
- **Screen off** after `screenOffAfter` minutes of an empty, idle room.
- **Follow-me** (`plugins/follow-me`, `lib/follow-me.nix`), merged, dry run by default:
  lights on at arrival, lights off and music paused in an empty room, and the music
  queue moves to the room a lone listener walks into (`music_assistant.transfer_queue`,
  which stops the source player).
- **The device's own pages.** The clock, calendar, timers, the music player and the
  photo frame are native to the device, not dashboard cards.
- **Device reminders.** TECHO5 has `reminder_set`: it chimes, speaks and shows on the
  device that holds it and optionally others. It is not tied to a person and is not
  shown on recognition.
- **To-dos.** Nothing in Home Assistant is set up as a personal to-do list today (the
  assistant router's triage test mentions `todo.shopping_list`, as an example entity).

## Behaviour

1. **Recognised, alone: the person's view.** When the router places exactly one known
   person in the room, the screen shows their view with, top to bottom:
   1. **Reminders**: the person's pending and due reminders, as one card at the top.
      The card is hidden when there are none, and the sections below move up.
   2. **Next up**: the person's next calendar events today and tomorrow.
   3. **To-do**: the person's list, the first few open items and how many more.
   4. **Home**: glance chips chosen for the person (the washer, the others' locations),
      the room's lights-off button, and the weather.
   5. **Player**: the device's own music page. By the time the person is recognised,
      follow-me has already moved their music to the room, so nothing is offered or
      asked.
2. **Reminders show only after recognition.** They are never spoken and never shown to
   an unknown person, to a household view or to someone else. A reminder that comes due
   waits until its person is recognised in a room with a screen, then appears at the
   top, with the greeting. It stays until the person marks it done on the screen or
   elsewhere.
3. **Not alone, unknown, or nobody: the household view.** The shared calendar, the
   weather, the room and the home status. No reminders, no to-dos.
4. **Reflows to the screen.** The view is a stack of independent sections in priority
   order (the order above). TECHO5 lays sections out in two columns on an Echo Show and
   one on a Spot; section visibility rules by screen width drop the optional sections
   (Home) on the smallest screens. Reminders and the greeting never hide. No pixel
   positions, no assumption about the number of columns.
5. **Follow-me leaves when the person leaves.** The existing follow-me rules apply: the
   music goes with the person, the source room's player stops, an empty idle room
   turns off its Echo Show screen and (see Decisions) its lights.

## Decisions

Made by the user:

- The screen **never offers** to move the music: when someone is recognised and their
  music is playing elsewhere, it is moved to the room's speaker. When they walk into
  another room, the previous room stops, and if there is no activity there the Echo's
  screen and the lights turn off.
- The base layout is "**reminders first, the rest below**" with the photo kept as the
  device's own photo page.
- It should work well on most screens, which means reflowing sections rather than a
  layout drawn for one resolution.

My calls (marked, to be checked):

- **Reminders and to-dos live in Home Assistant.** One `todo.*` entity per person for
  to-dos, and one more per person for reminders, whose items carry a due time. Home
  Assistant's own apps edit them, the voice assistant can add to them, and no new
  service or secret is needed. (TECHO5's built-in reminders stay as they are, and are
  not used for personal reminders.)
- **How the dashboard shows the items.** The device signs in to Home Assistant as the
  voice satellites' user, who is not an administrator and cannot render templates, so a
  markdown card with a template stays empty on the device (`docs/techo5.md`, Limits). The
  bridge (or the router) therefore reads each person's lists with the `todo.get_items`
  service and publishes them as plain entities the dashboard can show as tiles (one per
  open reminder, a few for to-dos, each with a visibility condition on being non-empty).
  To check in the first step: that `todo.get_items` works for that user.
- **Privacy is by presence, not secrecy.** Anyone with a Home Assistant login can open
  another person's list; the point is that the screen in the kitchen does not show
  someone's reminders to a bystander. That is a non-goal to improve.
- **Marking done on the screen** is a tap on the reminder tile, which completes the
  item in Home Assistant. Both `todo.update_item` and the tile's tap action are allowed
  to a non-administrator.
- **Dry run first, as follow-me.** The "everything turns off" part rides on follow-me's
  existing dry-run setting and switches.

## Goals

- Each recognised person sees, without a tap, what needs doing, what is next, their
  to-dos and their part of the home.
- Reminders reach the right person after recognition, never a bystander.
- The same view reflows on every screen the profile has, without per-device layouts.
- Everything is declared in `deploy.nix` per person and per screen; no entity id or
  person is hard-coded in a module.

## Non-goals

- Keeping lists secret from the household (they are ordinary Home Assistant entities).
- Spoken reminders, push notifications to phones, or reminders that follow someone
  between rooms. A reminder shows when its person is recognised at a screen.
- A new reminder or to-do service, or editing lists on the device (beyond marking an
  item done).
- Acting on recognition for anything but display and following music and lights:
  recognition is a hint, never authority (`docs/people.md`).
- Layouts drawn per device, and the streamed dashboard mode.

## Anti-goals

- A reminder shown to the wrong person, or to a household view, or spoken aloud in a
  room with others. A wrong guess must show less, never someone else's items.
- A recognised person left staring at an empty or loading screen: with no items, the
  view shows the next events and the home status, not blank cards.
- Turning off lights or a screen on someone in a room because their phone dropped out
  of Bluetooth: follow-me's grace periods and "unknown is not empty" rules apply.

## Constraints

- Only cards the device draws itself: headings, tiles, markdown without templates,
  pictures, graphs, conditionals and visibility (TECHO5 `docs/dashboards.md`).
- Two columns on an Echo Show, one on a Spot; the Show 8 is 1280×800. Only the kitchen
  Show 8 (TECHO5 v1.2.24) can be seen for real. Other screens follow the same rules but
  are untested.
- Follows `CONTRIBUTING.md`: the settings are options in `modules/core/settings.nix`
  with a placeholder in `deployments/example/deploy.nix` and the entry in
  `deploy.nix.example`; docs updated (`docs/techo5.md`, `docs/people.md`).
- Deployments are by hand from `origin/master`: merge before deploying.
- Always-on, nothing on the workload layer; nothing new at rest beyond Home Assistant's
  own list storage.

## Left to the builder

- Where the reading of the lists lives (the bridge or the router) and how often it
  refreshes.
- The exact entity names and the shape of the per-person options (`todo`,
  `reminders`) in `techo5Devices.<room>.profiles.<person>`.
- Which glance defaults to give a person who has none.
- How many to-do and reminder items fit before "+N more".
- Tests: the dashboard generator's tests, `test_brain.py`, and a fixture in
  `tests/ha-dashboards.py`.
