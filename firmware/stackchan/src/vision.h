// Faces seen through the CoreS3's camera, detected on the robot (esp-dl).
// Nothing leaves this file but a face's position and, on request, one frame
// as a JPEG for the server's face library (the robot keeps no pictures).
#pragma once

#include <Arduino.h>

struct Sighting {
  bool face = false;    // a face in the latest frame
  float x = 0;          // its centre, -1 (robot's left) .. 1 (robot's right)
  float y = 0;          // -1 (down) .. 1 (up)
  float size = 0;       // box width as a share of the frame
  uint8_t faces = 0;    // faces in the frame (the Pi only learns its turns from one)
  uint32_t at = 0;      // millis() of the frame
};

namespace vision {

// Starts the camera and the detector on core 0. False when there is no
// camera: the robot works without one, it just doesn't look at people.
bool begin();

// The latest sighting (copied under a lock).
Sighting latest();

// One fresh frame as a JPEG, for the server's face library: true and outSize
// set when it fit into out (the camera must be up; it works while the
// detector is paused, the sensor keeps streaming).
bool captureJpeg(uint8_t* out, uint32_t outLen, uint32_t* outSize);

// Paused while asleep: no frames, less heat and power.
void setPaused(bool paused);

// Time between frames: 150 ms normally, longer while napping (a face still
// wakes it, a little later).
void setFramePeriod(uint32_t ms);

// Frames the detector has looked at since boot (for the boot report).
uint32_t frameCount();

// Debug view: the camera image and the detector's boxes on the robot's
// screen (candidates yellow, faces green), in place of the face.
void setPreview(bool on);

// Frame size and detection counts since the last call, for the Pi's journal.
String stats();

}  // namespace vision
