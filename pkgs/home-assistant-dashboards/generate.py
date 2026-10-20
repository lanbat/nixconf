#!/usr/bin/env python3
"""Generate Home Assistant dashboards from its device, entity and area registries.

Reads the registries in a Home Assistant .storage directory and writes, into
--out, the lovelace storage files of the dashboards it owns:

  lovelace.lovelace        the Overview (Home Assistant 2026.2 and later keep
                           the default dashboard here, listed as "lovelace"): Home (a section per room), Media,
                           Climate, Garden and Energy tabs
  lovelace.lanbat-cameras  the cameras, live, with what they have seen
  lovelace.lanbat-system   Zigbee, updates, batteries, backups, voice, the
                           server's health and links to the other services
  lovelace.lanbat-screen-<room>
                           for each Echo Show on TECHO5 (--screens): what its
                           room's screen shows, drawn by the device itself, with
                           a view for each person (--people) that its bridge
                           turns to while the assistant router places them
                           there alone (pkgs/techo5-bridge)

and two fragments for post-setup to merge into stores the user shares:

  dashboards.json          this generator's entries for lovelace_dashboards
  energy-devices.json      device_consumption for the Energy dashboard

Nothing about a particular home is written here: entities are sorted into
cards by domain, device class, unit, integration and a few words in their
ids (a switch called "*light*" is a light). The output is a pure function of
the registries, sorted, so post-setup can compare it with what is there and
only restart Home Assistant when it changed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

LIGHT_WORDS = re.compile(r"light|lamp|spot|bulb|lantern|chandelier", re.I)
GARDEN_WORDS = re.compile(r"garden|outside|outdoor|yard|patio|balcony|terrace|allotment", re.I)
WATER_WORDS = re.compile(r"valve|tap|irrigation|sprinkler|water", re.I)
# Integrations whose entities have a place of their own (or none) rather
# than in the rooms.
NOT_IN_ROOMS = {"frigate", "backup", "systemmonitor", "met", "google_translate", "wyoming",
                "shopping_list", "extended_openai_conversation", "openai_conversation",
                "radio_browser", "automation", "script", "sun"}
# Domains that never make a card of their own.
QUIET_DOMAINS = {"automation", "script", "button", "number", "select", "text", "event", "update",
                 "tts", "stt", "wake_word", "conversation", "todo", "image", "scene", "zone",
                 "device_tracker", "ai_task", "notify", "remote", "siren", "tag"}
AREA_ICONS = [
    (r"kitchen", "mdi:silverware-fork-knife"), (r"living|lounge|sitting", "mdi:sofa"),
    (r"bed", "mdi:bed"), (r"bath|shower|toilet|wc", "mdi:shower"), (r"office|study", "mdi:desk"),
    (r"hall|landing|stairs|corridor|entrance", "mdi:stairs"), (r"garden|yard|outside", "mdi:flower"),
    (r"garage", "mdi:garage"), (r"dining", "mdi:table-chair"), (r"kid|nursery", "mdi:teddy-bear"),
]
OBJECT_ICONS = {"person": "mdi:walk", "car": "mdi:car", "bicycle": "mdi:bicycle", "motorcycle": "mdi:motorbike",
                "bus": "mdi:bus", "dog": "mdi:dog", "cat": "mdi:cat", "bird": "mdi:bird", "all": "mdi:eye"}
OFF_STATES = ["off", "unavailable", "unknown", "idle", "standby"]


def load(storage: str, name: str, default):
    try:
        with open(os.path.join(storage, name), encoding="utf-8") as f:
            return json.load(f).get("data", default)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


class Home:
    def __init__(self, storage: str) -> None:
        areas = load(storage, "core.area_registry", {}).get("areas", [])
        devices = load(storage, "core.device_registry", {}).get("devices", [])
        entities = load(storage, "core.entity_registry", {}).get("entities", [])
        entries = load(storage, "core.config_entries", {}).get("entries", [])
        self.areas = sorted(({"id": a["id"], "name": a["name"], "icon": a.get("icon")} for a in areas),
                            key=lambda a: a["name"].lower())
        self.area_name = {a["id"]: a["name"] for a in self.areas}
        self.devices = {d["id"]: d for d in devices if not d.get("disabled_by")}
        self.entry_domain = {e["entry_id"]: e["domain"] for e in entries}
        self.all = []
        for e in entities:
            if e.get("disabled_by") or e.get("hidden_by"):
                continue
            device = self.devices.get(e.get("device_id") or "")
            area = e.get("area_id") or (device or {}).get("area_id")
            self.all.append({
                "id": e["entity_id"],
                "domain": e["entity_id"].split(".", 1)[0],
                "platform": e.get("platform", ""),
                "class": e.get("device_class") or e.get("original_device_class") or "",
                "unit": e.get("unit_of_measurement") or "",
                "category": e.get("entity_category"),
                "device": e.get("device_id") or "",
                "device_name": ((device or {}).get("name_by_user") or (device or {}).get("name") or ""),
                "area": area if area in self.area_name else "",
            })
        self.all.sort(key=lambda e: e["id"])
        self.primary = [e for e in self.all if not e["category"]]
        self.by_device: dict[str, list[dict]] = {}
        for e in self.all:
            self.by_device.setdefault(e["device"], []).append(e)

    # ── classification ──────────────────────────────────────────────────────
    def device_has(self, e: dict, domain: str, cls: str) -> dict | None:
        for o in self.by_device.get(e["device"], []) if e["device"] else []:
            if o["domain"] == domain and o["class"] == cls and not o["category"]:
                return o
        return None

    def is_indicator(self, e: dict) -> bool:
        """A device's own light, not the room's: an Echo Show's screen (TECHO5,
        ESPHome "screen") or a robot's LED (the assistant router's). "Lights
        off" must not turn off the screen it was tapped on."""
        return ((e["platform"] == "esphome" and e["id"].endswith("_screen"))
                or e["id"].startswith("light.lanbat_stackchan_"))

    def is_light(self, e: dict) -> bool:
        if self.is_indicator(e):
            return False
        if e["domain"] == "light":
            return True
        return e["domain"] == "switch" and bool(LIGHT_WORDS.search(e["id"]) or LIGHT_WORDS.search(e["device_name"]))

    def is_water(self, e: dict) -> bool:
        return e["domain"] in ("switch", "valve") and bool(WATER_WORDS.search(e["id"]))

    def lights(self) -> list[str]:
        return [e["id"] for e in self.primary if self.is_light(e)]

    def sensors(self, cls: str) -> list[dict]:
        return [e for e in self.primary if e["domain"] == "sensor" and e["class"] == cls
                and e["platform"] not in ("frigate", "systemmonitor")]

    def media_players(self) -> list[dict]:
        """Players people listen to or watch: not a satellite's own reply player."""
        out = []
        for e in self.primary:
            if e["domain"] != "media_player":
                continue
            if any(o["domain"] == "assist_satellite" for o in self.by_device.get(e["device"], [])):
                continue
            out.append(e)
        return out

    def satellites(self) -> list[dict]:
        return [e for e in self.primary if e["domain"] == "assist_satellite"]

    def area_of(self, entity_id: str) -> str:
        for e in self.all:
            if e["id"] == entity_id:
                return e["area"]
        return ""

    def garden_areas(self) -> list[str]:
        return [a["id"] for a in self.areas if GARDEN_WORDS.search(a["name"])]


