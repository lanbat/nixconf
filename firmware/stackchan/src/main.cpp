// Stack-chan as the face of a lanbat voice satellite (docs/stackchan.md).
//
// The robot is alive on its own: it blinks and breathes (M5Stack-Avatar),
// looks at the people its camera sees, looks around when nobody is there and
// dozes off when nobody has been for a while. When a face appears after an
// absence it notices: a glance, a nod, a warm glow.
//
// The Pi (pkgs/lva-stackchan) tells it over USB serial, or over Wi-Fi when
// the robot isn't plugged into it (pilink.h), one JSON object per line, what
// the voice assistant is doing:
//
//   {"mood": "neutral|listening|thinking|happy|sad|surprised|sleepy|confused|curious"}
//   {"look": "track|user|up|center"}
//   {"gesture": "perk|nod|shake|wiggle|tilt"}
//   {"mouth": 0.0-1.0}
//   {"caption": {"text": "...", "who": "user|assistant|status", "ms": 8000}}
//   {"timers": [{"id", "name", "remaining_s", "total_s", "ringing"}]}
//   {"sleep": true|false}
//   {"status": {"muted": bool, "online": bool}}
//   {"config": {"brightness": 0-255, "notice": bool}}
//   {"yaw": -700..700}              where the voice came from (the Pi's mics)
//   {"capture": true}               one camera frame, as a JPEG
//   {"ping": 1}                       a heartbeat, every 3 s
//   {"wifi": {...} | null}          Wi-Fi details to keep (over USB only)
//   {"hello": 1}                     asks for a hello (the bridge just started)
//   {"phones": {...} | null}        whose phones to look for (its Pi's links only)
//   {"led": {"rgb": [r, g, b]} | null}  a notification light (Home Assistant)
//   {"tap": {"url": "https://..."}}  the link it is a tag with, for phones (nfc.h)
//   {"nfc_read": seconds}           read cards instead, for a while
//   {"nap": true|false}             nap now, or wake from a nap
//   {"face_at": ms}                 within ms, where the face is once the head is still
//   {"greet": true}                 someone it knows: awake, happy, a wag and a nod,
//                                    three green breaths
//   {"config": {..., "auto_brightness": bool}}  the screen follows the room's light
//   {"record": true|false}          the microphone on from the wake word to the
//                                    end of the sentence (ear.h), 15 s at most
//
// and hears back:
//
//   {"hello": {"fw", "proto", "link": "usb|wifi", "wifi": "<id>", "camera": bool,
//              "ear": bool, "phones": "<id>", "nfc": bool, "reset": "<why it
//              last started>", "up_s": seconds since}}
//                                    when a link comes up; the Pi answers
//                                    with the whole state
//   {"touch": "tap|stroke", "zone": "front|middle|back|screen"}
//   {"face": "new|lost"}
//   {"face_image": "base64 JPEG, or empty"}     a capture's answer
//   {"audio": "base64 s16le, 16 kHz mono"}       while recording, every 100 ms
//   {"seen": [{"person", "rssi"}]}  phones near it, every 15 s (phones.h)
//   {"senses": {"lux", "prox", "near"}}  light and a hand near it, on change (senses.h)
//   {"wave": true}                   a hand waved in front of the screen
//   {"nfc": "<UID hex>"}            a tag or card tapped on it (nfc.h)
//   {"tapped": true}                 a phone read its link (its tag, nfc.h)
//   {"health": {"rssi", "heap_kb", "psram_kb", "uptime_s", "nfc": "tag|reader",
//               "yaw"}}             every minute: how it is doing (yaw in degrees)
//   {"face_at": {"yaw", "faces"} | null}  the answer to face_at: a face's direction
//                                    in the head's degrees, and how many faces
//   {"log": "..."}
//
// Without a line for 10 s on either link it takes the Pi for gone and shows it.

#include <Arduino.h>
#include <ArduinoJson.h>
#include <Avatar.h>
#include <M5StackChan.h>
#include <M5Unified.h>
#include <esp_heap_caps.h>
#include <esp_system.h>

#include <freertos/idf_additions.h>

#include "ear.h"
#include "lights.h"
#include "nfc.h"
#include "phones.h"
#include "senses.h"
#include "pilink.h"
#include "vision.h"

using namespace m5avatar;

