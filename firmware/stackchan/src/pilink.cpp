#include "pilink.h"

#include <NetworkClientSecure.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <freertos/idf_additions.h>

#include <deque>

namespace pilink {
namespace {

constexpr uint32_t kUsbQuietMs = 10000;   // no USB line this long: Wi-Fi
constexpr uint32_t kUsbSteadyMs = 10000;  // USB back this long: Wi-Fi off
constexpr uint32_t kDialFirstMs = 5000, kDialMaxMs = 60000;
// The robot dozes between beacons (Bluetooth's radio sharing needs it), and
// some access points never flag the frames they hold for a dozing client:
// the MediaTek MT7921 serving as one (2026-10-20, a capture showed no
// beacon set its bit in ~800). A frame from the robot wakes it for a moment
// and the access point hands them over, so it sends one this often: a byte
// to the Pi's discard port, which the Pi drops.
constexpr uint32_t kKeepAliveMs = 200;
constexpr uint16_t kDiscardPort = 9;
constexpr uint32_t kConnectTimeoutMs = 8000;
constexpr size_t kMaxLine = 1024;  // the Pi's lines are short

struct Config {
  String ssid, password, host, fingerprint, token, id;
  uint16_t port = 0;
  bool valid() const { return ssid.length() && host.length() && port && fingerprint.length() == 64; }
};

Config config;
SemaphoreHandle_t configLock, usbLock, wifiLock;

// USB: read in the loop, so only the loop touches these.
String usbLine;
std::deque<String> usbLines;
uint32_t lastUsb = 0, usbSince = 0;

// Wi-Fi: the radio is switched in the loop (it may touch flash, which a task
// with its stack in PSRAM must not); the connection lives in its own task.
NetworkClientSecure client;
QueueHandle_t wifiLines;  // char*, from the Wi-Fi task to the loop
volatile bool wantWifi = false;
volatile bool radioOn = false;
volatile bool clientOpen = false;
volatile bool dialling = false;
volatile bool authed = false;  // the bridge answered the hello: its token was right
volatile bool wrongCertificate = false;  // to log once a link is up

bool usbAlive() { return lastUsb && millis() - lastUsb < kUsbQuietMs; }

Config copyConfig() {
  xSemaphoreTake(configLock, portMAX_DELAY);
  Config c = config;
  xSemaphoreGive(configLock);
  return c;
}

void logLine(const char* text) {
  JsonDocument doc;
  doc["log"] = text;
  send(doc);
}

void closeClient() {
  xSemaphoreTake(wifiLock, portMAX_DELAY);
  client.stop();
  clientOpen = authed = false;
  xSemaphoreGive(wifiLock);
}

bool dial() {
  Config c = copyConfig();
  if (!c.valid()) return false;
  // The chain is not checked: the bridge's certificate is its own, pinned by
  // its fingerprint below before anything is sent.
  client.setInsecure();
  client.setHandshakeTimeout(10);
  dialling = true;
  xSemaphoreTake(wifiLock, portMAX_DELAY);
  bool ok = client.connect(c.host.c_str(), c.port, kConnectTimeoutMs);
  bool pinned = ok && client.verify(c.fingerprint.c_str(), nullptr);
  if (ok && !pinned) client.stop();
  if (pinned) {
    clientOpen = true;
    authed = false;
    JsonDocument doc;
    helloLine(doc);
    doc["hello"]["token"] = c.token;
    String line;
    serializeJson(doc, line);
    line += '\n';
    client.write((const uint8_t*)line.c_str(), line.length());
  }
  xSemaphoreGive(wifiLock);
  dialling = false;
  if (ok && !pinned) wrongCertificate = true;  // someone else on that address
  return pinned;
}

// The Wi-Fi signal, read here in the Wi-Fi task: the radio and its TLS client
// are this task's alone (reading it from the loop broke the link).
volatile int lastRssi = 0;

void wifiTask(void*) {
  uint32_t backoff = kDialFirstMs, nextDial = 0, nextRssi = 0, nextKeepAlive = 0;
  WiFiUDP keepAlive;
  IPAddress pi;
  bool havePi = false;
  String line;
  uint8_t buf[256];
  for (;;) {
    if (!wantWifi || WiFi.status() != WL_CONNECTED) {
      if (clientOpen) closeClient();
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    if (!clientOpen) {
      if ((int32_t)(millis() - nextDial) >= 0 && !dial()) {
        nextDial = millis() + backoff;
        backoff = min(backoff * 2, kDialMaxMs);
      }
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    if ((int32_t)(millis() - nextKeepAlive) >= 0) {
      nextKeepAlive = millis() + kKeepAliveMs;
      if (havePi || (havePi = pi.fromString(copyConfig().host))) {
        keepAlive.beginPacket(pi, kDiscardPort);
        keepAlive.write((uint8_t)0);
        keepAlive.endPacket();
      }
    }
    if ((int32_t)(millis() - nextRssi) >= 0) {
      nextRssi = millis() + 10000;
      lastRssi = WiFi.RSSI();
    }
    xSemaphoreTake(wifiLock, portMAX_DELAY);
    bool alive = client.connected();
    int n = alive && client.available() ? client.read(buf, sizeof buf) : 0;
    xSemaphoreGive(wifiLock);
    for (int i = 0; i < n; i++) {
      if (buf[i] == '\n') {
        if (line.length()) {
          char* copy = strdup(line.c_str());
          if (copy && xQueueSend(wifiLines, &copy, 0) != pdTRUE) free(copy);
          if (!authed) {
            authed = true;  // the bridge only answers a hello with the right token
            backoff = kDialFirstMs;
          }
        }
        line = "";
      } else if (line.length() < kMaxLine) {
        line += (char)buf[i];
      }
    }
    if (!alive) {
      // Closed: the bridge went away, or refused the token.
      closeClient();
      line = "";
      nextDial = millis() + backoff;
      backoff = min(backoff * 2, kDialMaxMs);
    }
    if (n <= 0) vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void startRadio(const Config& c) {
  WiFi.persistent(false);  // the details live in our own namespace
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(c.ssid.c_str(), c.password.c_str());
  radioOn = true;
}

void stopRadio() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  radioOn = false;
}

void load() {
  Preferences prefs;
  if (!prefs.begin("pilink", true)) return;  // never provisioned
  Config c;
  c.ssid = prefs.getString("ssid", "");
  c.password = prefs.getString("password", "");
  c.host = prefs.getString("host", "");
  c.port = prefs.getUShort("port", 0);
  c.fingerprint = prefs.getString("fingerprint", "");
  c.token = prefs.getString("token", "");
  c.id = prefs.getString("id", "");
  prefs.end();
  if (c.valid()) config = c;
}

bool isHex(const String& s) {
  for (size_t i = 0; i < s.length(); i++)
    if (!isxdigit((unsigned char)s[i])) return false;
  return true;
}

}  // namespace

void begin() {
  configLock = xSemaphoreCreateMutex();
  usbLock = xSemaphoreCreateMutex();
  wifiLock = xSemaphoreCreateMutex();
  wifiLines = xQueueCreate(16, sizeof(char*));
  load();
  // TLS wants a deep stack; PSRAM has room for it (nothing in it touches flash).
  xTaskCreatePinnedToCoreWithCaps(wifiTask, "wifi", 10240, nullptr, 2, nullptr, 0, MALLOC_CAP_SPIRAM);
}

void update() {
  uint32_t now = millis();
  bool wasAlive = usbAlive();
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      usbLine.trim();
      // Only the Pi's JSON lines count as the Pi: a host that merely probes
      // the port (ModemManager's "AT" on a laptop, after every reset) must
      // not take the robot off Wi-Fi.
      if (usbLine.startsWith("{") && usbLine.endsWith("}")) {
        usbLines.push_back(usbLine);
        if (!wasAlive) usbSince = now;
        lastUsb = now;
        wasAlive = true;
      }
      usbLine = "";
    } else if (usbLine.length() < kMaxLine) {
      usbLine += c;
    }
  }

  Config c = copyConfig();
  bool usb = usbAlive();
  if (!wantWifi && c.valid() && !usb && now > kUsbQuietMs) {
    wantWifi = true;
    startRadio(c);
  } else if (wantWifi && (!c.valid() || (usb && now - usbSince > kUsbSteadyMs))) {
    wantWifi = false;  // the task closes the connection, then the radio goes off
  }
  if (!wantWifi && radioOn && !clientOpen && !dialling) stopRadio();
  if (wrongCertificate && active() != Via::None) {
    wrongCertificate = false;
    logLine("wifi: the bridge's certificate did not match; did not talk to it");
  }
}

bool next(String& line, Via& via) {
  if (!usbLines.empty()) {
    line = usbLines.front();
    usbLines.pop_front();
    via = Via::Usb;
    return true;
  }
  char* text = nullptr;
  if (xQueueReceive(wifiLines, &text, 0) == pdTRUE && text) {
    line = text;
    free(text);
    via = Via::Wifi;
    return true;
  }
  return false;
}

Via active() {
  if (usbAlive()) return Via::Usb;
  if (clientOpen && authed) return Via::Wifi;
  return Via::None;
}

int rssi() { return active() == Via::Wifi ? lastRssi : 0; }

const char* name(Via via) { return via == Via::Usb ? "usb" : via == Via::Wifi ? "wifi" : "none"; }

void send(JsonDocument& doc) {
  Via via = active();
  if (via == Via::Usb) {
    xSemaphoreTake(usbLock, portMAX_DELAY);
    serializeJson(doc, Serial);
    Serial.print('\n');
    xSemaphoreGive(usbLock);
  } else if (via == Via::Wifi) {
    // One write, so a long line (a face's picture) is a few TLS records, not
    // one per character.
    String line;
    serializeJson(doc, line);
    line += '\n';
    xSemaphoreTake(wifiLock, portMAX_DELAY);
    if (clientOpen) client.write((const uint8_t*)line.c_str(), line.length());
    xSemaphoreGive(wifiLock);
  }
}

String configId() { return copyConfig().id; }

bool provision(JsonVariantConst wifi) {
  Preferences prefs;
  if (wifi.isNull()) {
    if (prefs.begin("pilink", false)) {
      prefs.clear();
      prefs.end();
    }
    xSemaphoreTake(configLock, portMAX_DELAY);
    config = Config{};
    xSemaphoreGive(configLock);
    return true;
  }
  if (!wifi.is<JsonObjectConst>()) return false;
  Config c;
  c.ssid = wifi["ssid"] | "";
  c.password = wifi["password"] | "";
  c.host = wifi["host"] | "";
  c.port = wifi["port"] | 0;
  c.fingerprint = wifi["fingerprint"] | "";
  c.token = wifi["token"] | "";
  c.id = wifi["id"] | "";
  c.fingerprint.toLowerCase();
  if (!c.valid() || c.ssid.length() > 32 || c.password.length() > 64 || c.host.length() > 64 ||
      !isHex(c.fingerprint) || c.token.length() < 16 || c.token.length() > 64 || !c.id.length() ||
      c.id.length() > 32)
    return false;
  if (!prefs.begin("pilink", false)) return false;
  prefs.putString("ssid", c.ssid);
  prefs.putString("password", c.password);
  prefs.putString("host", c.host);
  prefs.putUShort("port", c.port);
  prefs.putString("fingerprint", c.fingerprint);
  prefs.putString("token", c.token);
  prefs.putString("id", c.id);
  prefs.end();
  xSemaphoreTake(configLock, portMAX_DELAY);
  config = c;
  xSemaphoreGive(configLock);
  // New details while on Wi-Fi: start over with them.
  wantWifi = false;
  return true;
}

}  // namespace pilink