# ── cards ───────────────────────────────────────────────────────────────────
def heading(text: str, icon: str | None = None, badges: list | None = None) -> dict:
    card = {"type": "heading", "heading": text, "heading_style": "title"}
    if icon:
        card["icon"] = icon
    if badges:
        card["badges"] = badges
    return card


def subheading(text: str, icon: str | None = None) -> dict:
    card = {"type": "heading", "heading": text, "heading_style": "subtitle"}
    if icon:
        card["icon"] = icon
    return card


def tile(entity: str, **extra) -> dict:
    card = {"type": "tile", "entity": entity}
    card.update(extra)
    return card


def shown_unless(entity: str, states: list[str]) -> list[dict]:
    return [{"condition": "state", "entity": entity, "state_not": states}]


def link_button(name: str, url: str, icon: str) -> dict:
    return {"type": "button", "name": name, "icon": icon, "show_state": False,
            "tap_action": {"action": "url", "url_path": url}}


def area_icon(area: dict) -> str:
    if area.get("icon"):
        return area["icon"]
    for pattern, icon in AREA_ICONS:
        if re.search(pattern, area["name"], re.I):
            return icon
    return "mdi:home-outline"


def climate_tile(e: dict) -> dict:
    return tile(e["id"], features=[{"type": "trend-graph", "hours_to_show": 24}],
                features_position="inline")