namespace {

constexpr const char* kFirmware = "1.9.8";
bool phonesWork = true;  // the address check passes Bluetooth's own sample (setup)
constexpr int kProtocol = 1;

// Head limits, in tenths of a degree (the BSP's units). Narrower than the
// servos' own (yaw ±128°, pitch 0-90°) so a gesture on top of a glance never
// hits an end stop.
constexpr int kYawMin = -700, kYawMax = 700;
constexpr int kPitchMin = 50, kPitchMax = 800;
constexpr int kPitchRest = 300;  // looking slightly up, at someone in front
// Half the camera's field of view, in tenths of a degree (GC0308, ~60°).
constexpr float kHalfFovYaw = 300, kHalfFovPitch = 230;

constexpr uint32_t kPiTimeoutMs = 10000;
constexpr uint32_t kAbsenceForNoticeMs = 60000;  // gone this long, then seen: noticed
constexpr uint32_t kFaceLostMs = 2500;
constexpr uint32_t kDrowsyAfterMs = 5 * 60000;
constexpr uint32_t kNapFramePeriodMs = 1000;

Avatar avatar;
bool hasCamera = false;
bool debugCamera = false;
uint32_t debugUntil = 0;
// The debug view ends by itself, so a lost "off" never leaves the robot faceless.
constexpr uint32_t kDebugMs = 120000;
uint32_t nextStats = 0;

// What the Pi said.
String mood = "neutral";
String look = "track";
bool asleep = false;
bool muted = false;
bool haOnline = true;
bool noticeGuests = true;
uint8_t brightness = 180;
bool autoBrightness = true;  // the screen follows the room's light (senses.h)
bool hasSenses = false;
bool hasNfc = false;
uint32_t nfcDebugUntil = 0;  // {"debug": "nfc"}: its state in the log
uint32_t sensesDebugUntil = 0, nextSensesDebug = 0;

String caption;
uint32_t captionUntil = 0;
String timerText;
float timerLeftShare = -1;
bool timerRinging = false;

// What the robot knows itself.
uint32_t lastLine = 0;
bool piPresent = false;
bool faceVisible = false;
uint32_t lastFaceSeen = 0;
// The last time anything happened with it: a turn (its moods), a touch, a
// hand, a tag, a command, waking from a nap. A nap or a doze counts from the
// later of this and the last face, so that a robot whose camera doesn't see
// you (at a desk, in the dark) stays awake after you've talked to it.
uint32_t lastActive = 0;
uint32_t lastFaceFrame = 0;
float lastFaceYaw = 0, lastFacePitch = kPitchRest;
bool drowsy = false;
// Nobody seen for napAfterMs (from the Pi; 0: never): a nap, dark and still,
// with the camera slowed down. A face, a touch or the wake word ends it.
bool napping = false;
// The battery, read every 30 s. Low (<= kLowBatteryLevel %, not charging):
// the servos are switched off, since a move's current surge on a weak supply
// made the power chip cut the robot off; they come back when it charges.
constexpr int kLowBatteryLevel = 15, kBatteryOkLevel = 25;
bool lowBattery = false;
uint32_t nextBatteryRead = 5000, nextBatteryReport = 0;
int lastBatteryLevel = -100;
uint32_t napAfterMs = 15 * 60000;
uint32_t nextWander = 0;
uint32_t touchDownZone = 0;

int targetYaw = 0, targetPitch = kPitchRest;

// The microphone, as a second ear for voice-id: on from the Pi's
// {"record": true} to {"record": false}, never longer than kRecordMaxMs.
bool hasEar = false;
constexpr uint32_t kRecordMaxMs = 15000;
constexpr size_t kAudioChunk = ear::kRate / 10;  // 100 ms per line
uint32_t recordUntil = 0;

// One frame as a JPEG, on request (the server's face library): the Pi asks,
// a task encodes the frame and sends it, the answer is one line.
volatile bool capturePending = false;
constexpr uint32_t kCaptureOutBytes = 48 * 1024;

// A gesture is a few keyframes on top of where the head is pointed.
struct Keyframe {
  int yaw, pitch;  // offsets, tenths of a degree
  uint16_t ms;
};
const Keyframe* gesture = nullptr;
// {"face_at": ms}: the Pi learns its turns to a voice from where the face then
// is. Answered once the head has been still for kStillMs, or with null.
uint32_t faceAtUntil = 0;
uint32_t lastMoveAt = 0;  // when the last move ends (a move takes its speed, in ms)
constexpr uint32_t kStillMs = 400;
int gestureLength = 0, gestureStep = 0;
uint32_t gestureStepAt = 0;

const Keyframe kNod[] = {{0, -120, 220}, {0, 40, 200}, {0, 0, 250}};
const Keyframe kShake[] = {{-130, 0, 180}, {130, 0, 220}, {-130, 0, 220}, {0, 0, 200}};
const Keyframe kWiggle[] = {{-90, 30, 120}, {90, 30, 120}, {-90, 30, 120}, {90, 30, 120}, {0, 0, 200}};
const Keyframe kPerk[] = {{0, 90, 150}, {0, 60, 300}};
const Keyframe kTilt[] = {{110, 40, 400}, {110, 40, 900}, {0, 0, 400}};
const Keyframe kDance[] = {{-200, 80, 250}, {200, 0, 250}, {-200, 80, 250}, {200, 0, 250},
                           {0, 150, 300}, {0, -60, 250}, {0, 150, 250}, {-300, 40, 350},
                           {300, 40, 450}, {0, 0, 350}};
const Keyframe kStretch[] = {{0, 220, 600}, {0, 220, 700}, {0, 0, 500}};
const Keyframe kLookAround[] = {{-450, 60, 700}, {-450, 60, 600}, {450, 60, 1100}, {450, 60, 600}, {0, 0, 700}};
// Joy, then yes: a wag (kWiggle) and a nod (kNod), for someone it knows.
const Keyframe kGreet[] = {{-90, 30, 120}, {90, 30, 120}, {-90, 30, 120}, {90, 30, 120},
                           {0, -120, 220}, {0, 40, 200}, {0, 0, 250}};

// The day's brightness, dimmed in a dim room: a bright face in the dark is
// glaring, a dim one in daylight unreadable. Relative to the brightest light
// this robot has seen lately (it fades over about half a day), since the
// sensor behind the glass reads only a few lux in a lit room.
float luxPeak = 0;
uint8_t dayBrightness() {
  if (!autoBrightness || !hasSenses || !senses::latest().ok) return brightness;
  float l = senses::latest().lux;
  float k = luxPeak > 1 ? constrain(0.3f + 0.7f * l / luxPeak, 0.3f, 1.0f) : 1.0f;
  return (uint8_t)(brightness * k);
}

// Both eyes the same way (Avatar's own saccades move them again in a while).
void gaze(float vertical, float horizontal) {
  avatar.setRightGaze(vertical, horizontal);
  avatar.setLeftGaze(vertical, horizontal);
}

// One line at a time on the link: a face's picture is a long write, and two
// concurrent writes would interleave it with whatever else the robot says.
void send(JsonDocument& doc) { pilink::send(doc); }

// Base64, for a face's picture.
String base64Encode(const uint8_t* data, uint32_t len) {
  static const char* alphabet =
      "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  String out;
  out.reserve(len / 3 * 4 + 4);
  uint32_t i = 0;
  for (; i + 3 <= len; i += 3) {
    uint32_t v = (uint32_t)data[i] << 16 | (uint32_t)data[i + 1] << 8 | data[i + 2];
    out += alphabet[v >> 18 & 0x3F];
    out += alphabet[v >> 12 & 0x3F];
    out += alphabet[v >> 6 & 0x3F];
    out += alphabet[v & 0x3F];
  }
  if (len - i == 1) {
    uint32_t v = (uint32_t)data[i] << 16;
    out += alphabet[v >> 18 & 0x3F];
    out += alphabet[v >> 12 & 0x3F];
    out += "==";
  } else if (len - i == 2) {
    uint32_t v = (uint32_t)data[i] << 16 | (uint32_t)data[i + 1] << 8;
    out += alphabet[v >> 18 & 0x3F];
    out += alphabet[v >> 12 & 0x3F];
    out += alphabet[v >> 6 & 0x3F];
    out += "=";
  }
  return out;
}

void sendHello() {
  JsonDocument doc;
  helloLine(doc);
  send(doc);
  static bool told = false;
  if (!told && pilink::active() != pilink::Via::None) {
    told = true;
    if (!phonesWork) {
      JsonDocument log;
      log["log"] = "phones: the address check fails Bluetooth's own sample; iPhones won't be recognised";
      send(log);
    }
  }
}

void sendEvent(const char* key, const char* value, const char* zone = nullptr) {
  JsonDocument doc;
  doc[key] = value;
  if (zone) doc["zone"] = zone;
  send(doc);
}

void startGesture(const Keyframe* frames, int length) {
  lastMoveAt = millis();
  gesture = frames;
  gestureLength = length;
  gestureStep = 0;
  gestureStepAt = millis();
}

#define GESTURE(k) startGesture(k, sizeof(k) / sizeof(k[0]))

void pointHead(int yaw, int pitch, int speed) {
  lastMoveAt = millis() + speed;
  targetYaw = constrain(yaw, kYawMin, kYawMax);
  targetPitch = constrain(pitch, kPitchMin, kPitchMax);
  M5StackChan.Motion.move(targetYaw, targetPitch, speed);
}

void setMood(const String& m) {
  mood = m;
  if (m != "sleepy") lastActive = millis();
  if (m == "happy" || m == "excited") {
    avatar.setExpression(Expression::Happy);
  } else if (m == "sad") {
    avatar.setExpression(Expression::Sad);
  } else if (m == "thinking" || m == "confused" || m == "curious") {
    avatar.setExpression(Expression::Doubt);
  } else if (m == "sleepy") {
    avatar.setExpression(Expression::Sleepy);
  } else {
    avatar.setExpression(Expression::Neutral);
  }

  if (m == "listening") {
    lights::setScene(lights::Scene::Listening);
    gaze(0, 0);
  } else if (m == "thinking") {
    lights::setScene(lights::Scene::Thinking);
    gaze(-0.6f, 0.3f);  // eyes up and aside, as people do
  } else if (m == "confused") {
    lights::setScene(lights::Scene::Error);
  } else if (m == "surprised") {
    lights::setScene(lights::Scene::Greeting);
  } else if (m == "excited" || (m == "happy" && !piPresent)) {
    lights::setScene(lights::Scene::Happy);
  } else if (m == "neutral" || m == "sleepy") {
    lights::setScene(lights::Scene::Off);
  }
}

void showText() {
  // A caption while one is due, otherwise the nearest timer.
  if (captionUntil && millis() < captionUntil) {
    avatar.setSpeechText(caption.c_str());
  } else {
    captionUntil = 0;
    avatar.setSpeechText(timerText.c_str());
  }
}

void startNap() {
  napping = drowsy = true;
  avatar.setExpression(Expression::Sleepy);
  avatar.setIsAutoBlink(false);
  avatar.setEyeOpenRatio(0);
  lights::setAsleep(true);
  vision::setFramePeriod(kNapFramePeriodMs);
  M5.Display.setBrightness(min<int>(brightness, 10));
  // The head stays where it is and the servos relax (the BSP releases torque
  // at rest): a move now would draw a surge for nothing.
  sendEvent("rest", "nap");
}

void endNap() {
  lastActive = millis();
  if (!napping) return;
  napping = drowsy = false;
  avatar.setIsAutoBlink(true);
  avatar.setEyeOpenRatio(1);
  lights::setAsleep(asleep);
  vision::setFramePeriod(150);
  sendEvent("rest", "awake");
  if (asleep) return;
  M5.Display.setBrightness(dayBrightness());
  setMood("neutral");
  pointHead(0, kPitchRest, 300);
  GESTURE(kStretch);
}

// Someone it knows came by: awake, happy, a wag and a nod, three green breaths.
// Not at night: the night's rest goes on.
void greet() {
  if (asleep) return;
  endNap();  // and counts as activity
  if (drowsy) {
    drowsy = false;
    pointHead(0, kPitchRest, 300);
  }
  setMood("happy");
  GESTURE(kGreet);
  lights::pulse(0, 220, 60, 3000);
}

void setAsleep(bool a) {
  if (a == asleep) return;
  // Night outranks a nap: the night's sleep takes over from here.
  if (a && napping) {
    napping = false;
    avatar.setIsAutoBlink(true);
    avatar.setEyeOpenRatio(1);
    vision::setFramePeriod(150);
    sendEvent("rest", "awake");
  }
  asleep = a;
  lights::setAsleep(a);
  vision::setPaused(a);
  if (a) {
    setMood("sleepy");
    pointHead(0, kPitchMin, 150);
    M5.Display.setBrightness(min<int>(brightness, 10));
  } else {
    drowsy = false;
    setMood("neutral");
    pointHead(0, kPitchRest, 300);
    M5.Display.setBrightness(dayBrightness());
  }
}

String clock(int seconds) {
  char buf[16];
  if (seconds >= 3600)
    snprintf(buf, sizeof buf, "%d:%02d:%02d", seconds / 3600, seconds / 60 % 60, seconds % 60);
  else
    snprintf(buf, sizeof buf, "%d:%02d", seconds / 60, seconds % 60);
  return buf;
}

void sendAudio(size_t most) {
  static int16_t samples[kAudioChunk];
  size_t n = ear::take(samples, min(most, kAudioChunk));
  if (!n) return;
  JsonDocument doc;
  doc["audio"] = base64Encode((const uint8_t*)samples, n * 2).c_str();
  send(doc);
}

// The ear's audio to the Pi as it comes; what is left once it stops.
void runEar() {
  if (!hasEar) return;
  if (ear::recording() && millis() > recordUntil) ear::stop();
  while (ear::available() >= kAudioChunk) sendAudio(kAudioChunk);
  if (!ear::recording() && ear::available()) sendAudio(ear::available());
}

void handle(JsonDocument& doc, pilink::Via via) {
  if (!doc["hello"].isUnbound()) sendHello();
  if (doc["led"].is<JsonObject>() && doc["led"]["rgb"].is<JsonArray>()) {
    JsonArray rgb = doc["led"]["rgb"];
    lights::setNotify(true, constrain(rgb[0] | 0, 0, 255), constrain(rgb[1] | 0, 0, 255),
                      constrain(rgb[2] | 0, 0, 255));
  } else if (doc["led"].isNull() && !doc["led"].isUnbound()) {
    lights::setNotify(false);
  }
  if (!doc["phones"].isUnbound()) {
    // Whose phones to look for, from its Pi: over USB, or over the Wi-Fi
    // link, which is pinned to the Pi's certificate and carries its token.
    // Only the Wi-Fi details themselves stay USB-only.
    JsonDocument reply;
    if (via != pilink::Via::Usb && via != pilink::Via::Wifi) {
      reply["log"] = "phones: refused, they only come from the robot's Pi";
    } else if (phones::provision(doc["phones"])) {
      reply["log"] = doc["phones"].isNull() ? "phones: forgotten" : "phones: kept, listening";
    } else {
      reply["log"] = "phones: malformed or empty, not kept";
    }
    send(reply);
    sendHello();
  }
  if (doc["record"].is<bool>() && hasEar) {
    if (doc["record"].as<bool>()) {
      ear::start();
      recordUntil = millis() + kRecordMaxMs;
    } else {
      ear::stop();
    }
  }
  if (!doc["wifi"].isUnbound()) {
    // Wi-Fi details: only from the Pi it is plugged into, never over Wi-Fi.
    JsonDocument reply;
    if (via != pilink::Via::Usb) {
      reply["log"] = "wifi: details refused, they only come over USB";
    } else if (pilink::provision(doc["wifi"])) {
      reply["log"] = doc["wifi"].isNull() ? "wifi: details forgotten" : "wifi: details kept";
    } else {
      reply["log"] = "wifi: details malformed, not kept";
    }
    send(reply);
    sendHello();  // with the new id, so the Pi sees they arrived
  }
  if (doc["mood"].is<const char*>()) {
    String m = doc["mood"].as<const char*>();
    if (napping && m != "neutral" && m != "sleepy") endNap();  // the wake word, a timer
    setMood(m);
    if (m == "listening" || m == "thinking") drowsy = false;
  }
  if (doc["look"].is<const char*>()) {
    look = doc["look"].as<const char*>();
    if (look == "user") {
      // At the last person seen, if recently; otherwise straight ahead.
      bool recent = lastFaceSeen && millis() - lastFaceSeen < 30000;
      pointHead(recent ? lastFaceYaw : 0, recent ? lastFacePitch : kPitchRest, 600);
    } else if (look == "up") {
      pointHead(targetYaw, targetPitch + 120, 250);
    } else if (look == "center") {
      pointHead(0, kPitchRest, 400);
    }
  }
  if (doc["yaw"].is<int>()) {
    // Where the voice came from, from the Pi's microphone array
    // (tenths of a degree, the servos' units).
    pointHead(doc["yaw"].as<int>(), targetPitch, 500);
  }
  if ((doc["capture"].is<bool>() && doc["capture"].as<bool>()) ||
      (doc["capture"].is<int>() && doc["capture"].as<int>() == 1)) {
    capturePending = true;
  }
  if (doc["gesture"].is<const char*>()) {
    String g = doc["gesture"].as<const char*>();
    if (g == "nod") GESTURE(kNod);
    else if (g == "shake") GESTURE(kShake);
    else if (g == "wiggle") GESTURE(kWiggle);
    else if (g == "perk") GESTURE(kPerk);
    else if (g == "tilt") GESTURE(kTilt);
    else if (g == "dance") {
      GESTURE(kDance);
      lights::setScene(lights::Scene::Happy);
    } else if (g == "look_around") GESTURE(kLookAround);
  }
  if (!doc["mouth"].isNull()) {
    float level = constrain(doc["mouth"].as<float>(), 0.0f, 1.0f);
    avatar.setMouthOpenRatio(level);
    lights::setMouth(level);
    if (level > 0) lights::setScene(lights::Scene::Speaking);
  }
  if (doc["caption"].is<JsonObject>()) {
    caption = doc["caption"]["text"] | "";
    captionUntil = millis() + (doc["caption"]["ms"] | 8000);
    lastActive = millis();  // something to read: not the moment to doze off
    showText();
  } else if (doc["caption"].isNull() && !doc["caption"].isUnbound()) {
    captionUntil = 0;
    showText();
  }
  if (doc["timers"].is<JsonArray>()) {
    JsonArray timers = doc["timers"];
    timerText = "";
    timerLeftShare = -1;
    timerRinging = false;
    for (JsonObject t : timers) {  // the bridge sends the soonest first
      int left = t["remaining_s"] | 0, total = t["total_s"] | 0;
      bool ringing = t["ringing"] | false;
      if (timerLeftShare < 0) {
        String name = t["name"] | "";
        timerText = (ringing ? String("Time's up! ") : String("")) + (name.length() ? name + " " : "") +
                    (ringing ? "" : clock(left));
        timerLeftShare = total > 0 ? (float)left / total : 0;
      }
      timerRinging |= ringing;
    }
    lights::setTimer(timerLeftShare, timerRinging);
    showText();
  }
  if (doc["sleep"].is<bool>()) setAsleep(doc["sleep"].as<bool>());
  if (doc["face_at"].is<int>()) faceAtUntil = millis() + constrain(doc["face_at"].as<int>(), 100, 10000);
  if (doc["greet"].is<bool>() && doc["greet"].as<bool>()) greet();
  if (doc["nap"].is<bool>()) {
    // {"nap": true|false}: from Home Assistant; not at night, which has its own rest.
    if (doc["nap"].as<bool>() && !napping && !asleep) startNap();
    if (!doc["nap"].as<bool>()) endNap();
  }
  if (doc["nfc_read"].is<int>() && hasNfc) {
    // {"nfc_read": seconds}: read cards for a while ("remember this card").
    nfc::readFor((uint32_t)constrain(doc["nfc_read"].as<int>(), 0, 120) * 1000);
  }
  if (doc["tap"].is<JsonObject>() && hasNfc) {
    // {"tap": {"url": "https://..."}}: the link to be a tag with (the
    // router's "who tapped" page, a fresh one every minute); "" stops it.
    String url = doc["tap"]["url"] | "";
    nfc::setLink(url.startsWith("https://") && url.length() <= 120 ? url : String(""));
  }
  if (doc["debug"].is<const char*>() && String(doc["debug"].as<const char*>()) == "nfc") {
    nfcDebugUntil = millis() + 30000;
  } else if (doc["debug"].is<const char*>() && String(doc["debug"].as<const char*>()) == "senses") {
    sensesDebugUntil = millis() + 30000;
  } else if (doc["debug"].is<const char*>() && String(doc["debug"].as<const char*>()) == "phones") {
    phones::debug(120000);
  } else if (doc["debug"].is<const char*>()) {
    // {"debug": "camera"} shows what the camera sees; {"debug": "off"} ends it.
    debugCamera = hasCamera && String(doc["debug"].as<const char*>()) == "camera";
    debugUntil = millis() + kDebugMs;
    if (debugCamera) {
      avatar.suspend();
      M5.Display.fillScreen(TFT_BLACK);
    } else {
      avatar.resume();
    }
    vision::setPreview(debugCamera);
  }
  if (doc["status"].is<JsonObject>()) {
    muted = doc["status"]["muted"] | false;
    haOnline = doc["status"]["online"] | true;
    lights::setStatus(muted, haOnline);
  }
  if (doc["config"].is<JsonObject>()) {
    if (doc["config"]["brightness"].is<int>()) {
      brightness = doc["config"]["brightness"];
      M5.Display.setBrightness(dayBrightness());
    }
    if (doc["config"]["notice"].is<bool>()) noticeGuests = doc["config"]["notice"];
    if (doc["config"]["auto_brightness"].is<bool>()) {
      autoBrightness = doc["config"]["auto_brightness"];
      if (!asleep && !napping) M5.Display.setBrightness(dayBrightness());
    }
    if (doc["config"]["nap_after_s"].is<int>()) napAfterMs = (uint32_t)doc["config"]["nap_after_s"].as<int>() * 1000;
  }
}

void readLink() {
  pilink::update();
  String line;
  pilink::Via via;
  while (pilink::next(line, via)) {
    JsonDocument doc;
    if (deserializeJson(doc, line) != DeserializationError::Ok || !doc.is<JsonObject>()) continue;
    lastLine = millis();
    if (!piPresent) {
      piPresent = true;
      lights::setStatus(muted, haOnline);
    }
    handle(doc, via);
  }
  // A link came up: hello on it, and the Pi answers with the whole state.
  // Over Wi-Fi the hello opened the connection (with the token) already.
  static pilink::Via linkSeen = pilink::Via::None;
  pilink::Via now = pilink::active();
  if (now != linkSeen) {
    linkSeen = now;
    if (now == pilink::Via::Usb) sendHello();
  }
  if (piPresent && millis() - lastLine > kPiTimeoutMs) {
    piPresent = false;
    lights::setStatus(muted, false);
    caption = "No Pi";
    captionUntil = millis() + 4000;
    showText();
    setMood("neutral");
    avatar.setMouthOpenRatio(0);
  }
}

// The capture's answer, from its own task: encoding and the long write would
// otherwise hold up the loop (the face tracking, the servos, the touch).
void captureTask(void*) {
  uint8_t* out = (uint8_t*)heap_caps_aligned_alloc(16, kCaptureOutBytes, MALLOC_CAP_SPIRAM);
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(20));
    if (!capturePending) continue;
    capturePending = false;
    uint32_t size = 0;
    bool ok = out != nullptr && vision::captureJpeg(out, kCaptureOutBytes, &size);
    JsonDocument doc;
    doc["face_image"] = ok ? base64Encode(out, size).c_str() : "";
    send(doc);
  }
}

