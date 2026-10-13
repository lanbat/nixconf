// The CoreS3's LTR-553 above the screen: a proximity sensor (an infrared
// LED and its echo) and an ambient light sensor (docs/stackchan.md).
//
// Read ten times a second on the internal I2C bus (0x23). A hand near the
// face is the echo rising well over its resting level (which follows slow
// changes, so a reflection off a wall settles in); a wave is a hand that
// comes and goes within 0.6 s. Light is lux at the sensor, by the datasheet's
// formula; behind the CoreS3's dark front glass that is a few lux in a lit
// room, so what it is good for is "brighter or darker than usual here".
#pragma once

#include <Arduino.h>

namespace senses {

struct Reading {
  bool ok = false;     // the sensor answered
  uint16_t prox = 0;   // raw echo, 0..2047
  float lux = 0;
  bool near = false;   // a hand (or a face) close to the screen
};

bool begin();
// From loop(); true when something changed worth telling: `near` flipped,
// a wave, or the light moved by more than a little.
bool update(Reading& now, bool& waved);
const Reading& latest();
// For the debug log: the last raw values.
void raw(uint16_t& ch0, uint16_t& ch1, uint8_t& status, uint16_t& prox, float& resting);

}  // namespace senses
