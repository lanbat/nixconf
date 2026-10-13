#include "nfc.h"

#include <M5Unified.h>
#include <M5UnitUnified.h>
#include <M5UnitUnifiedNFC.h>

namespace nfc {
namespace {

constexpr uint32_t kPollMs = 300, kRestingPollMs = 1000;
constexpr uint32_t kSameTapMs = 2000;

m5::unit::UnitUnified units;
m5::unit::UnitNFC unit{};
m5::nfc::NFCLayerA nfcA{unit};
bool started = false;
uint32_t nextPoll = 0;
String lastUid;
uint32_t lastSeen = 0;

}  // namespace

bool begin() {
  started = units.add(unit, M5.In_I2C) && units.begin();
  return started;
}

String update(bool resting) {
  uint32_t now = millis();
  if (!started || now < nextPoll) return "";
  nextPoll = now + (resting ? kRestingPollMs : kPollMs);
  units.update();
  std::vector<m5::nfc::a::PICC> piccs;
  if (!nfcA.detect(piccs) || piccs.empty()) return "";
  String uid = piccs[0].uidAsString().c_str();
  // Halted: a tag left on the reader doesn't answer again until it is lifted.
  nfcA.deactivate();
  uid.toUpperCase();
  bool sameTap = uid == lastUid && now - lastSeen < kSameTapMs;
  lastUid = uid;
  lastSeen = now;
  return sameTap ? "" : uid;
}

}  // namespace nfc