// The proximity and light sensor: a hand near wakes it or makes it perk up,
// a wave goes to the Pi (it stops a ringing timer or a reply), and the screen
// follows the room's light.
void runSenses() {
  if (!hasSenses) return;
  if (sensesDebugUntil && millis() < sensesDebugUntil && millis() >= nextSensesDebug) {
    nextSensesDebug = millis() + 200;
    uint16_t ch0, ch1, prox;
    uint8_t status;
    float rest;
    senses::raw(ch0, ch1, status, prox, rest);
    char line[120];
    snprintf(line, sizeof line, "senses: ch0 %u ch1 %u status 0x%02X prox %u resting %.0f lux %.1f", ch0, ch1,
             status, prox, rest, senses::latest().lux);
    JsonDocument d;
    d["log"] = line;
    send(d);
  }
  senses::Reading r;
  bool waved = false;
  bool wasNear = senses::latest().near;
  bool changed = senses::update(r, waved);
  if (senses::latest().ok) {
    static uint32_t lastPeak = millis();
    uint32_t now = millis();
    luxPeak *= powf(0.5f, (now - lastPeak) / (12.0f * 3600000.0f));  // half-life 12 h
    lastPeak = now;
    luxPeak = max(luxPeak, senses::latest().lux);
  }
  if (!changed) return;
  JsonDocument doc;
  doc["senses"]["lux"] = (int)roundf(r.lux);
  doc["senses"]["prox"] = r.prox;
  doc["senses"]["near"] = r.near;
  send(doc);
  if (waved) {
    JsonDocument w;
    w["wave"] = true;
    send(w);
  }
  if (r.near && !wasNear && !asleep) {
    lastActive = millis();
    if (napping) {
      endNap();
    } else if (drowsy) {
      drowsy = false;
      setMood("neutral");
      pointHead(0, kPitchRest, 300);
    } else if (!gesture && mood == "neutral") {
      GESTURE(kPerk);
    }
  }
  if (!asleep && !napping) M5.Display.setBrightness(dayBrightness());
}