def room_cards(home: Home, area: str) -> list[dict]:
    """The cards of one room (area "" is everything without one)."""
    cards: list[dict] = []
    # A camera shows in its room whatever its integration (Frigate's other
    # entities have the Cameras dashboard).
    mine = [e for e in home.primary if e["area"] == area and e["domain"] not in QUIET_DOMAINS
            and (e["platform"] not in NOT_IN_ROOMS or e["domain"] == "camera")]
    lights = [e for e in mine if home.is_light(e)]
    for e in lights:
        if e["domain"] == "light":
            cards.append(tile(e["id"], features=[{"type": "light-brightness"}]))
        else:
            cards.append(tile(e["id"], tap_action={"action": "toggle"}, icon="mdi:lightbulb"))
    for e in mine:
        if e in lights:
            continue
        if e["domain"] in ("switch", "valve"):
            extra = {"tap_action": {"action": "toggle"}}
            if home.is_water(e):
                extra["icon"] = "mdi:water-pump"
            elif home.device_has(e, "sensor", "power"):
                extra["icon"] = "mdi:power-socket-uk"
            cards.append(tile(e["id"], **extra))
        elif e["domain"] == "sensor" and e["class"] in ("temperature", "humidity"):
            cards.append(climate_tile(e))
        elif e["domain"] == "sensor" and e["class"] in ("moisture", "illuminance", "co2", "pm25", "carbon_dioxide"):
            cards.append(tile(e["id"]))
        elif e["domain"] == "binary_sensor" and e["class"] in ("door", "window", "motion", "occupancy", "opening",
                                                              "moisture", "smoke", "gas"):
            cards.append(tile(e["id"]))
        elif e["domain"] in ("cover", "climate", "fan", "lock", "vacuum", "humidifier", "water_heater"):
            cards.append(tile(e["id"]))
        elif e["domain"] == "media_player" and e in home.media_players():
            cards.append(tile(e["id"], features=[{"type": "media-player-playback"}],
                              visibility=shown_unless(e["id"], OFF_STATES)))
        elif e["domain"] == "assist_satellite":
            cards.append(tile(e["id"], icon="mdi:microphone-message"))
        elif e["domain"] == "camera":
            cards.append({"type": "picture-entity", "entity": e["id"], "camera_view": "live",
                          "show_state": False, "show_name": False})
    return cards


def all_off_action(entities: list[str], confirm: bool = True) -> dict:
    """Lights and light switches off together: homeassistant.turn_off takes
    any domain. A screen's dashboard goes without the confirmation: TECHO5
    can't ask first, so it doesn't do a tap that would need it."""
    action = {"action": "perform-action", "perform_action": "homeassistant.turn_off",
              "target": {"entity_id": entities}}
    if confirm:
        action["confirmation"] = {"text": "Turn off these lights?"}
    return action


GREETING = """\
## {% set h = now().hour %}{{ 'Good morning' if h < 12 else ('Good afternoon' if h < 18 else 'Good evening') }}{{ ', ' ~ user if user else '' }}
{%- set lights = LIGHTS -%}
{%- set on = lights | select('is_state', 'on') | list -%}
{%- set playing = states.media_player | selectattr('state', 'eq', 'playing') | map(attribute='name') | list %}

**{{ now().strftime('%A %-d %B, %H:%M') }}** ·
{{ on | count }} light{{ '' if on | count == 1 else 's' }} on
{%- if playing %} · playing in {{ playing | join(', ') }}{% endif %}
{%- if WEATHER and states(WEATHER) not in ['unknown', 'unavailable'] %} ·
{{ state_attr(WEATHER, 'temperature') }}{{ state_attr(WEATHER, 'temperature_unit') }} and {{ states(WEATHER) | replace('-', ' ') | replace('partlycloudy', 'partly cloudy') }} outside{% endif %}
"""


