#include "vision.h"

#include <M5Unified.h>
#include <esp_camera.h>
#include <esp_heap_caps.h>
#include <freertos/idf_additions.h>
#include <esp_log.h>

#include "esp_jpeg_enc.h"
#include "human_face_detect.hpp"

// esp32-camera's own SCCB teardown (private header): it removes the sensor
// from the I2C bus the driver opened and deletes the bus.
extern "C" int SCCB_Deinit(void);

namespace {

// M5Stack's GC0308 configuration for the CoreS3 (M5CoreS3's GC0308.cpp). The
// sensor has its own clock (no XCLK); its SCCB is the internal I2C bus.
camera_config_t cameraConfig() {
  camera_config_t c = {};
  c.pin_pwdn = -1;
  c.pin_reset = -1;
  c.pin_xclk = -1;
  c.pin_sccb_sda = 12;
  c.pin_sccb_scl = 11;
  c.pin_d7 = 47;
  c.pin_d6 = 48;
  c.pin_d5 = 16;
  c.pin_d4 = 15;
  c.pin_d3 = 42;
  c.pin_d2 = 41;
  c.pin_d1 = 40;
  c.pin_d0 = 39;
  c.pin_vsync = 46;
  c.pin_href = 38;
  c.pin_pclk = 45;
  c.xclk_freq_hz = 20000000;
  c.ledc_timer = LEDC_TIMER_0;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.pixel_format = PIXFORMAT_RGB565;
  c.frame_size = FRAMESIZE_QVGA;
  c.jpeg_quality = 0;
  c.fb_count = 2;
  c.fb_location = CAMERA_FB_IN_PSRAM;
  c.grab_mode = CAMERA_GRAB_LATEST;
  c.sccb_i2c_port = -1;
  return c;
}

SemaphoreHandle_t lock;
Sighting current;
volatile bool paused = false;
volatile uint32_t frames = 0;
// Debug view: the camera image on the robot's own screen, with the
// detector's boxes, and counts for the Pi's journal.
volatile bool preview = false;
// The debug view's frame and boxes, copied here for the main loop to draw:
// the display is its, and drawing from this task as well crashed the robot
// (a mutex given by the other task: "assert failed: xTaskPriorityDisinherit").
struct PreviewBox {
  int16_t x0, y0, x1, y1;
  bool face;
};
uint16_t* previewFrame = nullptr;
PreviewBox previewBoxes[8];
int previewBoxCount = 0;
int previewW = 0, previewH = 0;
bool previewFresh = false;
volatile uint32_t candidatesSeen = 0, trackedFrames = 0;
volatile bool started = false;
// Time spent detecting, for stats(): the whole frame and the tile, ms in all, and runs.
volatile uint32_t wholeMs = 0, wholeRuns = 0, tileMs = 0, tileRuns = 0;
// Faces found by the tile pass, and the widest and narrowest faces, pixels.
volatile uint32_t tileFaces = 0, widestPx = 0, narrowestPx = 0;
volatile float bestScore = 0;

// One frame as a JPEG, on request (the server's face library; the robot keeps
// no pictures). The encoder wants a 16-byte-aligned input, and the frame
// buffer's alignment is the heap's (8 bytes), so a frame is copied into this.
jpeg_enc_handle_t jpeg = nullptr;
uint8_t* jpegIn = nullptr;
constexpr uint16_t kFrameW = 320, kFrameH = 240;

// The image is mirrored: a face on the robot's left is on the frame's right.
constexpr float kMirrorX = -1.0f;
// One frame every 150 ms; one a second while the robot naps.
volatile uint32_t framePeriodMs = 150;

// A detection counts as a face once it has turned up in about the same place
// in kHitsNeeded frames in a row, so one stray hit doesn't move the head.
constexpr int kHitsNeeded = 2;
// Far faces: the detector sees the whole frame halved to 160x120, where a
// face more than about a metre away is under its smallest template (16 px).
// So each frame with no near face one 160x120 piece of the frame is also
// looked at full size: the one around a face far off while it is there, else
// the one around what just moved (someone walking in), else the next of kTiles
// in turn (overlapping by half, so a small face is whole in one of them). With
// MSR+MNP a pass takes ~68 ms, so both fit in a frame (150 ms); with ESPDet
// (317 ms) they didn't.
constexpr bool kTilesOn = true;
constexpr float kTileMotionMin = 0.01f, kTileMotionMax = 0.5f;  // shares of the frame that changed
constexpr int kTileW = 160, kTileH = 120;
constexpr int kTiles[][2] = {{80, 60}, {0, 0}, {80, 0}, {160, 0}, {0, 60}, {160, 60},
                             {0, 120}, {80, 120}, {160, 120}};
constexpr int kTileCount = sizeof(kTiles) / sizeof(kTiles[0]);
uint8_t* tileBuf = nullptr;

// A face the detector found, in frame pixels.
struct Found {
  float score;
  int x0, y0, x1, y1;
};

// Copies the tile at (tx, ty) out of the frame (RGB565: two bytes a pixel).
void cutTile(const uint8_t* frame, int frameW, int tx, int ty) {
  for (int y = 0; y < kTileH; y++)
    memcpy(tileBuf + y * kTileW * 2, frame + ((ty + y) * frameW + tx) * 2, kTileW * 2);
}
// Movement: the frame in kGridW × kGridH cells, each its mean brightness; a
// cell that changed by more than kMotionDelta (of 255) since the frame before
// moved.
constexpr int kGridW = 16, kGridH = 12, kMotionDelta = 12;
uint8_t lastGrid[kGridW * kGridH];
bool haveGrid = false;

void brightnessGrid(const uint8_t* buf, int w, int h, uint8_t* grid) {
  int cw = w / kGridW, ch = h / kGridH;
  for (int gy = 0; gy < kGridH; gy++) {
    for (int gx = 0; gx < kGridW; gx++) {
      uint32_t sum = 0, n = 0;
      for (int y = gy * ch; y < (gy + 1) * ch; y += 2) {  // every other pixel: plenty for a mean
        for (int x = gx * cw; x < (gx + 1) * cw; x += 2) {
          const uint8_t* p = buf + (y * w + x) * 2;  // RGB565, high byte first
          uint16_t v = (uint16_t)(p[0] << 8 | p[1]);
          sum += (((v >> 11) << 3) * 77 + (((v >> 5) & 0x3F) << 2) * 150 + ((v & 0x1F) << 3) * 29) >> 8;
          n++;
        }
      }
      grid[gy * kGridW + gx] = n ? sum / n : 0;
    }
  }
}
constexpr float kSamePlace = 0.25f;  // share of the frame width
constexpr uint32_t kHitGapMs = 700;

void detectTask(void*) {
  // Espressif's human_face_detect (esp-dl 3, the two-stage MSR+MNP model in
  // flash), with the thresholds StackChan-CustomFW uses on this camera. The
  // detector bundled with Arduino 2.0.x scored a face here 0.10-0.15 and
  // found it in about one frame in a hundred (2026-10-09). ESPDet (224x224)
  // sees further but ran at 2.5 frames a second and left too little internal
  // RAM for the NFC (2026-10-15): tiles instead (above).
  HumanFaceDetect detector;
  detector.set_score_thr(0.25f, 0);
  detector.set_score_thr(0.3f, 1);
  // A second one for the tiles: each keeps its buffers for its own image size
  // (one alternating between the two sizes rebuilt them at every run, 320 ms).
  HumanFaceDetect tileDetector;
  tileDetector.set_score_thr(0.25f, 0);
  tileDetector.set_score_thr(0.3f, 1);
  int nextTile = 0;
  int farX = -1, farY = 0;  // the tile around a far face, while there is one
  uint32_t farAt = 0;
  float trackX = 0, trackY = 0;
  int hits = 0;
  uint32_t lastHit = 0;

  while (!started) vTaskDelay(pdMS_TO_TICKS(100));
  for (;;) {
    if (paused) {
      vTaskDelay(pdMS_TO_TICKS(500));
      continue;
    }
    uint32_t started = millis();
    camera_fb_t* fb = esp_camera_fb_get();
    if (!fb) {
      vTaskDelay(pdMS_TO_TICKS(200));
      continue;
    }
    frames++;
    uint8_t grid[kGridW * kGridH];
    brightnessGrid(fb->buf, fb->width, fb->height, grid);
    float moved = 0, movedX = 0, movedY = 0;
    int motionTX = -1, motionTY = 0;  // the tile around what moved, if anything did
    if (haveGrid) {
      int n = 0;
      float sx = 0, sy = 0;
      for (int i = 0; i < kGridW * kGridH; i++) {
        if (abs((int)grid[i] - (int)lastGrid[i]) > kMotionDelta) {
          n++;
          sx += (i % kGridW + 0.5f) / kGridW;
          sy += (i / kGridW + 0.5f) / kGridH;
        }
      }
      if (n) {
        moved = (float)n / (kGridW * kGridH);
        movedX = kMirrorX * (sx / n * 2 - 1);
        movedY = -(sy / n * 2 - 1);
        if (moved >= kTileMotionMin && moved <= kTileMotionMax) {
          motionTX = constrain((int)(sx / n * kFrameW) - kTileW / 2, 0, kFrameW - kTileW);
          motionTY = constrain((int)(sy / n * kFrameH) - kTileH / 2, 0, kFrameH - kTileH);
        }
      }
    }
    memcpy(lastGrid, grid, sizeof grid);
    haveGrid = true;
    // The camera sends RGB565 high byte first.
    dl::image::img_t img = {fb->buf, (uint16_t)fb->width, (uint16_t)fb->height,
                            dl::image::DL_IMAGE_PIX_TYPE_RGB565BE};
    Found found[10];
    int foundCount = 0;
    uint32_t t0 = millis();
    auto& wholeFound = detector.run(img);
    wholeMs += millis() - t0;
    wholeRuns++;
    for (auto& c : wholeFound) {
      if (foundCount == 10) break;
      found[foundCount++] = {c.score, c.box[0], c.box[1], c.box[2], c.box[3]};
    }
    bool whole = foundCount > 0;
    if (kTilesOn && tileBuf && fb->width == kFrameW && fb->height == kFrameH &&
        fb->len >= (size_t)kFrameW * kFrameH * 2) {
      // Nothing near in the whole frame: a piece of it, full size.
      int tx, ty;
      if (farX >= 0 && millis() - farAt < kHitGapMs * 2) {
        tx = farX;
        ty = farY;
      } else if (motionTX >= 0) {
        farX = -1;
        tx = motionTX;
        ty = motionTY;
      } else {
        farX = -1;
        tx = kTiles[nextTile][0];
        ty = kTiles[nextTile][1];
        nextTile = (nextTile + 1) % kTileCount;
      }
      if (!whole) {
        cutTile(fb->buf, fb->width, tx, ty);
        dl::image::img_t tile = {tileBuf, kTileW, kTileH, dl::image::DL_IMAGE_PIX_TYPE_RGB565BE};
        uint32_t t1 = millis();
        auto& tileFound = tileDetector.run(tile);
        tileMs += millis() - t1;
        tileRuns++;
        for (auto& c : tileFound) {
          if (foundCount == 10) break;
          // The tile is the model's own size: its boxes are in tile pixels.
          found[foundCount++] = {c.score, tx + c.box[0], ty + c.box[1], tx + c.box[2], ty + c.box[3]};
          tileFaces++;
        }
      }
    }
    candidatesSeen += foundCount;

    for (int i = 0; i < foundCount; i++) {
      uint32_t w = (uint32_t)max(0, found[i].x1 - found[i].x0);
      if (w > widestPx) widestPx = w;
      if (!narrowestPx || w < narrowestPx) narrowestPx = w;
    }
    // The strongest candidate, in frame shares (0..1).
    const Found* best = nullptr;
    for (int i = 0; i < foundCount; i++)
      if (!best || found[i].score > best->score) best = &found[i];
    float bx = 0, by = 0, bw = 0;
    if (best) {
      bx = (best->x0 + best->x1) / 2.0f / fb->width;
      by = (best->y0 + best->y1) / 2.0f / fb->height;
      bw = (float)(best->x1 - best->x0) / fb->width;
      if (!whole) {
        // A far face: keep looking at the piece around it.
        farX = constrain((int)(bx * fb->width) - kTileW / 2, 0, kFrameW - kTileW);
        farY = constrain((int)(by * fb->height) - kTileH / 2, 0, kFrameH - kTileH);
        farAt = millis();
      }
      if (best->score > bestScore) bestScore = best->score;
      uint32_t now = millis();
      bool near = hits > 0 && now - lastHit < kHitGapMs && fabsf(bx - trackX) < kSamePlace &&
                  fabsf(by - trackY) < kSamePlace;
      hits = near ? hits + 1 : 1;
      trackX = bx;
      trackY = by;
      lastHit = now;
    } else if (millis() - lastHit > kHitGapMs) {
      hits = 0;
    }

    if (preview && previewFrame && fb->len >= (size_t)fb->width * fb->height * 2) {
      xSemaphoreTake(lock, portMAX_DELAY);
      memcpy(previewFrame, fb->buf, (size_t)fb->width * fb->height * 2);
      previewW = fb->width;
      previewH = fb->height;
      previewBoxCount = 0;
      for (int i = 0; i < foundCount && previewBoxCount < 8; i++) {
        const Found& c = found[i];
        previewBoxes[previewBoxCount++] = {(int16_t)c.x0, (int16_t)c.y0, (int16_t)c.x1, (int16_t)c.y1,
                                           &c == best && hits >= kHitsNeeded};
      }
      previewFresh = true;
      xSemaphoreGive(lock);
    }
    esp_camera_fb_return(fb);

    Sighting s;
    s.at = millis();
    s.motion = moved;
    s.motionX = movedX;
    s.motionY = movedY;
    if (best && hits >= kHitsNeeded) {
      trackedFrames++;
      s.face = true;
      s.x = kMirrorX * (bx * 2 - 1);
      s.y = -(by * 2 - 1);
      s.size = bw;
      s.faces = (uint8_t)min(foundCount, 9);
    }

    xSemaphoreTake(lock, portMAX_DELAY);
    current = s;
    xSemaphoreGive(lock);

    // Always yield, even when the detector took longer than a frame period:
    // this task never blocks otherwise, and core 0's idle task must run or
    // the task watchdog restarts the robot (a pass takes 150-330 ms).
    uint32_t spent = millis() - started;
    uint32_t period = framePeriodMs;
    vTaskDelay(pdMS_TO_TICKS(spent + 20 < period ? period - spent : 20));
  }
}

}  // namespace