// A tag tapped on it: to the Pi (Home Assistant's tag scanner, a person's
// card for the router), with a little perk and a glow so the tap shows.
void runNfc() {
  if (!hasNfc) return;
  if (nfcDebugUntil && millis() < nfcDebugUntil) {
    static uint32_t nextLog = 0;
    if (millis() >= nextLog) {
      nextLog = millis() + 2000;
      JsonDocument d;
      d["log"] = String("nfc: ") + nfc::status();
      send(d);
    }
  }
  nfc::Event e = nfc::update(asleep || napping);
  if (!e.uid.length() && !e.phoneRead) return;
  endNap();
  JsonDocument doc;
  if (e.uid.length()) doc["nfc"] = e.uid;
  if (e.phoneRead) doc["tapped"] = true;  // a phone read its link (the router learns who from the page)
  send(doc);
  lights::setScene(lights::Scene::Greeting);
  if (!gesture) GESTURE(kPerk);
}

void readBattery() {
  uint32_t now = millis();
  if (now < nextBatteryRead) return;
  nextBatteryRead = now + 30000;
  int level = M5.Power.getBatteryLevel();
  bool charging = M5.Power.isCharging() == m5::Power_Class::is_charging;
  int mv = M5.Power.getBatteryVoltage();
  bool low = lowBattery ? !(charging || level >= kBatteryOkLevel)
                        : (level >= 0 && level <= kLowBatteryLevel && !charging);
  bool changed = low != lowBattery;
  if (changed) {
    lowBattery = low;
    M5StackChan.setServoPowerEnabled(!low);
    lights::setLowBattery(low);
    if (low) {
      caption = "Low battery - please charge me";
      captionUntil = now + 15000;
      showText();
    }
  }
  if (changed || abs(level - lastBatteryLevel) >= 5 || now >= nextBatteryReport) {
    lastBatteryLevel = level;
    nextBatteryReport = now + 300000;
    JsonDocument doc;
    doc["battery"]["level"] = level;
    doc["battery"]["mv"] = mv;
    doc["battery"]["charging"] = charging;
    doc["battery"]["low"] = lowBattery;
    send(doc);
  }
}