def home_view(home: Home) -> dict:
    lights = home.lights()
    weather = next((e["id"] for e in home.primary if e["domain"] == "weather"), "")
    top: list[dict] = [
        {"type": "markdown", "text_only": True,
         "content": GREETING.replace("LIGHTS", json.dumps(lights)).replace("WEATHER", json.dumps(weather))},
    ]
    if weather:
        top.append({"type": "weather-forecast", "entity": weather, "forecast_type": "daily",
                    "show_current": True, "show_forecast": True})
    if lights:
        top.append({"type": "button", "name": "All lights off", "icon": "mdi:lightbulb-group-off",
                    "show_state": False, "tap_action": all_off_action(lights),
                    "hold_action": {"action": "none"}})
    sections = [{"type": "grid", "cards": [heading("Home", "mdi:home-heart")] + top}]
    for area in home.areas:
        cards = room_cards(home, area["id"])
        if not cards:
            continue
        room_lights = [e for e in lights if home.area_of(e) == area["id"]]
        badges = []
        temp = next((e["id"] for e in home.sensors("temperature") if e["area"] == area["id"]), None)
        hum = next((e["id"] for e in home.sensors("humidity") if e["area"] == area["id"]), None)
        for s in (temp, hum):
            if s:
                badges.append({"type": "entity", "entity": s, "show_icon": True, "show_state": True})
        head = heading(area["name"], area_icon(area), badges)
        if room_lights:
            head["badges"] = badges + [{
                "type": "entity", "entity": room_lights[0], "icon": "mdi:lightbulb-group-off",
                "show_state": False, "tap_action": all_off_action(room_lights),
                "visibility": [{"condition": "or", "conditions": [
                    {"condition": "state", "entity": l, "state": "on"} for l in room_lights]}]}]
        area_card = {"type": "area", "area": area["id"], "display_type": "compact",
                     "features": [{"type": "area-controls"}], "features_position": "inline"}
        sections.append({"type": "grid", "cards": [head, area_card] + cards})
    rest = room_cards(home, "")
    if rest:
        sections.append({"type": "grid", "cards": [heading("Not in a room yet", "mdi:help-box-outline")] + rest})
    persons = [e["id"] for e in home.primary if e["domain"] == "person"]
    badges = [{"type": "entity", "entity": p, "show_name": True} for p in persons]
    return {"title": "Home", "path": "home", "icon": "mdi:home", "type": "sections",
            "max_columns": 4, "badges": badges, "sections": sections}


def media_view(home: Home) -> dict | None:
    players = home.media_players()
    satellites = home.satellites()
    if not players and not satellites:
        return None
    sections = []
    by_area: dict[str, list[dict]] = {}
    for p in players:
        by_area.setdefault(p["area"], []).append(p)
    for area in [a["id"] for a in home.areas] + [""]:
        ps = by_area.get(area)
        if not ps:
            continue
        name = home.area_name.get(area, "Elsewhere")
        cards = [heading(name, "mdi:speaker")]
        for p in ps:
            cards.append({"type": "media-control", "entity": p["id"]})
        sections.append({"type": "grid", "cards": cards})
    if satellites:
        cards = [heading("Voice assistants", "mdi:account-voice")]
        for s in satellites:
            cards.append(tile(s["id"], icon="mdi:microphone-message"))
            vol = next((o for o in home.by_device.get(s["device"], []) if o["domain"] == "media_player"), None)
            if vol:
                cards.append(tile(vol["id"], features=[{"type": "media-player-volume-slider"}]))
        sections.append({"type": "grid", "cards": cards})
    return {"title": "Media", "path": "media", "icon": "mdi:play-circle", "type": "sections",
            "max_columns": 4, "sections": sections}


def climate_view(home: Home) -> dict | None:
    temps, hums = home.sensors("temperature"), home.sensors("humidity")
    if not temps and not hums:
        return None
    sections = []
    for area in [a["id"] for a in home.areas] + [""]:
        t = [e for e in temps if e["area"] == area]
        h = [e for e in hums if e["area"] == area]
        if not t and not h:
            continue
        cards = [heading(home.area_name.get(area, "Not in a room yet"),
                         area_icon({"name": home.area_name.get(area, ""), "icon": None}))]
        cards += [climate_tile(e) for e in t]
        cards += [{"type": "gauge", "entity": e["id"], "min": 0, "max": 100, "needle": True,
                   "severity": {"red": 75, "yellow": 60, "green": 35}} for e in h]
        sections.append({"type": "grid", "cards": cards})
    graphs = [heading("Trends", "mdi:chart-line")]
    if temps:
        graphs.append({"type": "history-graph", "title": "Temperature, last 48 hours", "hours_to_show": 48,
                       "entities": [e["id"] for e in temps]})
        graphs.append({"type": "statistics-graph", "title": "Temperature, last 30 days", "days_to_show": 30,
                       "period": "day", "chart_type": "line", "stat_types": ["min", "mean", "max"],
                       "entities": [e["id"] for e in temps]})
    if hums:
        graphs.append({"type": "history-graph", "title": "Humidity, last 48 hours", "hours_to_show": 48,
                       "entities": [e["id"] for e in hums]})
    sections.append({"type": "grid", "column_span": 2, "cards": graphs})
    return {"title": "Climate", "path": "climate", "icon": "mdi:thermometer", "type": "sections",
            "max_columns": 4, "sections": sections}


