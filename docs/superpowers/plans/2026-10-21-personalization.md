# Personalization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A recognised person alone in front of an Echo Show sees their reminders, next events, to-dos and home status; lights go off in a room left empty, whoever switched them on.

**Architecture:** The techo5 bridge reads the lone person's Home Assistant `todo` lists and calendars with the satellite token and hands the result to the assistant router, which publishes it as MQTT sensors for the *room* (cleared the moment that person is not alone there). The generated per-person dashboard view shows those sensors as tiles, with visibility conditions. A script completes a reminder on a tap. Follow-me gets one more light-off rule.

**Tech Stack:** Python 3 (techo5 bridge, assistant router, pytest), Nix (module options, Home Assistant YAML generation), Home Assistant REST and MQTT discovery, TECHO5 drawn dashboards.

**Spec:** `docs/superpowers/specs/2026-10-21-personalization-design.md`

## Global Constraints

- Only cards the device draws itself: headings, tiles, markdown without templates, pictures, graphs, conditionals and visibility (spec, Constraints).
- Two columns on an Echo Show, one on a Spot; the Show 8 is 1280×800. No pixel positions, no assumption about the number of columns (spec, Behaviour 4).
- Reminders and to-dos exist in the room's sensors only while exactly one known person is placed in that room; never spoken (spec, Behaviour 2 and 3).
- The bridge uses the satellite user's token. No administrator token on the bridge (spec, My calls: Credentials).
- A reminder is an open item of the reminders list due now or overdue (oldest first), then due later today. Reminders are completed by tap; to-dos are display-only (spec, My calls).
- Follow-me's lights-off for hand-switched lights: 10 minutes empty, following follow-me's dry run, pause and guests switches; never TVs or video, never the Echo Show's or Stack-chan's own lights (spec, Defaults).
- No new secret, no new service, always-on tier (spec, Constraints).
- Follow `CONTRIBUTING.md`: option in `modules/core/settings.nix`, placeholder in `deployments/example/deploy.nix`, entry in `deployments/homelab/deploy.nix.example`, docs updated.
- Deployments are by hand from `origin/master`: merge before deploying.

## Review Focus

- Two known people in the room, or one unknown: no item sensor holds anything (private items never reach a bystander). Pinned in Task 4 and Task 5.
- The router, the bridge or Mosquitto restarts while items are published: retained item sensors from before are cleared at the router's start, so nothing stays on a screen nobody is standing at. Task 5.
- Home Assistant unreachable, the token refused (403), or a list that does not exist: the view still shows events and home status, with no blank or error card, and the bridge does not loop on the failure. Tasks 3 and 4.
- Hostile or odd item text: empty, 10 000 characters, newlines and markup, 500 items: titles are cut to 60 characters on one line, counts are capped, nothing is rendered as markup. Task 3.
- A date-only due (`2026-10-21`), an item due at midnight, a due time across a DST change, and a reminder completed twice or already gone when the tile is tapped: the script ends without error. Tasks 3 and 6.

---

### Task 1: Check what the satellite token can do

**Files:**
- Modify: `docs/superpowers/specs/2026-10-21-personalization-design.md` (new section "Spike results")

**Interfaces:**
- Produces: for each of `todo.get_items`, `todo.update_item`, `calendar.get_events`, `script.turn_on` of a script, and `POST /api/states/<id>`: works or refused for the `Voice satellites` user, and the loopback URL Home Assistant answers on from the server. Tasks 3 and 6 use the verdicts.

- [ ] **Step 1: On the server, read the voice token and call the REST API as the bridge will**

```bash
TOKEN=$(sudo cat /run/agenix/ha-voice-token)
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $TOKEN" \
  -X POST "https://ha.<domain>/api/services/todo/get_items?return_response" \
  -H 'Content-Type: application/json' -d '{"entity_id":"todo.shopping_list","status":"needs_action"}'
```

Repeat for `calendar/get_events?return_response` (`{"entity_id":"calendar.family","duration":{"hours":48}}`) and `todo/update_item`. Expected: `200` for each. A `403` or `401` is a failed check.

- [ ] **Step 2: Record the verdicts in the spec's "Spike results"**