const char* zoneName(int i) { return i == 0 ? "front" : i == 1 ? "middle" : "back"; }

void readTouch() {
  auto& ts = M5StackChan.TouchSensor;
  // Remember the zone pressed hardest while the head is touched.
  const auto& in = ts.getIntensities();
  int best = -1, bestLevel = 0;
  for (int i = 0; i < 3; i++)
    if (in[i] > bestLevel) best = i, bestLevel = in[i];
  if (best >= 0) touchDownZone = best;

  bool stroke = ts.wasSwiped();
  if (stroke) {
    sendEvent("touch", "stroke", zoneName(touchDownZone));
  } else if (ts.wasClicked()) {
    sendEvent("touch", "tap", zoneName(touchDownZone));
  }
  if (M5.Touch.getCount() && M5.Touch.getDetail(0).wasClicked()) {
    sendEvent("touch", "tap", "screen");
  }
  if ((stroke || ts.wasPressed()) && !piPresent) {
    // On its own it still enjoys being petted.
    setMood("happy");
    GESTURE(kNod);
  }
  if (stroke || ts.wasPressed()) lastActive = millis();
  if (napping && (stroke || ts.wasPressed())) endNap();
  if (drowsy && (stroke || ts.wasPressed())) {
    drowsy = false;
    setMood("neutral");
    pointHead(0, kPitchRest, 300);
  }
}