def garden_view(home: Home) -> dict | None:
    gardens = home.garden_areas()
    moisture = [e for e in home.sensors("moisture")]
    water = [e for e in home.primary if home.is_water(e)]
    weather = next((e["id"] for e in home.primary if e["domain"] == "weather"), None)
    if not moisture and not water and not gardens:
        return None
    sections = []
    if weather:
        sections.append({"type": "grid", "cards": [
            heading("Weather", "mdi:weather-partly-cloudy"),
            {"type": "weather-forecast", "entity": weather, "forecast_type": "hourly", "show_current": True},
            {"type": "weather-forecast", "entity": weather, "forecast_type": "daily", "show_current": False}]})
    if moisture:
        cards = [heading("Soil", "mdi:sprout")]
        for e in moisture:
            cards.append({"type": "gauge", "entity": e["id"], "min": 0, "max": 100, "needle": True,
                          "severity": {"red": 0, "yellow": 20, "green": 35}})
            for o in home.by_device.get(e["device"], []):
                if o["domain"] == "sensor" and o["class"] == "temperature" and not o["category"]:
                    cards.append(climate_tile(o))
        cards.append({"type": "history-graph", "title": "Soil moisture, last 7 days", "hours_to_show": 168,
                      "entities": [e["id"] for e in moisture]})
        sections.append({"type": "grid", "cards": cards})
    for valve in water:
        cards = [heading(valve["device_name"] or "Water", "mdi:water-pump"),
                 tile(valve["id"], tap_action={"action": "toggle"}, icon="mdi:water-pump")]
        litres = []
        for o in home.by_device.get(valve["device"], []):
            if o["domain"] == "sensor" and o["unit"] in ("L", "min", "m³", "gal"):
                if re.search(r"daily|real_time|today", o["id"]):
                    cards.append(tile(o["id"]))
                if o["unit"] == "L" and "daily" in o["id"]:
                    litres.append(o["id"])
        if litres:
            cards.append({"type": "history-graph", "title": "Water used, last 30 days", "hours_to_show": 720,
                          "entities": litres})
        sections.append({"type": "grid", "cards": cards})
    for area in gardens:
        other = [c for c in room_cards(home, area)
                 if c.get("entity") not in {e["id"] for e in moisture + water}]
        if other:
            sections.append({"type": "grid", "cards": [heading(home.area_name[area], "mdi:flower")] + other})
    return {"title": "Garden", "path": "garden", "icon": "mdi:flower", "type": "sections",
            "max_columns": 4, "sections": sections}


TOTAL_POWER = """\
{%- set ids = IDS -%}
{%- set w = ids | map('states') | map('float', 0) | sum -%}
## ⚡ {{ w | round(0) | int }} W
{{ ids | select('is_state', 'unavailable') | list | count }} of {{ ids | count }} meters unavailable
"""


def energy_view(home: Home) -> dict | None:
    power = home.sensors("power")
    energy = home.sensors("energy")
    if not power and not energy:
        return None
    sections = []
    cards = [heading("Right now", "mdi:flash"),
             {"type": "markdown", "text_only": True,
              "content": TOTAL_POWER.replace("IDS", json.dumps([e["id"] for e in power]))}]
    cards += [tile(e["id"], **({"name": e["device_name"]} if e["device_name"] else {}),
                   features=[{"type": "trend-graph", "hours_to_show": 24}], features_position="inline")
              for e in power]
    sections.append({"type": "grid", "cards": cards})
    graphs = [heading("Use", "mdi:chart-bar")]
    if power:
        graphs.append({"type": "history-graph", "title": "Power, last 24 hours", "hours_to_show": 24,
                       "entities": [e["id"] for e in power]})
    if energy:
        graphs.append({"type": "statistics-graph", "title": "Energy per day, last 30 days", "days_to_show": 30,
                       "period": "day", "chart_type": "bar", "stat_types": ["change"],
                       "entities": [e["id"] for e in energy]})
    graphs.append({"type": "button", "name": "Energy dashboard", "icon": "mdi:lightning-bolt-circle",
                   "show_state": False, "tap_action": {"action": "navigate", "navigation_path": "/energy"}})
    sections.append({"type": "grid", "column_span": 2, "cards": graphs})
    return {"title": "Energy", "path": "energy", "icon": "mdi:lightning-bolt", "type": "sections",
            "max_columns": 4, "sections": sections}


