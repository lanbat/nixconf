# Home Assistant dashboards

Home Assistant's dashboards are generated from what is connected to it. On every run,
`home-assistant-post-setup` reads Home Assistant's device, entity and area registries and
rewrites the dashboards below (`pkgs/home-assistant-dashboards`). A device paired today
appears after the next deploy, or overnight: a timer reruns post-setup at 03:30 and
restarts Home Assistant only when something changed.

Nothing about a particular home is in the repository. Entities are sorted by domain,
device class, unit and integration, with a few words in their ids:

- a light is a `light.*`, or a `switch.*` whose id or device mentions a light, lamp,
  spot or bulb;
- a plug is any other switch on a device that measures power;
- water is a switch or valve called valve, tap, irrigation, sprinkler or water;
- a garden is an area called garden, outside, yard, patio, balcony or terrace.

## What you get

**Overview** (the default dashboard), as tabs:

| Tab | Shows |
|---|---|
| Home | A greeting with the time, the lights on, what's playing and the weather; the forecast; "All lights off". Then one section per room, with its temperature and humidity as badges, a room light switch, dimmable bulbs, light switches, plugs, sensors with a 24-hour trend line, the room's TV and music while they play, and its voice satellite. Devices in no room come last. |
| Media | A media control per player, by room, and each voice satellite with its volume. |
| Climate | Every thermometer and hygrometer by room, 48-hour history and 30-day minimum, mean and maximum. |
| Garden | Hourly and daily forecast, soil moisture gauges and their week, the water valve with today's use and the last 30 days. |
| Energy | Total power now, each plug with a trend line, 24-hour power and energy per day. Each plug is also added to Home Assistant's own Energy dashboard as an individual device. |

**Cameras**: each camera live, with badges for what is in view now (person, car, dog…),
its Frigate switches, the last snapshot of each kind of object, the Frigate zones with
their counts, a log of the last day's detections, and a link to Frigate.

**System** (administrators only): the Zigbee bridge and devices with a weak signal,
updates waiting, low batteries, backups, the server's processor, memory, disk and load
when the System Monitor integration is set up, the voice satellites and engines with a log of the voice automations,
and links to Grafana, Frigate, Music Assistant, Zigbee2MQTT and Homepage.

## Rooms

The dashboards follow Home Assistant's areas. Give a device without one a room in the
profile, by the name Home Assistant shows for it, or by one of its entity ids when names
repeat (several wall switches all called "light"):

```nix
# deployments/<profile>/deploy.nix, in the server's modules
{
  lanbat.services.home-assistant.settings.deviceAreas = {
    livingroom_lamp = "Living Room";
    "switch.0x00124b0012345678" = "Hall";
  };
}
```

A room that doesn't exist yet is created. Names that match nothing are logged by
`home-assistant-post-setup`. Rooms you set in Home Assistant itself stay, unless the
profile names the device.

## A room's screen

Each room with an Echo Show on TECHO5 (`deployment.techo5Devices`, [techo5.md](techo5.md))
gets `lanbat-screen-<room>` ("Kitchen screen", out of the sidebar), drawn by the device
itself, so it uses only cards TECHO5 draws: headings, tiles, buttons. Its views:

- **home**: the room's devices, the weather, a lights-off button while any are on, and
  where everyone is ("Where is <name>");
- **one per person** (the person's key): the same, greeting them, with where the others
  are.

The device's bridge turns to a person's view while the assistant router places them alone
in the room, and back to home otherwise. The clock, calendar, timers and music are the
device's own.

## Editing

The Overview, Cameras and System dashboards belong to the generator: changes made to
them in Home Assistant are replaced on the next run. Make your own dashboards next to
them (Settings → Dashboards); those, and the Map, are never touched. Of the Energy
dashboard, only the list of individual devices is written; your grid, solar and
gas sources stay.

To regenerate now: `systemctl restart home-assistant-post-setup` on the server.