void follow() {
  uint32_t now = millis();
  if (!hasCamera || asleep) return;
  Sighting s = vision::latest();
  if (debugCamera) {
    // The preview owns the screen: report faces, don't react to them.
    if (s.face && !faceVisible) sendEvent("face", "new");
    if (s.face) lastFaceSeen = millis();
    faceVisible = s.face || (faceVisible && millis() - lastFaceSeen < kFaceLostMs);
    return;
  }
  bool fresh = s.face && s.at != lastFaceFrame;

  if (fresh) {
    lastFaceFrame = s.at;
    bool wasAway = !lastFaceSeen || now - lastFaceSeen > kAbsenceForNoticeMs;
    lastFaceSeen = now;
    // Where the face is, in the head's angles: the camera turns with the head.
    lastFaceYaw = constrain(targetYaw + s.x * kHalfFovYaw, (float)kYawMin, (float)kYawMax);
    lastFacePitch = constrain(targetPitch + s.y * kHalfFovPitch, (float)kPitchMin, (float)kPitchMax);
    if (faceAtUntil && !gesture && (int32_t)(now - lastMoveAt) > (int32_t)kStillMs) {
      JsonDocument d;
      d["face_at"]["yaw"] = (int)lroundf(lastFaceYaw / 10);
      d["face_at"]["faces"] = s.faces;
      send(d);
      faceAtUntil = 0;
    }
    if (!faceVisible) {
      faceVisible = true;
      sendEvent("face", "new");
      endNap();
      if (drowsy) {
        drowsy = false;
        setMood("neutral");
      }
      if (wasAway && noticeGuests && look == "track" && mood == "neutral") {
        // Someone came in: look at them, a glow, a nod.
        avatar.setExpression(Expression::Happy);
        lights::setScene(lights::Scene::Greeting);
        pointHead(lastFaceYaw, lastFacePitch, 700);
        GESTURE(kNod);
      }
    }
    if (look == "track" && !gesture) {
      // Follow gently; ignore small offsets so the head isn't nervous.
      if (fabsf(s.x) > 0.12f || fabsf(s.y) > 0.15f) {
        pointHead(targetYaw + s.x * kHalfFovYaw * 0.6f, targetPitch + s.y * kHalfFovPitch * 0.6f, 350);
      }
      gaze(-s.y * 0.5f, s.x * 0.5f);
    }
  } else if (faceVisible && now - lastFaceSeen > kFaceLostMs) {
    faceVisible = false;
    sendEvent("face", "lost");
    if (mood == "neutral") avatar.setExpression(Expression::Neutral);
  }
}

