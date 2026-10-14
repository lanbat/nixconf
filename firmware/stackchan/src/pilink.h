// The robot's line to its Pi: USB serial when it is plugged into the Pi,
// Wi-Fi otherwise (docs/stackchan.md).
//
// USB wins whenever the Pi talks on it (the bridge pings every 3 s). After
// 10 s without a USB line the robot joins Wi-Fi and dials the bridge over TLS,
// checking the bridge's certificate by its SHA-256 fingerprint and presenting
// a token in its hello. Once USB has been back for 10 s, Wi-Fi is switched
// off again. The Wi-Fi details (network, address, fingerprint, token) only
// ever come from the Pi over USB, and are kept in flash.
#pragma once

#include <Arduino.h>
#include <ArduinoJson.h>

namespace pilink {

enum class Via : uint8_t { None, Usb, Wifi };

void begin();
// Reads USB and switches Wi-Fi on and off; call from loop().
void update();
// The next whole line from either link; false when none is waiting.
bool next(String& line, Via& via);
// One line to the Pi on the active link, from any task; dropped without one.
void send(JsonDocument& doc);
Via active();
const char* name(Via via);
// The Wi-Fi signal in dBm while the link is Wi-Fi, else 0.
int rssi();
// The stored Wi-Fi details' id, "" when there are none (for the hello).
String configId();
// {"ssid", "password", "host", "port", "fingerprint", "token", "id"}, kept
// in flash; null forgets them. Only for lines that came over USB. False when
// malformed.
bool provision(JsonVariantConst wifi);

}  // namespace pilink

// The hello line, from main.cpp; the link adds the token when it dials.
void helloLine(JsonDocument& doc);