def cameras_view(home: Home, links: dict) -> dict:
    sections = []
    frigate_devices = {e["device"] for e in home.all if e["platform"] == "frigate" and e["device"]}
    cameras = [e for e in home.primary if e["domain"] == "camera"]
    camera_devices = {c["device"] for c in cameras}

    def occupancy(device: str) -> list[dict]:
        out = []
        for o in home.by_device.get(device, []):
            m = re.match(r"binary_sensor\..*_([a-z]+)_occupancy$", o["id"])
            if m and m.group(1) != "all":
                out.append({"type": "entity", "entity": o["id"], "icon": OBJECT_ICONS.get(m.group(1), "mdi:eye"),
                            "visibility": [{"condition": "state", "entity": o["id"], "state": "on"}]})
        return out

    for cam in cameras:
        cards = [heading(cam["device_name"] or cam["id"], "mdi:cctv", occupancy(cam["device"])),
                 {"type": "picture-entity", "entity": cam["id"], "camera_view": "live", "show_state": False,
                  "show_name": False}]
        toggles = [o for o in home.by_device.get(cam["device"], []) if o["domain"] == "switch"]
        if toggles:
            cards.append({"type": "glance", "show_state": False, "columns": max(1, min(len(toggles), 4)),
                          "entities": [{"entity": t["id"], "tap_action": {"action": "toggle"}} for t in toggles]})
        snaps = [o for o in home.by_device.get(cam["device"], []) if o["domain"] == "image"]
        if snaps:
            cards.append(subheading("Last seen", "mdi:image-multiple"))
            for s in snaps:
                cards.append({"type": "picture-entity", "entity": s["id"], "show_state": False,
                              "grid_options": {"columns": 6},
                              "visibility": shown_unless(s["id"], ["unknown", "unavailable"])})
        sections.append({"type": "grid", "column_span": 2, "cards": cards})
    zones = sorted(d for d in frigate_devices - camera_devices
                   if any(o["id"].endswith("_all_occupancy") for o in home.by_device.get(d, [])))
    if zones:
        cards = [heading("Zones", "mdi:map-marker-radius")]
        for d in zones:
            name = (home.devices.get(d) or {}).get("name") or d
            counts = [o for o in home.by_device.get(d, []) if re.search(r"_(person|car|dog|cat|bicycle)_active_count$", o["id"])]
            cards.append(subheading(name, "mdi:map-marker"))
            cards += [tile(o["id"], grid_options={"columns": 12}) for o in home.by_device.get(d, [])
                      if o["id"].endswith("_all_occupancy")]
            cards += [tile(o["id"], icon=OBJECT_ICONS.get(o["id"].split("_")[-3], "mdi:eye"),
                           grid_options={"columns": 4}) for o in counts]
        sections.append({"type": "grid", "cards": cards})
    if cameras:
        logbook = [o["id"] for c in cameras for o in home.by_device.get(c["device"], [])
                   if re.search(r"_(person|car)_occupancy$", o["id"])]
        cards = [heading("Recent detections", "mdi:history")]
        if logbook:
            cards.append({"type": "logbook", "hours_to_show": 24, "target": {"entity_id": logbook}})
        if "Frigate" in links:
            cards.append(link_button("Frigate", links["Frigate"], "mdi:cctv"))
        sections.append({"type": "grid", "cards": cards})
    if not sections:
        sections.append({"type": "grid", "cards": [heading("No cameras yet", "mdi:cctv-off")]})
    return {"title": "Cameras", "path": "cameras", "icon": "mdi:cctv", "type": "sections",
            "max_columns": 4, "sections": sections}