void wander() {
  uint32_t now = millis();
  if (asleep || napping || look != "track" || faceVisible || gesture || mood != "neutral") return;
  uint32_t idle = now - max(lastFaceSeen, lastActive);
  if (napAfterMs && idle > napAfterMs) {
    startNap();
    return;
  }
  if (!drowsy && idle > kDrowsyAfterMs) {  // since boot, if nobody yet
    // Nobody for a while: it dozes, head down, until someone shows up.
    drowsy = true;
    avatar.setExpression(Expression::Sleepy);
    pointHead(0, kPitchMin + 100, 120);
    return;
  }
  if (drowsy || now < nextWander) return;
  // Glance somewhere else now and then, like something alive.
  nextWander = now + random(6000, 15000);
  pointHead(random(-450, 450), kPitchRest + random(-80, 150), 200);
  gaze(random(-30, 30) / 100.0f, random(-50, 50) / 100.0f);
}

void runGesture() {
  if (!gesture) return;
  uint32_t now = millis();
  if (now - gestureStepAt < (gestureStep ? gesture[gestureStep - 1].ms : 0)) return;
  if (gestureStep >= gestureLength) {
    gesture = nullptr;
    M5StackChan.Motion.move(targetYaw, targetPitch, 500);
    return;
  }
  const Keyframe& k = gesture[gestureStep++];
  gestureStepAt = now;
  M5StackChan.Motion.move(constrain(targetYaw + k.yaw, kYawMin, kYawMax),
                          constrain(targetPitch + k.pitch, kPitchMin, kPitchMax), 850);
}

}  // namespace

