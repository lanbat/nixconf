#include "lights.h"

#include <M5StackChan.h>

namespace {

constexpr int kCount = 12;
lights::Scene scene = lights::Scene::Off;
uint32_t sceneSince = 0;
float mouth = 0;
float timerLeft = -1;
bool timerRinging = false;
bool muted = false;
bool online = true;
bool asleep = false;
bool lowBattery = false;
uint32_t lastFrame = 0;

struct Rgb {
  uint8_t r, g, b;
};

Rgb pulseColour = {0, 0, 0};
uint32_t pulseMs = 0;

Rgb scale(Rgb c, float k) {
  k = constrain(k, 0.0f, 1.0f);
  return {(uint8_t)(c.r * k), (uint8_t)(c.g * k), (uint8_t)(c.b * k)};
}

// Breathing between 15 % and 100 % with the given period.
float breathe(uint32_t t, uint32_t period) {
  return 0.15f + 0.85f * (0.5f - 0.5f * cosf(2 * PI * (t % period) / (float)period));
}

}  // namespace

namespace lights {

void setScene(Scene s) {
  if (s != scene) sceneSince = millis();
  scene = s;
}
void setMouth(float level) { mouth = level; }
void setTimer(float left, bool ringing) {
  timerLeft = left;
  timerRinging = ringing;
}
void setStatus(bool m, bool o) {
  muted = m;
  online = o;
}
void setAsleep(bool a) { asleep = a; }
void setLowBattery(bool low) { lowBattery = low; }
bool notifyOn = false;
Rgb notifyColour = {0, 0, 0};
void setNotify(bool on, uint8_t r, uint8_t g, uint8_t b) {
  notifyOn = on;
  notifyColour = {r, g, b};
}
void pulse(uint8_t r, uint8_t g, uint8_t b, uint32_t ms) {
  pulseColour = {r, g, b};
  pulseMs = ms;
  sceneSince = millis();
  scene = Scene::Pulse;
}

void update() {
  uint32_t now = millis();
  if (now - lastFrame < 33) return;
  lastFrame = now;
  uint32_t t = now - sceneSince;

  Rgb frame[kCount] = {};
  switch (scene) {
    case Scene::Off:
      break;
    case Scene::Listening:
      for (auto& c : frame) c = scale({0, 170, 200}, breathe(t, 1600));
      break;
    case Scene::Thinking: {
      int head = (t / 70) % kCount;
      for (int i = 0; i < kCount; i++) {
        int d = (head - i + kCount) % kCount;
        frame[i] = scale({120, 40, 220}, d == 0 ? 1.0f : d == 1 ? 0.4f : d == 2 ? 0.12f : 0.0f);
      }
      break;
    }
    case Scene::Speaking:
      for (auto& c : frame) c = scale({200, 200, 190}, 0.1f + 0.9f * mouth);
      break;
    case Scene::Error:
      if (t < 900 && (t / 150) % 2 == 0)
        for (auto& c : frame) c = {200, 0, 0};
      else if (t >= 900)
        scene = Scene::Off;
      break;
    case Scene::Greeting:
      if (t < 1500)
        for (auto& c : frame) c = scale({255, 140, 40}, 1.0f - t / 1500.0f);
      else
        scene = Scene::Off;
      break;
    case Scene::Happy:
      if (t < 1200) {
        for (int i = 0; i < kCount; i++)
          frame[i] = ((i + t / 100) % 3 == 0) ? Rgb{255, 80, 160} : Rgb{0, 0, 0};
      } else {
        scene = Scene::Off;
      }
      break;
    case Scene::Pulse:
      // In and out once a second, from dark, then back to nothing.
      if (t < pulseMs)
        for (auto& c : frame) c = scale(pulseColour, 0.5f - 0.5f * cosf(2 * PI * (t % 1000) / 1000.0f));
      else
        scene = Scene::Off;
      break;
  }

  // A timer shows as amber progress from the bottom of both sides while
  // nothing else is going on, and flashes the whole body while it rings.
  if (timerRinging) {
    bool on = (now / 250) % 2 == 0;
    for (auto& c : frame) c = on ? Rgb{255, 120, 0} : Rgb{0, 0, 0};
  } else if (timerLeft >= 0 && scene == Scene::Off) {
    int lit = (int)ceilf(timerLeft * 6);
    for (int i = 0; i < 6; i++) {
      Rgb c = i < lit ? Rgb{120, 60, 0} : Rgb{0, 0, 0};
      frame[5 - i] = c;  // left side, bottom up
      frame[6 + i] = c;  // right side, bottom up
    }
  }

  // Home Assistant's notification light, under the status lights.
  if (notifyOn && !timerRinging && scene == Scene::Off) {
    for (auto& c : frame) c = notifyColour;
  }

  // The first LED of each side is a status light: dim red while the
  // microphone is muted, a slow red pulse while the Pi or Home Assistant is
  // out of reach.
  if (muted) {
    frame[0] = frame[6] = {60, 0, 0};
  } else if (!online && scene == Scene::Off) {
    frame[0] = frame[6] = scale({90, 0, 0}, breathe(now, 3000));
  }

  if (asleep && !timerRinging && !notifyOn && scene == Scene::Off) {
    for (auto& c : frame) c = {0, 0, 0};
  }
  if (lowBattery) {
    frame[0] = frame[6] = scale({200, 90, 0}, breathe(now, 2000));
  }

  // The LEDs hang off the body's IO expander on the shared I2C bus: write
  // only what changed.
  static Rgb shown[kCount] = {};
  static bool first = true;
  bool changed = first;
  for (int i = 0; i < kCount; i++) {
    if (first || frame[i].r != shown[i].r || frame[i].g != shown[i].g || frame[i].b != shown[i].b) {
      M5StackChan.setRgbColor(i, frame[i].r, frame[i].g, frame[i].b);
      shown[i] = frame[i];
      changed = true;
    }
  }
  if (changed) M5StackChan.refreshRgb();
  first = false;
}

}  // namespace lights