def system_view(home: Home, links: dict) -> dict:
    sections = []
    bridge = [d for d, dev in home.devices.items() if dev.get("manufacturer") == "Zigbee2MQTT"]
    if bridge:
        es = [o for o in home.by_device.get(bridge[0], [])
              if re.search(r"permit_join|connection_state|restart_required|_version$|log_level", o["id"])
              and o["domain"] in ("switch", "binary_sensor", "sensor")]
        cards = [heading("Zigbee", "mdi:zigbee")] + [tile(o["id"]) for o in es]
        lq = [e["id"] for e in home.all if e["id"].endswith("_linkquality") and not e.get("category") == "config"]
        if lq:
            cards.append({"type": "entity-filter", "entities": lq, "state_filter": [{"operator": "<", "value": 40}],
                          "card": {"type": "entities", "title": "Weak signal"}})
        sections.append({"type": "grid", "cards": cards})
    updates = [e["id"] for e in home.all if e["domain"] == "update"]
    batteries = [e["id"] for e in home.all if e["domain"] == "sensor" and e["class"] == "battery"]
    cards = [heading("Attention", "mdi:alert-circle-outline")]
    if updates:
        cards.append({"type": "entity-filter", "entities": updates, "state_filter": ["on"],
                      "card": {"type": "entities", "title": "Updates available"},
                      "show_empty": False})
    if batteries:
        cards.append({"type": "entity-filter", "entities": batteries,
                      "state_filter": [{"operator": "<", "value": 25}],
                      "card": {"type": "entities", "title": "Low batteries"}, "show_empty": False})
        cards.append({"type": "entities", "title": "Batteries", "entities": batteries})
    sections.append({"type": "grid", "cards": cards})
    backup = [e["id"] for e in home.all if e["platform"] == "backup" and e["domain"] == "sensor"]
    if backup:
        sections.append({"type": "grid", "cards": [heading("Backups", "mdi:backup-restore")] +
                         [tile(b) for b in backup]})
    sm = [e for e in home.all if e["platform"] == "systemmonitor"]
    if sm:
        cards = [heading("Server", "mdi:server")]
        for e in sm:
            if e["unit"] == "%":
                cards.append({"type": "gauge", "entity": e["id"], "min": 0, "max": 100, "needle": True,
                              "severity": {"green": 0, "yellow": 70, "red": 90}, "grid_options": {"columns": 4}})
            else:
                cards.append(tile(e["id"], grid_options={"columns": 6}))
        sections.append({"type": "grid", "cards": cards})
    sats = home.satellites()
    voice = [e for e in home.all if e["domain"] in ("stt", "tts", "wake_word", "conversation")]
    if sats or voice:
        cards = [heading("Voice", "mdi:account-voice")] + [tile(s["id"], icon="mdi:microphone-message") for s in sats]
        cards += [tile(v["id"]) for v in voice]
        autos = [e["id"] for e in home.all if e["domain"] == "automation"]
        if autos:
            cards.append({"type": "logbook", "hours_to_show": 24, "target": {"entity_id": autos}})
        sections.append({"type": "grid", "cards": cards})
    if links:
        cards = [heading("Services", "mdi:apps")]
        icons = {"Grafana": "mdi:chart-areaspline", "Frigate": "mdi:cctv", "Music Assistant": "mdi:music",
                 "Zigbee2MQTT": "mdi:zigbee", "Homepage": "mdi:view-dashboard", "Jellyfin": "mdi:filmstrip",
                 "Immich": "mdi:image-multiple", "Nextcloud": "mdi:cloud"}
        for name in sorted(links):
            card = link_button(name, links[name], icons.get(name, "mdi:open-in-new"))
            card["grid_options"] = {"columns": 4}
            cards.append(card)
        sections.append({"type": "grid", "cards": cards})
    return {"title": "System", "path": "system", "icon": "mdi:server-network", "type": "sections",
            "max_columns": 4, "sections": sections}


# ── a room's screen (an Echo Show on TECHO5) ───────────────────────────────
# Only cards the device draws itself (TECHO5 docs/dashboards.md): headings,
# tiles, pictures, and visibility. Its clock, calendar, timers and music are
# its own.
def person_slug(key: str) -> str:
    """As the assistant router names a person's entities (mqtt.slug)."""
    return re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_") or "room"


def where_sensors(home: Home, people: dict[str, str], but: str | None = None) -> list[str]:
    ids = {e["id"] for e in home.all}
    return [f"sensor.lanbat_people_where_{person_slug(k)}" for k in sorted(people)
            if k != but and f"sensor.lanbat_people_where_{person_slug(k)}" in ids]


def screen_sections(home: Home, room: str, people: dict[str, str], person: str | None) -> list[dict]:
    area = next((a for a in home.areas if a["name"].casefold() == room.casefold()), None)
    weather = next((e["id"] for e in home.primary if e["domain"] == "weather"), "")
    first = people.get(person, "").split()[0] if person and people.get(person, "").split() else ""
    top = [heading(f"Hi {first}" if first else room, "mdi:hand-wave" if first else
                   (area_icon(area) if area else "mdi:home-outline"))]
    if weather:
        top.append(tile(weather, grid_options={"columns": 12}))
    room_lights = [e for e in home.lights() if area and home.area_of(e) == area["id"]]
    if room_lights:
        top.append({"type": "button", "name": "Lights off", "icon": "mdi:lightbulb-group-off", "show_state": False,
                    "tap_action": all_off_action(room_lights, confirm=False), "hold_action": {"action": "none"},
                    "visibility": [{"condition": "or", "conditions": [
                        {"condition": "state", "entity": l, "state": "on"} for l in room_lights]}]})
    sections = [{"type": "grid", "cards": top}]
    cards = [c for c in (room_cards(home, area["id"]) if area else []) if c["type"] != "picture-entity"]
    if cards:
        sections.append({"type": "grid", "cards": [subheading(area["name"], area_icon(area))] + cards})
    others = where_sensors(home, people, but=person)
    if others:
        sections.append({"type": "grid", "cards": [subheading("Who's where", "mdi:account-group")]
                         + [tile(s) for s in others]})
    return sections


