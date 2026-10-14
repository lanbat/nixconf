// NFC on the robot (the StackChan body's ST25R3916, docs/stackchan.md): a
// tag for phones, and a reader for cards when asked.
//
// A tag: an NTAG213 holding one link (from the router, through the Pi). A
// phone held to the robot reads it and opens the link: the router's "who
// tapped" page, behind Authentik, which knows who the phone's owner is
// (docs/people.md).
//
// A reader, in a window someone asks for ("remember this card"), or always
// while it has no link: a card or tag tapped on it is reported by its UID
// once per tap. Phones and bank cards answer a reader with a new random UID
// each time (4 bytes, starting 08): they are not reported.
//
// The chip is one or the other, never both: taking turns lost the phone
// (nfc.cpp). The robot keeps nothing.
#pragma once

#include <Arduino.h>

namespace nfc {

struct Event {
  String uid;              // a card or tag tapped on it, or ""
  bool phoneRead = false;  // a phone just read its link
};

bool begin();
// The link to be, "" for none (then it only reads).
void setLink(const String& url);
// Read cards for `ms` instead of being the tag.
void readFor(uint32_t ms);
// From loop().
Event update(bool resting);
// For {"debug": "nfc"}: what it is doing.
String status();
// "tag" (for phones) or "reader" (for cards), for the health line.
const char* mode();

}  // namespace nfc