// Why the chip last started. A crash, a brownout or a watchdog says something
// went wrong; power-on (plugged in, or the power chip cut it off), software
// (a restart, a flash) and the reset button don't.
const char* resetReason() {
  switch (esp_reset_reason()) {
    case ESP_RST_POWERON: return "power-on";
    case ESP_RST_EXT: return "external";
    case ESP_RST_SW: return "software";
    case ESP_RST_PANIC: return "panic";
    case ESP_RST_INT_WDT: return "interrupt-watchdog";
    case ESP_RST_TASK_WDT: return "task-watchdog";
    case ESP_RST_WDT: return "watchdog";
    case ESP_RST_DEEPSLEEP: return "deep-sleep";
    case ESP_RST_BROWNOUT: return "brownout";
    case ESP_RST_SDIO: return "sdio";
    default: return "other";
  }
}

void helloLine(JsonDocument& doc) {
  doc["hello"]["fw"] = kFirmware;
  doc["hello"]["proto"] = kProtocol;
  doc["hello"]["link"] = pilink::name(pilink::active() == pilink::Via::None ? pilink::Via::Wifi : pilink::active());
  doc["hello"]["wifi"] = pilink::configId();
  doc["hello"]["camera"] = hasCamera;
  doc["hello"]["ear"] = hasEar;
  doc["hello"]["phones"] = phones::configId();
  doc["hello"]["nfc"] = hasNfc;
  doc["hello"]["reset"] = resetReason();
  doc["hello"]["up_s"] = millis() / 1000;
}

void setup() {
  M5StackChan.begin();  // M5Unified, servo power, touch, LEDs
  // The Pi speaks and listens; the robot's own speaker stays off. Its
  // amplifier keeps whatever state the last firmware left it in (the stock
  // one leaves it on), and on with no signal it amplifies noise: switching it
  // on and off through M5Unified powers it down. The microphone is off too,
  // except while the Pi asks for a sentence (ear.h).
  M5.Speaker.begin();
  M5.Speaker.end();
  hasEar = ear::begin();
  // The bridge sends its whole state in a burst when it connects.
  Serial.setRxBufferSize(4096);
  Serial.begin(115200);
  M5.Display.setBrightness(dayBrightness());

  avatar.init();
  avatar.setSpeechFont(&fonts::efontCN_12);

  // The address check first: its AES lock is made on first use, and later
  // internal RAM can be short for a moment while everything starts.
  phonesWork = phones::selfTest();
  pilink::begin();
  phones::begin();
  hasCamera = vision::begin();
  // After the camera: its start-up takes the internal I2C bus over and hands
  // it back re-created, which leaves a driver bound to the old one deaf (the
  // NFC reader was).
  hasSenses = senses::begin();
  hasNfc = nfc::begin();
  pointHead(0, kPitchRest, 300);
  randomSeed(esp_random());
  // Its stack in PSRAM: internal RAM is for the radios (docs/stackchan.md).
  xTaskCreatePinnedToCoreWithCaps(captureTask, "face", 8192, nullptr, 3, nullptr, 1, MALLOC_CAP_SPIRAM);
}

// Every minute: the link's signal, free memory, its NFC's mode and where the
// head points, for Home Assistant (through the Pi).
void runHealth() {
  static uint32_t next = 5000;
  uint32_t now = millis();
  if (now < next) return;
  next = now + 60000;
  JsonDocument doc;
  if (pilink::rssi() < 0) doc["health"]["rssi"] = pilink::rssi();
  doc["health"]["heap_kb"] = heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024;
  doc["health"]["psram_kb"] = heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024;
  doc["health"]["uptime_s"] = now / 1000;
  if (hasNfc) doc["health"]["nfc"] = nfc::mode();
  doc["health"]["yaw"] = targetYaw / 10;
  send(doc);
}

void loop() {
  M5StackChan.update();  // touch sensor (and M5.update)
  runHealth();
  readLink();
  runEar();
  phones::update();
  runSenses();
  runNfc();
  readTouch();
  readBattery();
  follow();
  wander();
  runGesture();
  if (captionUntil && millis() > captionUntil) showText();
  if (faceAtUntil && millis() > faceAtUntil) {
    faceAtUntil = 0;
    JsonDocument d;
    d["face_at"] = nullptr;  // nobody seen with the head still
    send(d);
  }
  lights::update();

  // Once, 15 s after boot: whether the camera delivers frames.
  static bool reported = false;
  if (debugCamera && millis() > debugUntil) {
    debugCamera = false;
    vision::setPreview(false);
    avatar.resume();
  }
  if (debugCamera && millis() > nextStats) {
    nextStats = millis() + 2000;
    JsonDocument doc;
    doc["log"] = vision::stats();
    send(doc);
  }
  if (!reported && hasCamera && millis() > 15000) {
    reported = true;
    JsonDocument doc;
    doc["log"] = String("camera: ") + vision::frameCount() + " frames in the first 15 s";
    send(doc);
  }
  delay(10);
}