If any call is refused, grant that one capability to the satellite user in `pkgs/home-assistant-post-setup/setup-ha.sh` (the user's `system-users` group stays); never put an administrator token on the bridge. Record the verdict, the URL the bridge will use (`https://ha.<domain>`, or loopback if the CA is a problem) and commit.

```bash
git add docs/superpowers/specs/2026-10-21-personalization-design.md
git commit -m "Spec: what the satellite token can do (spike)"
```

---

### Task 2: Per-person lists in the profile

**Files:**
- Modify: `modules/core/settings.nix` (the `profile` submodule, around line 460)
- Modify: `services/techo5-bridge.nix` (`profileJson`, the `profiles`/`household` JSON)
- Modify: `deployments/example/deploy.nix`, `deployments/homelab/deploy.nix.example`
- Test: `tests/service-settings.nix` (the techo5 case)

**Interfaces:**
- Produces: profile options `todo` and `reminders`, each `types.nullOr (types.strMatching "todo\\.[a-z0-9_]+")`, default `null` (the default list is `todo.<person key>_todo` and `todo.<person key>_reminders`). `profileJson` adds `todo` and `reminders` strings to the bridge's JSON profile (resolved with the person key, empty for the household).
- Consumes: nothing from earlier tasks.

- [ ] **Step 1: Write the failing test** in `tests/service-settings.nix`: a techo5 device with `profiles.alex = { }` and `profiles.sam.todo = "todo.sam_chores"` evaluates, and the bridge's config JSON has `profiles.alex.todo == "todo.alex_todo"`, `profiles.alex.reminders == "todo.alex_reminders"`, `profiles.sam.todo == "todo.sam_chores"`, and `household.todo == ""`. A value such as `"sensor.x"` for `todo` is rejected at evaluation.
- [ ] **Step 2: Run it to see it fail**

Run: `nix build .#checks.x86_64-linux.service-settings` Expected: FAIL (option does not exist).

- [ ] **Step 3: Add the two options with descriptions** to the `profile` submodule, and `profileJson = key: p: { ...existing; todo = ...; reminders = ...; }` taking the person key (the household passes `null` and gets empty strings).
- [ ] **Step 4: Add placeholders** (`todo`/`reminders` commented, with the defaults named) to the two deploy files.
- [ ] **Step 5: Run `nix fmt`, then the check again.** Expected: PASS.
- [ ] **Step 6: Commit** `git commit -am "techo5: per-person to-do and reminder lists in the profile"`.

---

### Task 3: Reading lists and calendars

**Files:**
- Create: `pkgs/techo5-bridge/techo5_bridge/lists.py`
- Test: `pkgs/techo5-bridge/tests/test_lists.py`

**Interfaces:**
- Produces, in `lists.py`:
  - `@dataclass class Item: uid: str; title: str; due: datetime | None; list: str`
  - `@dataclass class Event: title: str; start: datetime; all_day: bool`
  - `def clean(text: str, limit: int = 60) -> str`: one line, control characters and markup characters (`` ` * _ [ ] < > ``) removed, cut to `limit` with `…`.
  - `def parse_items(entity: str, response: dict) -> list[Item]`: the `todo.get_items` response (`{"<entity>": {"items": [...]}}`), open items only (`status == "needs_action"`), `due` as date or datetime (a date means that day at 00:00 local), at most 200 items read.
  - `def pick_reminders(items: list[Item], now: datetime, limit: int = 5) -> list[Item]`: items with a due time, due now or overdue first (oldest first), then due before the end of today; items without a due time are not reminders.
  - `def pick_todos(items: list[Item], limit: int = 5) -> tuple[list[Item], int]`: open items without a due time in list order, and how many more there are.
  - `def parse_events(response: dict, now: datetime, limit: int = 3) -> list[Event]`: the `calendar.get_events` response over several calendars, sorted by start, ended ones dropped.
  - `class Ha:` with `__init__(self, url: str, token: str, session: aiohttp.ClientSession)`, `async def todo_items(self, entity: str) -> dict | None`, `async def events(self, calendars: list[str], hours: int = 48) -> dict | None`, `async def complete(self, entity: str, uid: str) -> bool`. Each returns `None`/`False` on any HTTP error, timeout or bad JSON (with one log line, not a traceback), and does not retry itself.
- Consumes: Task 1's verdicts (the URL and that the token is accepted).

- [ ] **Step 1: Write the failing tests** in `test_lists.py`, with names and assertions: `test_open_items_only` (a `completed` item is dropped); `test_date_only_due_is_due_that_day` (`"2026-10-21"` with `now` 09:00 on the 21st is a reminder, overdue); `test_reminders_overdue_first_then_later_today` (order by due, one due tomorrow excluded, one without due excluded); `test_todos_are_the_undated_and_count_the_rest` (7 undated items give 5 and `2`); `test_events_sorted_and_ended_dropped`; `test_clean_makes_one_short_plain_line` (a 10 000-character title with newlines and `**x**` comes back at most 60 characters, one line, no `*`); `test_500_items_are_capped`; `test_midnight_and_dst` (a due of `2026-03-29T02:30` Europe/London, which does not exist, does not raise); `test_ha_errors_give_none` (a stub session answering 403, a timeout, and invalid JSON: each returns `None` and does not raise).
- [ ] **Step 2: Run** `nix build .#checks.x86_64-linux.techo5-bridge` (or `pytest pkgs/techo5-bridge/tests/test_lists.py` in `nix develop`). Expected: FAIL (module missing).
- [ ] **Step 3: Implement the functions and `Ha`** in `lists.py` to the signatures above. `Ha` calls `POST /api/services/todo/get_items?return_response`, `calendar/get_events?return_response`, `todo/update_item` with `{"entity_id", "item": uid, "status": "completed"}`.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "techo5-bridge: read a person's lists and calendars"`.

---

### Task 4: The bridge hands items to the router

**Files:**
- Modify: `pkgs/techo5-bridge/techo5_bridge/brain.py` (`Profile`, `Brain`)
- Modify: `pkgs/techo5-bridge/techo5_bridge/__main__.py` (`Echo`)
- Test: `pkgs/techo5-bridge/tests/test_brain.py`

**Interfaces:**
- Consumes: `lists.py` from Task 3; the profile JSON of Task 2.
- Produces:
  - `Profile` gains `todo: str = ""` and `reminders: str = ""`; `Profile.from_json` reads them.
  - `Brain.wants_items(self) -> tuple[str | None, Profile]`: the lone known person (`Brain.profile()`'s person) or `None` for the household.
  - `Brain.items(self, now: float, data: dict | None) -> list`: with `data = {"reminders": [Item], "todos": [Item], "more": int, "events": [Event]}` returns `[("router", {"items": {...}})]`; with `None` (nothing to show) returns a router message with empty lists. The message is `{"items": {"reminders": [{"uid", "title", "due", "list"}], "todos": [{"title"}], "more": int, "events": [{"title", "start", "all_day"}]}}`, titles already cleaned.
  - New output `("fetch", person_or_None, profile)` from `Brain.tick` every `ITEMS_REFRESH_S = 60.0` while the room's screen has a view to show, and at once when who is in the room changes. `Echo._carry` answers it by calling `Ha`, then `Brain.items`.
  - When `Brain.here` no longer names exactly one known person, `Brain.router` also returns `("router", {"items": EMPTY})` at once.

- [ ] **Step 1: Write the failing tests** in `test_brain.py`: `test_one_known_person_gets_a_fetch_at_once` (`here=[alex]` yields `("fetch", "alex", profile)`); `test_two_people_get_no_items_and_the_sensors_are_cleared` (after alex was alone, a second person arrives: the output contains the router message with empty `reminders`, `todos`, `events` and no `fetch` for a person); `test_leaving_clears_items`; `test_items_are_refreshed_every_minute_while_alone`; `test_items_message_shape` (exact dict with one reminder, one todo, `more == 2`, one event); `test_household_fetches_events_only` (the fetch for `None` carries the household's calendars and no lists); `test_a_failed_read_still_sends_events` (data with empty lists and events is still sent; `None` from `Ha` leaves the previous message unchanged and sends nothing).
- [ ] **Step 2: Run to see them fail.**
- [ ] **Step 3: Implement** `Profile` fields, `wants_items`, `items`, the refresh timer in `tick`, and the clearing in `router`. In `Echo._carry`, handle `"fetch"`: `Ha.todo_items(profile.reminders)` and `Ha.todo_items(profile.todo)` (skipped for the household), `Ha.events(profile.calendars or household calendars)`, `pick_*`, then `await self.carry(self.brain.items(now, data))`. Build one `aiohttp.ClientSession` and one `Ha` per bridge from `HA_TOKEN_FILE` and `ha_url`; with no token, no fetch is ever issued.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "techo5-bridge: items for the one person in the room"`.

---

### Task 5: The router publishes the room's items

**Files:**
- Modify: `pkgs/assistant-router/assistant_router/body.py` (accept the `items` message from a `screen` body)
- Modify: `pkgs/assistant-router/assistant_router/mqtt.py`
- Test: `pkgs/assistant-router/tests/test_mqtt.py`, `pkgs/assistant-router/tests/test_body.py`

**Interfaces:**
- Consumes: the `items` message of Task 4.
- Produces, in `mqtt.py`:
  - `def items_discovery(room: str) -> list[tuple[str, dict]]`: discovery configs for `sensor.lanbat_screen_<slug(room)>_reminder_{1..5}` (attributes `uid`, `list`, `due`), `..._todo_{1..5}`, `..._todo_more` (the count), `..._event_{1..3}` (state `"10:00 Standup"`), under a device named `<room> screen`.
  - `def items_state(items: dict | None) -> dict[str, tuple[str, dict]]`: object id → (state, attributes). An empty slot's state is the string `none`.
  - `Bodies` keeps each screen body's last `items`, and `HomeAssistant.tick` publishes changes only; a body that disconnects counts as having sent empty items.
  - At start, `HomeAssistant` publishes `none` (retained) to every slot of every room that has had a screen body in `lanbat.deployment.techo5Devices`, before anything else, so retained items from a previous run are gone.
- `body.py` ignores an `items` message from a body that is not a `screen`, and one whose `here` does not name exactly one person.

- [ ] **Step 1: Write the failing tests:** `test_items_become_slot_sensors` (one reminder, two todos, one event: states and attributes as above, other slots `none`); `test_two_people_in_the_room_publish_nothing_private` (the router has two people placed in the room; an `items` message is published as all `none`); `test_disconnect_clears`; `test_start_clears_retained_slots` (the first publishes are all `none` and retained); `test_items_from_a_robot_are_ignored`; `test_unchanged_items_are_not_republished`.
- [ ] **Step 2: Run** `nix build .#checks.x86_64-linux.assistant-router`. Expected: FAIL.
- [ ] **Step 3: Implement** to the signatures above, following `people_discovery` and `HomeAssistant.tick` for the publishing pattern.
- [ ] **Step 4: Run the tests.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "assistant-router: publish the room's reminders, to-dos and events while one person is there"`.

---

### Task 6: Home Assistant: the completing script and the lists

**Files:**
- Modify: `services/home-assistant.nix` (the `script` set, next to `lanbat_notify_person`)
- Modify: `pkgs/home-assistant-post-setup/setup-ha.sh` (a `local_todo` entry per list)
- Test: `tests/ha-dashboards.nix` or a new case beside it that evaluates the script; the post-setup check that exists for `ensure_techo5`

**Interfaces:**
- Produces: `script.lanbat_complete_reminder` with fields `room` (string, the room's name) and `slot` (1–5). It reads `state_attr('sensor.lanbat_screen_<slug>_reminder_<slot>', 'uid')` and `'list'` and, when both are set, calls `todo.update_item` with `{item: uid, status: completed}` on that list; with either missing it does nothing and does not error. `mode: single`.
- `setup-ha.sh` adds, for every person with a profile on any techo5 device, `todo.<key>_todo` and `todo.<key>_reminders` via `add_entry local_todo`, unless an entry with that title exists (so a second run adds nothing); a profile naming a list of its own is left alone.

- [ ] **Step 1: Write the failing check:** the generated script YAML contains `todo.update_item`, guards both attributes, and `mode: single`; the post-setup reconciliation, given two profiles, wants four `local_todo` entries and none on a second run (follow the existing `techo5_reconciled` test).
- [ ] **Step 2: Run it.** Expected: FAIL.
- [ ] **Step 3: Implement** the script and an `ensure_todo_lists` function in `setup-ha.sh`, called after `ensure_techo5`.
- [ ] **Step 4: Run the check.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Home Assistant: complete a reminder from the screen, and a to-do list per person"`.

---

### Task 7: The dashboard

**Files:**
- Modify: `pkgs/home-assistant-dashboards/generate.py` (`screen_sections`, `screen_dashboard`)
- Test: `tests/ha-dashboards.py`

**Interfaces:**
- Consumes: the sensor names of Task 5 and `script.lanbat_complete_reminder` of Task 6; `person_slug(room)` names the room's slug.
- Produces: `screen_sections(home, room, people, person)` returns, for a person, the sections in this order, each a self-contained `grid` section: **Reminders** (heading "Reminders" and up to 5 tiles, each `tap_action` `perform-action` on `script.lanbat_complete_reminder` with `data: {room, slot}`; the whole section visible only when `sensor..._reminder_1` is not `none`/`unavailable`/`unknown`, and each tile on its own sensor); **Next up** (3 event tiles, each hidden when its sensor is `none`/`unavailable`/`unknown`); **To-do** (heading, up to 5 tiles and a "+N more" tile hidden when `_todo_more` is `0`); **Home** (weather, lights-off button, the room's cards, and the other people's "where" tiles from `where_sensors(home, people, but=person)`, which replace the separate "Who's where" section; section visibility drops the section below a screen width of 600). For `person is None` (the household): Next up, the weather, the room and home status, and no Reminders or To-do section at all.
- Visibility uses `condition: state` with `state_not: ["none", "unavailable", "unknown"]`, never a template.

- [ ] **Step 1: Write the failing tests:** `test_person_view_section_order` (Reminders, Next up, To-do, Home); `test_reminder_tiles_complete_through_the_script` (the tap action and its data for slot 2); `test_reminders_hide_with_no_reminder` (the section's visibility is the `state_not` list on slot 1); `test_household_view_has_no_personal_section`; `test_no_separate_who_is_where_section_for_a_person` (the other people's tiles are in Home); `test_home_hides_on_a_narrow_screen`; `test_no_templates_in_screen_views` (walk the dict: no string contains `{{`); `test_room_names_slug_like_the_router` (`"Bedroom 1"` gives `bedroom_1`).
- [ ] **Step 2: Run** `nix build .#checks.x86_64-linux.ha-dashboards`. Expected: FAIL.
- [ ] **Step 3: Implement** the sections to the shapes above, reusing `tile`, `heading`/`subheading` and `room_cards`.
- [ ] **Step 4: Run the test.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Dashboard: a person's screen shows reminders, events, to-dos and the home"`.

---

### Task 8: Follow-me turns off every light in an empty room

**Files:**
- Modify: `modules/server/follow-me.nix` (options), `lib/follow-me.nix` (automation), `plugins/follow-me/default.nix` if it lists options
- Test: `tests/follow-me-templates.py`, `tests/follow-me.nix`

**Interfaces:**
- Produces: options `lightsOffAnyMinutes` (`seconds`-typed like its neighbours, default `10`) and `lightsOffAny` (`bool`, default `false`). `lib/follow-me.nix` gains `lightsOffAny cfg room rc`, id `followme_lights_off_any_<slug room>`, listed beside `lightsOff` for every room with lights, only when `cfg.lightsOffAny`: trigger `binary_sensor.followme_<r>_occupied` `on` to `off` for `cfg.lightsOffAnyMinutes` minutes; conditions `notPaused`, `notGuests`, and a template true when some light of `rc.lights` is on; action `light.turn_off` on `rc.lights` through `act`, so a dry run only logs "would turn off the <room> lights (nobody there, switched by hand)".

- [ ] **Step 1: Write the failing tests:** `test_lights_off_any_is_absent_by_default`; `test_lights_off_any_triggers_after_ten_minutes_empty`; `test_lights_off_any_respects_pause_guests_and_dry_run`; `test_lights_off_any_only_touches_the_rooms_lights` (the Echo Show's `light.*` is not in the targets); `test_unavailable_presence_does_not_turn_lights_off` (the trigger is on the occupied sensor's `on`→`off` only, which `unavailable` never produces).
- [ ] **Step 2: Run** `nix build .#checks.x86_64-linux.follow-me`. Expected: FAIL.
- [ ] **Step 3: Implement** the options and the automation as above.
- [ ] **Step 4: Run the test.** Expected: PASS.
- [ ] **Step 5: Commit** `git commit -am "Follow-me: optionally turn off hand-switched lights in an empty room"`.

---

### Task 9: Docs and the walk-through

**Files:**
- Modify: `docs/techo5.md` (what the person's screen shows; the lists; the item sensors; the privacy rule), `docs/people.md` (one paragraph under "In Home Assistant"), the follow-me docs section where its rules are listed (`lightsOffAny`), `docs/failure-modes.md` (home Assistant down: events and home status only)
- Modify: `deployments/example/deploy.nix`, `deployments/homelab/deploy.nix.example` (`lightsOffAny`)

- [ ] **Step 1: Write the docs** to match what Tasks 2–8 built. State plainly that the lists are ordinary Home Assistant entities any household member can open, and that the screen shows them only to the person recognised alone.
- [ ] **Step 2: Run the whole check set**

```bash
nix fmt
nix build .#checks.x86_64-linux.{service-settings,techo5-bridge,assistant-router,ha-dashboards,follow-me,assertions,settings-guard,validate-deploy}
nix eval --raw .#nixosConfigurations.example-server.config.system.build.toplevel.drvPath
```

Expected: every build succeeds, the eval prints a path.

- [ ] **Step 3: Walk-through on the kitchen Show** after merging and deploying (by hand, from `origin/master`): follow-me stays in dry run for the first day; add a reminder due now to `todo.<key>_reminders` and a to-do; walk up alone: the greeting, then the reminder card, events, to-dos; tap the reminder: it disappears and the item is completed in Home Assistant; a second person walks up: the personal sections go; walk away: after 5 minutes the screen goes off; read the logbook for "Follow-me would turn off the lights" lines. Record what the screen looked like (and any layout problem on the 1280×800 panel) in the spec's "Spike results".
- [ ] **Step 4: Commit** `git commit -am "Docs: the personalised screen and follow-me's light rule"`.
