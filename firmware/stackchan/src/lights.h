// The body's 12 RGB LEDs (6 left, 6 right), as one light that says what the
// assistant is doing at a glance, even from across the room.
#pragma once

#include <Arduino.h>

namespace lights {

enum class Scene {
  Off,
  Listening,  // cyan, breathing
  Thinking,   // violet, chasing round
  Speaking,   // white, following the mouth
  Error,      // three red flashes
  Greeting,   // a warm glow that fades
  Happy,      // a short pink sparkle
};

void setScene(Scene scene);
void setMouth(float level);  // 0..1, while Speaking
// Timer progress (share left, 0..1) and whether one is ringing; a negative
// share means no timer.
void setTimer(float left, bool ringing);
void setStatus(bool muted, bool online);
void setAsleep(bool asleep);
// A slow amber pulse on the first LED of each side: the battery is low.
void setLowBattery(bool low);
// A notification light from Home Assistant: the whole body in one colour
// while nothing else is showing (a conversation or a ringing timer wins; the
// mute and battery lights stay). Shown at night too: someone asked for it.
void setNotify(bool on, uint8_t r = 0, uint8_t g = 0, uint8_t b = 0);

// Call from loop(): renders at about 30 fps.
void update();

}  // namespace lights
