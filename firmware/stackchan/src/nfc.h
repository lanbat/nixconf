// NFC tags and cards on the robot (the StackChan body's ST25R3916 reader,
// docs/stackchan.md).
//
// Polls for NFC-A tags every 300 ms while the robot is awake (once a second
// while it naps or sleeps, so a tap still wakes it) and reports a
// tag's UID once per tap: a tag that stays on the reader is not reported
// again, and the same tag within 2 s is one tap. What a tag means is Home
// Assistant's (its tag scanner) or the router's (a person's card); the robot
// keeps nothing.
#pragma once

#include <Arduino.h>

namespace nfc {

bool begin();
// From loop(); the UID (upper-case hex) of a tag just tapped, or "".
String update(bool resting);

}  // namespace nfc
