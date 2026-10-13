// The robot as a Bluetooth room sensor: whose phone is near it
// (docs/people.md, "Phones in the room").
//
// The same recognition as the Pi's room-presence (pkgs/room-presence): an
// Android phone by the iBeacon UUID the Home Assistant app sends
// (people.<key>.bleBeacon), an iPhone by resolving its rotating address with
// its Identity Resolving Key (the room-presence-irks secret). The Pi hands
// both over USB only, and the robot keeps them in flash. A passive scan
// listens a slice of every second (Wi-Fi shares the radio); every 15 s the
// robot reports each person heard in the last minute with the median signal
// strength. Only person keys and strengths leave it.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

namespace phones {

void begin();
// From loop(): the report, every 15 s while anyone was heard.
void update();
// {"beacons": {"<32 hex>": "key"}, "irks": {"key": ["<32 hex>", ...]}, "id"}
// from the Pi over USB, kept in flash; null forgets them and stops scanning.
bool provision(JsonVariantConst config);
// The id of what it keeps, "" when nothing (for the hello).
String configId();
// Counts in the log every 5 s for `ms` ({"debug": "phones"}).
void debug(uint32_t ms);

// Whether a resolvable private address (most significant byte first)
// belongs to the device with `irk`: Bluetooth's ah(irk, prand) == hash.
bool resolves(const uint8_t irk[16], const uint8_t addressMsbFirst[6]);
// The Bluetooth specification's sample: false means AES or the byte order broke.
bool selfTest();

}  // namespace phones