def screen_dashboard(home: Home, room: str, people: dict[str, str]) -> dict:
    views = [{"title": room, "path": "home", "icon": "mdi:home", "type": "sections", "max_columns": 2,
              "sections": screen_sections(home, room, people, None)}]
    for key in sorted(people):
        views.append({"title": people[key], "path": key, "icon": "mdi:account", "type": "sections",
                      "max_columns": 2, "sections": screen_sections(home, room, people, key)})
    return {"title": f"{room} screen", "views": views}


def store(key: str, config: dict) -> dict:
    return {"version": 1, "minor_version": 1, "key": key, "data": {"config": config}}


def generate(storage: str, links: dict, screens: list[dict] | None = None,
             people: dict[str, str] | None = None) -> dict[str, dict]:
    home = Home(storage)
    overview = [home_view(home)] + [v for v in (media_view(home), climate_view(home), garden_view(home),
                                                energy_view(home)) if v]
    out = {
        "lovelace.lovelace": store("lovelace.lovelace", {"title": "Home", "views": overview}),
        "lovelace.lanbat-cameras": store("lovelace.lanbat-cameras",
                                         {"title": "Cameras", "views": [cameras_view(home, links)]}),
        "lovelace.lanbat-system": store("lovelace.lanbat-system",
                                        {"title": "System", "views": [system_view(home, links)]}),
        "dashboards.json": [
            {"id": "lanbat-cameras", "url_path": "lanbat-cameras", "title": "Cameras", "icon": "mdi:cctv",
             "mode": "storage", "require_admin": False, "show_in_sidebar": True},
            {"id": "lanbat-system", "url_path": "lanbat-system", "title": "System", "icon": "mdi:server-network",
             "mode": "storage", "require_admin": True, "show_in_sidebar": True},
            {"id": "lovelace", "url_path": "lovelace", "title": "Overview", "icon": "mdi:view-dashboard",
             "mode": "storage", "require_admin": False, "show_in_sidebar": True},
        ],
        "energy-devices.json": energy_devices(home),
        "all-lights.json": home.lights(),
    }
    for screen in screens or []:
        path, room = screen["path"], screen["room"]
        out[f"lovelace.{path}"] = store(f"lovelace.{path}", screen_dashboard(home, room, people or {}))
        out["dashboards.json"].append({"id": path, "url_path": path, "title": f"{room} screen",
                                       "icon": "mdi:tablet-dashboard", "mode": "storage",
                                       "require_admin": False, "show_in_sidebar": False})
    return out


def energy_devices(home: Home) -> list[dict]:
    out = []
    for e in home.sensors("energy"):
        if e["unit"] not in ("kWh", "Wh", "MWh"):
            continue
        item = {"stat_consumption": e["id"], "name": e["device_name"] or e["id"]}
        rate = home.device_has(e, "sensor", "power")
        if rate:
            item["stat_rate"] = rate["id"]
        out.append(item)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--storage", required=True, help="Home Assistant's .storage directory")
    p.add_argument("--out", required=True, help="directory to write the generated files into")
    p.add_argument("--links", default="{}", help='JSON object {"Name": "https://..."} for the link buttons')
    p.add_argument("--screens", default="[]",
                   help='JSON list [{"room": "Kitchen", "path": "lanbat-screen-kitchen"}] of rooms with a screen')
    p.add_argument("--people", default="{}", help='JSON object {"key": "Name"} of the people (deployment.people)')
    args = p.parse_args()
    links = json.loads(args.links or "{}")
    os.makedirs(args.out, exist_ok=True)
    for name, content in generate(args.storage, links, json.loads(args.screens or "[]"),
                                  json.loads(args.people or "{}")).items():
        with open(os.path.join(args.out, name), "w", encoding="utf-8") as f:
            json.dump(content, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")


if __name__ == "__main__":
    sys.exit(main())