namespace vision {

bool begin() {
  lock = xSemaphoreCreateMutex();
  // The driver's DMA task logs a warning for a short or overflowing frame,
  // and its small stack overflows doing it (stack canary, cam_task). A bad
  // frame is only skipped; keep it quiet.
  esp_log_level_set("cam_hal", ESP_LOG_NONE);
  esp_log_level_set("camera", ESP_LOG_ERROR);
  // The camera's SCCB shares the internal I2C bus (PMIC, touch, the body's
  // IO expander and head sensor). It is needed only to set the sensor up:
  // M5Unified lets go of the bus, the camera driver opens it and configures
  // the sensor, closes it again (SCCB_Deinit; ESP-IDF 5 allows one driver per
  // port), and the bus goes back to M5Unified. Frames come over the parallel
  // port.
  M5.In_I2C.release();
  camera_config_t config = cameraConfig();
  esp_err_t err = esp_camera_init(&config);
  if (err == ESP_OK) {
    if (sensor_t* sensor = esp_camera_sensor_get()) {
      sensor->set_hmirror(sensor, 0);
    }
  }
  SCCB_Deinit();
  M5.In_I2C.begin(I2C_NUM_1, 12, 11);
  if (err != ESP_OK) {
    return false;
  }
  // The face's picture, on request: a copy of the frame for the encoder,
  // which itself is opened per picture (captureJpeg).
  jpegIn = (uint8_t*)heap_caps_aligned_alloc(16, kFrameW * kFrameH * 2, MALLOC_CAP_SPIRAM);
  // A piece of the frame at full size, for faces too far for the whole one.
  tileBuf = (uint8_t*)heap_caps_aligned_alloc(16, kTileW * kTileH * 2, MALLOC_CAP_SPIRAM);
  // Its stack in PSRAM: internal RAM is for the radios.
  xTaskCreatePinnedToCoreWithCaps(detectTask, "vision", 12288, nullptr, 1, nullptr, 0, MALLOC_CAP_SPIRAM);
  return true;
}

Sighting latest() {
  xSemaphoreTake(lock, portMAX_DELAY);
  Sighting s = current;
  xSemaphoreGive(lock);
  return s;
}

bool captureJpeg(uint8_t* out, uint32_t outLen, uint32_t* outSize) {
  if (!jpegIn || !out || !outSize) return false;
  // Opened per picture: the encoder holds about 39 KB of internal RAM, which
  // Wi-Fi needs the rest of the time. 4:2:0 at quality 55 keeps a QVGA frame
  // well under the 48 KB the capture task gives it.
  jpeg_enc_config_t jc = DEFAULT_JPEG_ENC_CONFIG();
  jc.src_type = JPEG_PIXEL_FORMAT_RGB565_BE;  // the camera's native order
  jc.subsampling = JPEG_SUBSAMPLE_420;
  jc.quality = 55;
  jc.task_enable = false;  // encode in the caller's context
  if (jpeg_enc_open(&jc, &jpeg) != JPEG_ERR_OK) return false;
  camera_fb_t* fb = esp_camera_fb_get();
  if (!fb) {
    jpeg_enc_close(jpeg);
    jpeg = nullptr;
    return false;
  }
  bool ok = false;
  if (fb->format == PIXFORMAT_RGB565 && fb->width == kFrameW && fb->height == kFrameH) {
    size_t bytes = (size_t)fb->width * fb->height * 2;
    memcpy(jpegIn, fb->buf, bytes);
    int size = 0;
    ok = jpeg_enc_process(jpeg, jpegIn, (int)bytes, out, (int)outLen, &size) == JPEG_ERR_OK;
    if (ok) *outSize = (uint32_t)size;
  }
  esp_camera_fb_return(fb);
  jpeg_enc_close(jpeg);
  jpeg = nullptr;
  return ok;
}

void setPaused(bool p) { paused = p; }

void setFramePeriod(uint32_t ms) { framePeriodMs = ms; }

uint32_t frameCount() { return frames; }

void start() { started = true; }

void setPreview(bool on) {
  if (on && !previewFrame) {
    previewFrame = (uint16_t*)heap_caps_malloc(kFrameW * kFrameH * 2, MALLOC_CAP_SPIRAM);
  }
  preview = on && previewFrame;
}

void drawPreview() {
  if (!preview || !previewFrame) return;
  xSemaphoreTake(lock, portMAX_DELAY);
  if (!previewFresh) {
    xSemaphoreGive(lock);
    return;
  }
  previewFresh = false;
  M5.Display.startWrite();
  M5.Display.pushImage(0, 0, previewW, previewH, previewFrame);
  for (int i = 0; i < previewBoxCount; i++) {
    const PreviewBox& b = previewBoxes[i];
    M5.Display.drawRect(b.x0, b.y0, b.x1 - b.x0, b.y1 - b.y0, b.face ? TFT_GREEN : TFT_YELLOW);
  }
  M5.Display.endWrite();
  xSemaphoreGive(lock);
}

String stats() {
  char buf[260];
  snprintf(buf, sizeof buf,
           "camera: %u frames, %u candidates (best %.2f, %u by the tile, %u-%u px wide), %u frames with a face; "
           "detect %u ms whole, %u ms tile; %u KB internal free",
           (unsigned)frames, (unsigned)candidatesSeen, (double)bestScore, (unsigned)tileFaces,
           (unsigned)narrowestPx, (unsigned)widestPx, (unsigned)trackedFrames,
           (unsigned)(wholeRuns ? wholeMs / wholeRuns : 0), (unsigned)(tileRuns ? tileMs / tileRuns : 0),
           (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
  candidatesSeen = trackedFrames = 0;
  tileFaces = widestPx = narrowestPx = 0;
  wholeMs = wholeRuns = tileMs = tileRuns = 0;
  bestScore = 0;
  return buf;
}

}  // namespace vision
