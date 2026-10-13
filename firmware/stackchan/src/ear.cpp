#include "ear.h"

#include <M5Unified.h>
#include <esp_heap_caps.h>

namespace ear {
namespace {

constexpr uint32_t kRingSamples = kRate;     // one second: the loop drains it every 10 ms
constexpr uint32_t kChunkFrames = kRate / 100;  // 10 ms per record(), stereo

int16_t* ring = nullptr;
int16_t* chunks[2] = {nullptr, nullptr};
volatile uint32_t written = 0;  // samples since start()
uint32_t readPos = 0;
volatile bool on = false;
bool works = false;

// From M5Unified's capture task: both channels mixed into the ring, and the
// buffer back in line. Captured in stereo: M5Unified's mono takes a channel
// that carries no microphone on the CoreS3 (measured: silence while counting
// aloud), and the two that do are alike.
void onChunk(void*, void* data, size_t length) {
  if (!on) return;  // stopping: let the queue run dry
  const int16_t* in = (const int16_t*)data;
  uint32_t frames = length / 2;
  uint32_t at = written;
  for (uint32_t f = 0; f < frames; f++) ring[(at + f) % kRingSamples] = (int16_t)(((int32_t)in[2 * f] + in[2 * f + 1]) / 2);
  written = at + frames;
  M5.Mic.record((int16_t*)data, length, kRate, true);
}

}  // namespace

bool begin() {
  ring = (int16_t*)heap_caps_calloc(kRingSamples, sizeof(int16_t), MALLOC_CAP_SPIRAM);
  for (auto& c : chunks) c = (int16_t*)heap_caps_malloc(kChunkFrames * 2 * sizeof(int16_t), MALLOC_CAP_SPIRAM);
  if (!ring || !chunks[0] || !chunks[1]) return false;
  auto cfg = M5.Mic.config();
  cfg.sample_rate = kRate;
  cfg.stereo = true;
  M5.Mic.config(cfg);
  M5.Mic.setBufferReleaseCallback(nullptr, onChunk);
  works = M5.Mic.begin();
  M5.Mic.end();
  return works;
}

void start() {
  if (!works || on) return;
  written = 0;
  readPos = 0;
  on = true;
  if (!M5.Mic.begin()) {
    on = false;
    return;
  }
  M5.Mic.record(chunks[0], kChunkFrames * 2, kRate, true);
  M5.Mic.record(chunks[1], kChunkFrames * 2, kRate, true);
}

void stop() {
  if (!on) return;
  on = false;
  M5.Mic.end();
}

bool recording() { return on; }

size_t available() {
  uint32_t end = written;
  // Fallen a whole ring behind: skip to what is still there.
  if (end - readPos > kRingSamples) readPos = end - kRingSamples;
  return end - readPos;
}

size_t take(int16_t* out, size_t max) {
  size_t n = min(available(), max);
  for (size_t i = 0; i < n; i++) out[i] = ring[(readPos + i) % kRingSamples];
  readPos += n;
  return n;
}

}  // namespace ear
