#include "phones.h"

#include <BLEDevice.h>
#include <Preferences.h>
#include <host/ble_gap.h>
#include <mbedtls/aes.h>

#include <algorithm>
#include <vector>

#include "pilink.h"

namespace phones {
namespace {

constexpr uint32_t kReportMs = 15000;
constexpr uint32_t kWindowMs = 60000;   // a phone heard in the last minute is near
constexpr size_t kMaxBeacons = 16, kMaxIrks = 32, kMaxKey = 40;
constexpr size_t kSamples = 32;         // per person, the newest
constexpr size_t kCache = 64;           // addresses already resolved (or not)
// A slice of every interval, so Wi-Fi keeps the radio most of the time.
constexpr uint16_t kScanInterval = 0x0800;  // 1.28 s, in 0.625 ms units
constexpr uint16_t kScanWindow = 0x0100;    // 160 ms

struct Beacon {
  uint8_t uuid[16];
  int person;
};
struct Irk {
  uint8_t key[16];
  int person;
};
struct Sample {
  uint32_t at;
  int8_t rssi;
};
struct Cached {
  uint8_t address[6];
  int person;  // -1: nobody's
};

std::vector<String> people;
std::vector<Beacon> beacons;
std::vector<Irk> irks;
std::vector<std::vector<Sample>> heard;  // per person
Cached cache[kCache];
size_t cacheNext = 0, cacheUsed = 0;
String id;
// The lists and the samples: the Bluetooth host's task fills samples, the
// loop reads them and swaps in new lists. The host never waits for it: a
// busy lock drops that advertisement.
SemaphoreHandle_t lock = nullptr;
bool started = false;      // the Bluetooth stack
volatile bool scanning = false;
uint32_t nextReport = 0, nextScanCheck = 0;
volatile uint32_t advertisements = 0, matched = 0;  // for the debug log
int lastScanError = 0;
uint32_t debugUntil = 0, nextDebug = 0;

bool hex16(const String& text, uint8_t out[16]) {
  String h = text;
  h.replace("-", "");
  if (h.length() != 32) return false;
  for (int i = 0; i < 16; i++) {
    char pair[3] = {h[2 * i], h[2 * i + 1], 0};
    if (!isxdigit((unsigned char)pair[0]) || !isxdigit((unsigned char)pair[1])) return false;
    out[i] = (uint8_t)strtoul(pair, nullptr, 16);
  }
  return true;
}

void ah(const uint8_t key[16], const uint8_t prand[3], uint8_t out[3]) {
  uint8_t in[16] = {0}, enc[16];
  memcpy(in + 13, prand, 3);
  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  mbedtls_aes_setkey_enc(&aes, key, 128);
  mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, in, enc);
  mbedtls_aes_free(&aes);
  memcpy(out, enc + 13, 3);
}

// Who sent this advertisement, or -1. In the Bluetooth host's task.
int recognise(const uint8_t* data, uint8_t length, const ble_addr_t& addr) {
  // An iBeacon: Apple's manufacturer data, 0x02 0x15, then the UUID.
  for (uint8_t i = 0; i + 1 < length;) {
    uint8_t len = data[i];
    if (len == 0 || i + 1 + len > length) break;
    const uint8_t* field = data + i + 2;
    if (data[i + 1] == 0xFF && len >= 1 + 22 && field[0] == 0x4C && field[1] == 0x00 && field[2] == 0x02 &&
        field[3] == 0x15) {
      for (const auto& b : beacons)
        if (!memcmp(b.uuid, field + 4, 16)) return b.person;
      return -1;
    }
    i += 1 + len;
  }
  // A resolvable private address: most significant bits 01.
  uint8_t msb[6];
  for (int i = 0; i < 6; i++) msb[i] = addr.val[5 - i];
  if (addr.type != BLE_ADDR_RANDOM || (msb[0] >> 6) != 0b01 || irks.empty()) return -1;
  for (size_t i = 0; i < cacheUsed; i++)
    if (!memcmp(cache[i].address, msb, 6)) return cache[i].person;
  int person = -1;
  for (const auto& k : irks)
    if (resolves(k.key, msb)) {
      person = k.person;
      break;
    }
  memcpy(cache[cacheNext].address, msb, 6);
  cache[cacheNext].person = person;
  cacheNext = (cacheNext + 1) % kCache;
  cacheUsed = min(cacheUsed + 1, kCache);
  return person;
}

void advertisement(const uint8_t* data, uint8_t length, const ble_addr_t& addr, int8_t rssi) {
  advertisements = advertisements + 1;
  if (xSemaphoreTake(lock, 0) != pdTRUE) return;
  int person = recognise(data, length, addr);
  if (person >= 0) matched = matched + 1;
  if (person >= 0 && (size_t)person < heard.size()) {
    auto& s = heard[person];  // capacity reserved: no allocation here
    if (s.size() >= kSamples) s.erase(s.begin());
    s.push_back({millis(), rssi});
  }
  xSemaphoreGive(lock);
}

int onGap(struct ble_gap_event* e, void*) {
  if (e->type == BLE_GAP_EVENT_DISC) {
    advertisement(e->disc.data, e->disc.length_data, e->disc.addr, e->disc.rssi);
#if defined(CONFIG_BT_NIMBLE_EXT_ADV) && CONFIG_BT_NIMBLE_EXT_ADV
  } else if (e->type == BLE_GAP_EVENT_EXT_DISC) {
    advertisement(e->ext_disc.data, e->ext_disc.length_data, e->ext_disc.addr, e->ext_disc.rssi);
#endif
  } else if (e->type == BLE_GAP_EVENT_DISC_COMPLETE) {
    scanning = false;
  }
  return 0;
}

void startScan() {
  if (!started) {
    if (!BLEDevice::init("")) return;
    started = true;
  }
  struct ble_gap_disc_params params = {};
  params.itvl = kScanInterval;
  params.window = kScanWindow;
  params.passive = 1;            // listen only: the robot never asks phones anything
  params.filter_duplicates = 0;  // every advertisement: its strength is the point
  int rc = ble_gap_disc(BLE_OWN_ADDR_PUBLIC, BLE_HS_FOREVER, &params, onGap, nullptr);
  scanning = rc == 0 || rc == BLE_HS_EALREADY;
  lastScanError = rc;
}

void stopScan() {
  if (scanning) ble_gap_disc_cancel();
  scanning = false;
}

// From the stored JSON: what to look for. False when there is nothing.
bool load(const String& json) {
  JsonDocument doc;
  if (deserializeJson(doc, json) != DeserializationError::Ok || !doc.is<JsonObject>()) return false;
  std::vector<String> names;
  std::vector<Beacon> b;
  std::vector<Irk> k;
  // Built outside the lock (allocations), swapped in under it.
  auto index = [&](const String& key) {
    for (size_t i = 0; i < names.size(); i++)
      if (names[i] == key) return (int)i;
    if (key.isEmpty() || key.length() > kMaxKey) return -1;
    names.push_back(key);
    return (int)names.size() - 1;
  };
  for (JsonPairConst kv : doc["beacons"].as<JsonObjectConst>()) {
    Beacon x;
    String key = kv.value() | "";
    if (b.size() < kMaxBeacons && hex16(kv.key().c_str(), x.uuid) && (x.person = index(key)) >= 0) b.push_back(x);
  }
  for (JsonPairConst kv : doc["irks"].as<JsonObjectConst>()) {
    for (JsonVariantConst hex : kv.value().as<JsonArrayConst>()) {
      Irk x;
      if (k.size() < kMaxIrks && hex16(hex | "", x.key) && (x.person = index(kv.key().c_str())) >= 0) {
        k.push_back(x);
        // Tools disagree on the key's byte order: try it reversed too.
        Irk r = x;
        std::reverse(r.key, r.key + 16);
        k.push_back(r);
      }
    }
  }
  std::vector<std::vector<Sample>> h(names.size());
  for (auto& v : h) v.reserve(kSamples);
  xSemaphoreTake(lock, portMAX_DELAY);
  people.swap(names);
  beacons.swap(b);
  irks.swap(k);
  heard.swap(h);
  cacheUsed = cacheNext = 0;
  xSemaphoreGive(lock);
  id = doc["id"] | "";
  return !beacons.empty() || !irks.empty();
}

}  // namespace

