// The robot's microphone as a second ear for voice-id (docs/people.md).
//
// Off except between the Pi's {"record": true} (the wake word) and
// {"record": false} (the end of the sentence), 15 s at most: then it captures
// 16 kHz mono into a ring in PSRAM, which the loop drains to the Pi in
// 100 ms chunks. Nothing is kept on the robot. (The CoreS3's two microphones
// behave as one point, so they cannot tell where a voice comes from;
// one channel is all this needs.)
#pragma once

#include <Arduino.h>

namespace ear {

constexpr uint32_t kRate = 16000;

// Whether the microphone works (it is switched off again afterwards).
bool begin();
void start();
void stop();
bool recording();
// Samples captured since the last call, up to `max`.
size_t take(int16_t* out, size_t max);
size_t available();

}  // namespace ear
