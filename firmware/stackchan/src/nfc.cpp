#include "nfc.h"

#include <M5Unified.h>
#include <M5UnitUnified.h>
#include <M5UnitUnifiedNFC.h>
#include <freertos/idf_additions.h>

namespace nfc {
namespace {

// One at a time, and the tag by default. Taking turns doesn't work: a phone
// whose field was there before the tag started is never noticed (the tag
// wakes on a field appearing), and the robot's own reader field makes the
// phone back off (measured: an Android phone read the tag only while the
// robot was a tag throughout, 13 reads in about two seconds). So the robot
// reads cards only in a window someone asks for ("remember this card").
constexpr uint32_t kPollMs = 150;
constexpr uint32_t kSameTapMs = 2000;
constexpr uint32_t kSessionGapMs = 3000;  // a phone's reads this far apart are separate taps

m5::unit::UnitUnified units;
m5::unit::UnitNFC unit{};
m5::nfc::NFCLayerA reader{unit};

class Tag : public m5::nfc::EmulationLayerA {
 public:
  using EmulationLayerA::EmulationLayerA;
  uint32_t reads = 0, lastRead = 0;
  State receive_callback(const uint8_t* rx, const uint32_t rx_len) override {
    if (rx_len >= 1 && rx[0] == 0x30) {  // READ: a phone reading the link
      reads++;
      lastRead = millis();
    }
    return EmulationLayerA::receive_callback(rx, rx_len);
  }
};
Tag tag{unit};

bool started = false;
// The NFC work runs in its own task: as a tag the robot must answer a
// phone's reads within milliseconds, which the main loop (face, servos)
// can't promise. The loop only collects what happened.
SemaphoreHandle_t lock = nullptr;
volatile bool wantRead = false;
String pendingLink;
volatile bool linkChanged = false;
QueueHandle_t events = nullptr;  // Event*, from the task to the loop
volatile bool restingNow = false;
bool beingTag = false;
String link;
uint8_t memory[45 * 4];
uint32_t readUntil = 0, nextPoll = 0;
String lastUid;
uint32_t lastSeen = 0, sessionReads = 0, phoneSeen = 0;
uint32_t stuckSince = 0;  // a field on, but the tag still "off"
volatile bool fieldSeen = false;  // the last look, for status()
uint32_t restarts = 0;
uint32_t polls = 0, found = 0;  // for status(): the reader's attempts and finds
bool lastRestartOk = true;
String lastRaw;  // the last UID read, filtered or not (status())

// An NTAG213's memory holding one NDEF URI record; false when it won't fit.
bool build(const String& url, const uint8_t uid[7]) {
  memset(memory, 0, sizeof memory);
  memcpy(memory, uid, 3);
  memory[3] = 0x88 ^ uid[0] ^ uid[1] ^ uid[2];  // BCC0, with the cascade tag
  memcpy(memory + 4, uid + 3, 4);
  memory[8] = uid[3] ^ uid[4] ^ uid[5] ^ uid[6];  // BCC1
  memory[9] = 0x48;
  const uint8_t cc[4] = {0xE1, 0x10, 0x12, 0x00};  // NDEF 1.0, 144 bytes
  memcpy(memory + 12, cc, 4);
  bool https = url.startsWith("https://");
  String rest = https ? url.substring(8) : url;
  size_t payload = 1 + rest.length();
  size_t record = 4 + payload;
  if (record + 3 > 144) return false;
  uint8_t* p = memory + 16;
  *p++ = 0x03;  // NDEF message TLV
  *p++ = (uint8_t)record;
  *p++ = 0xD1;  // one short well-known record
  *p++ = 0x01;
  *p++ = (uint8_t)payload;
  *p++ = 'U';
  *p++ = https ? 0x04 : 0x00;  // "https://"
  memcpy(p, rest.c_str(), rest.length());
  p[rest.length()] = 0xFE;
  const uint8_t dynLock[4] = {0x00, 0x00, 0x00, 0xBD}, cfg0[4] = {0x02, 0x00, 0x00, 0xFF};
  memcpy(memory + 40 * 4, dynLock, 4);
  memcpy(memory + 41 * 4, cfg0, 4);  // no password (AUTH0 0xFF)
  return true;
}

// Switching modes runs the chip's whole start-up (a reset, the antenna and
// field-detector settings, then the mode): the mode switch alone left the
// reader deaf after being a tag (measured: no card read until a reboot).
bool restart(bool emulation) {
  auto cfg = unit.config();
  cfg.emulation = emulation;
  cfg.mode = m5::nfc::NFC::A;
  unit.config(cfg);
  lastRestartOk = unit.begin();
  return lastRestartOk;
}

void toReader() {
  if (beingTag) tag.end();
  restart(false);
  beingTag = false;
}

bool toTag() {
  // A fixed, made-up 7-byte UID (0x04 first, as NXP's tags have).
  const uint8_t uid[7] = {0x04, 0x5A, 0x7C, 0x11, 0x22, 0x33, 0x44};
  m5::nfc::a::PICC picc{};
  if (!build(link, uid) || !picc.emulate(m5::nfc::a::Type::NTAG_213, uid, sizeof uid)) return false;
  if (!restart(true) || !tag.begin(picc, memory, sizeof memory)) {
    toReader();
    return false;
  }
  beingTag = true;
  return true;
}

// Another reader's field (a phone) is on the antenna.
bool fieldPresent() {
  uint8_t v = 0;
  return unit.readAuxiliaryDisplay(v) && (v & 0x40);  // efd_o: external field detected
}

// A phone or a bank card: a random 4-byte UID (first byte 0x08), new each time.
bool randomUid(const String& uid) { return uid.length() == 8 && uid.startsWith("08"); }

}  // namespace

Event work(bool resting);

void task(void*) {
  for (;;) {
    if (linkChanged) {
      xSemaphoreTake(lock, portMAX_DELAY);
      link = pendingLink;
      linkChanged = false;
      xSemaphoreGive(lock);
      if (beingTag) toReader();  // a tag again with the new link, next round
    }
    if (wantRead) {
      wantRead = false;
      if (beingTag) toReader();
      nextPoll = 0;
    }
    Event e = work(restingNow);
    if (e.uid.length() || e.phoneRead) {
      Event* copy = new Event(e);
      if (xQueueSend(events, &copy, 0) != pdTRUE) delete copy;
    }
    vTaskDelay(1);  // about a millisecond: a phone's reads get their answers in time
  }
}

bool begin() {
  started = units.add(unit, M5.In_I2C) && units.begin();
  if (!started) return false;
  lock = xSemaphoreCreateMutex();
  events = xQueueCreate(8, sizeof(Event*));
  // Internal stack: it talks I2C, and a PSRAM stack must not meet a flash write.
  xTaskCreatePinnedToCore(task, "nfc", 4096, nullptr, 3, nullptr, 1);
  return true;
}

void setLink(const String& url) {
  xSemaphoreTake(lock, portMAX_DELAY);
  bool same = url == pendingLink && !linkChanged && url == link;
  pendingLink = url;
  if (!same) linkChanged = true;
  xSemaphoreGive(lock);
}

void readFor(uint32_t ms) {
  readUntil = millis() + ms;
  wantRead = true;
}

Event update(bool resting) {
  restingNow = resting;
  Event e;
  Event* got = nullptr;
  if (events && xQueueReceive(events, &got, 0) == pdTRUE && got) {
    e = *got;
    delete got;
  }
  return e;
}

Event work(bool resting) {
  Event e;
  if (!started) return e;
  uint32_t now = millis();
  units.update();
  bool reading = now < readUntil || !link.length();

  if (beingTag) {
    if (reading) {
      toReader();
      return e;
    }
    tag.update();
    // A phone's reads, one tap per burst.
    if (tag.reads != sessionReads) {
      if (now - phoneSeen > kSessionGapMs) e.phoneRead = true;
      sessionReads = tag.reads;
      phoneSeen = now;
    }
    // The tag wakes on a field appearing. A phone already lying on the robot
    // when it started never "appears": start it again, which re-arms that.
    auto st = tag.state();
    fieldSeen = fieldPresent();
    if (st == m5::nfc::EmulationLayerA::State::Off && fieldSeen) {
      if (!stuckSince) stuckSince = now;
      if (now - stuckSince > 1000) {
        stuckSince = 0;
        restarts++;
        tag.end();
        toTag();
      }
    } else {
      stuckSince = 0;
    }
    return e;
  }

  if (!reading) {
    toTag();
    return e;
  }
  if (now < nextPoll) return e;
  nextPoll = now + (resting && !link.length() && now >= readUntil ? 1000 : kPollMs);
  std::vector<m5::nfc::a::PICC> piccs;
  polls++;
  if (!reader.detect(piccs) || piccs.empty()) return e;
  found++;
  String uid = piccs[0].uidAsString().c_str();
  reader.deactivate();  // halted: a tag left on it doesn't answer again until lifted
  uid.toUpperCase();
  lastRaw = uid;
  if (randomUid(uid)) return e;
  bool sameTap = uid == lastUid && now - lastSeen < kSameTapMs;
  lastUid = uid;
  lastSeen = now;
  if (!sameTap) e.uid = uid;
  return e;
}

String status() {
  static const char* names[] = {"none", "off", "idle", "ready", "active", "halt"};
  int st = (int)tag.state();
  return String(beingTag ? "tag" : "reader") + (link.length() ? "" : " (no link)") +
         (millis() < readUntil ? " (reading window)" : "") + ", tag state " +
         (st >= 0 && st < 6 ? names[st] : "?") + ", " + tag.reads + " reads, field " +
         (fieldSeen ? "on" : "off") + ", " + restarts + " restarts, reader " + polls + " polls " + found +
         " found (last " + (lastRaw.length() ? lastRaw : String("-")) + "), chip restart " +
         (lastRestartOk ? "ok" : "FAILED");
}

}  // namespace nfc