bool resolves(const uint8_t irk[16], const uint8_t msb[6]) {
  uint8_t hash[3];
  ah(irk, msb, hash);
  return !memcmp(hash, msb + 3, 3);
}

bool selfTest() {
  // Bluetooth Core, Vol 3, Part H, D.7: IRK ec0234a3...9b resolves 70:81:94:0D:FB:AA.
  uint8_t irk[16], addr[6] = {0x70, 0x81, 0x94, 0x0D, 0xFB, 0xAA};
  hex16("ec0234a357c8ad05341010a60a397d9b", irk);
  uint8_t rev[16];
  std::reverse_copy(irk, irk + 16, rev);
  return resolves(irk, addr) || resolves(rev, addr);
}

void begin() {
  lock = xSemaphoreCreateMutex();
  Preferences prefs;
  String json;
  if (prefs.begin("phones", true)) {
    json = prefs.getString("config", "");
    prefs.end();
  }
  if (json.length() && load(json)) startScan();
}

void debug(uint32_t ms) { debugUntil = millis() + ms; }

void update() {
  uint32_t now = millis();
  if (debugUntil && now < debugUntil && now >= nextDebug) {
    nextDebug = now + 5000;
    char line[160];
    snprintf(line, sizeof line, "phones: %s, scan %s (rc %d), %u advertisements, %u matched, %u people",
             id.length() ? id.c_str() : "nothing to look for", scanning ? "on" : "off", lastScanError,
             (unsigned)advertisements, (unsigned)matched, (unsigned)people.size());
    JsonDocument doc;
    doc["log"] = line;
    pilink::send(doc);
  }
  if (!id.length()) return;
  if (now >= nextScanCheck) {
    nextScanCheck = now + 5000;
    if (!scanning) startScan();  // stopped by an error: again
  }
  if (now < nextReport) return;
  nextReport = now + kReportMs;
  JsonDocument doc;
  JsonArray seen = doc["seen"].to<JsonArray>();
  std::vector<std::pair<String, int>> found;
  xSemaphoreTake(lock, portMAX_DELAY);
  for (size_t p = 0; p < heard.size(); p++) {
    int8_t recent[kSamples];
    size_t n = 0;
    for (const auto& s : heard[p])
      if (now - s.at <= kWindowMs) recent[n++] = s.rssi;
    if (n) {
      std::sort(recent, recent + n);
      found.push_back({people[p], recent[n / 2]});
    }
  }
  xSemaphoreGive(lock);
  if (found.empty()) return;
  for (auto& f : found) {
    JsonObject o = seen.add<JsonObject>();
    o["person"] = f.first;
    o["rssi"] = f.second;
  }
  pilink::send(doc);
}

bool provision(JsonVariantConst config) {
  Preferences prefs;
  if (config.isNull()) {
    stopScan();
    if (prefs.begin("phones", false)) {
      prefs.clear();
      prefs.end();
    }
    load("{}");
    id = "";
    return true;
  }
  if (!config.is<JsonObjectConst>()) return false;
  String json;
  serializeJson(config, json);
  if (json.length() > 3500 || !(config["id"] | "")[0]) return false;
  if (!load(json)) return false;
  if (!prefs.begin("phones", false)) return false;
  prefs.putString("config", json);
  prefs.end();
  if (!scanning) startScan();
  return true;
}

String configId() { return id; }

}  // namespace phones
