#include "senses.h"

#include <M5Unified.h>

namespace senses {
namespace {

constexpr uint8_t kAddr = 0x23;
constexpr uint32_t kI2cHz = 400000;
constexpr uint32_t kPeriodMs = 100;
// Measured on a CoreS3: the resting echo wanders about +-10; a fingertip 1-2 cm
// in front of the camera adds 40-140, a hand up to 1900.
constexpr uint16_t kNearAbove = 80;   // over the resting echo: something is close
constexpr uint16_t kFarBelow = 40;    // and back under this: it left (hysteresis)
constexpr uint32_t kWaveMs = 600;     // came and went this quickly: a wave
constexpr uint32_t kLuxEveryMs = 5000;
constexpr float kGain = 8, kIntegration = 1.0f;  // 8x, 100 ms (ALS_CONTR, ALS_MEAS_RATE)

bool present = false;
Reading current;
float resting = 0;  // the echo with nothing near, followed slowly
uint32_t nextRead = 0, nearSince = 0, nextLux = 0;
float reportedLux = -1;
uint16_t lastCh0 = 0, lastCh1 = 0;
uint8_t lastStatus = 0;

void write(uint8_t reg, uint8_t value) { M5.In_I2C.writeRegister8(kAddr, reg, value, kI2cHz); }

float lux(uint16_t ch0, uint16_t ch1) {
  // LTR-553ALS-01 datasheet, appendix A.
  if (ch0 + ch1 == 0) return 0;
  float ratio = (float)ch1 / (ch0 + ch1);
  float l;
  if (ratio < 0.45f)
    l = 1.7743f * ch0 + 1.1059f * ch1;
  else if (ratio < 0.64f)
    l = 4.2785f * ch0 - 1.9548f * ch1;
  else if (ratio < 0.85f)
    l = 0.5926f * ch0 + 0.1185f * ch1;
  else
    l = 0;
  return max(0.0f, l / kGain / kIntegration);
}

}  // namespace

bool begin() {
  // Part ID 0x92 (register 0x86): the sensor is there.
  if (M5.In_I2C.readRegister8(kAddr, 0x86, kI2cHz) != 0x92) return false;
  write(0x80, 0x02);  // software reset
  delay(20);
  write(0x82, 0x7F);  // proximity LED: 60 kHz, 100 % duty, 100 mA
  write(0x83, 0x0F);  // 15 pulses
  write(0x84, 0x02);  // proximity every 50 ms
  write(0x85, 0x03);  // light: 100 ms integration, every 500 ms
  write(0x80, 0x0D);  // light on, gain 8x
  write(0x81, 0x03);  // proximity on
  present = true;
  return true;
}

bool update(Reading& out, bool& waved) {
  waved = false;
  uint32_t now = millis();
  if (!present || now < nextRead) return false;
  nextRead = now + kPeriodMs;
  uint8_t p[2], b[4];
  if (!M5.In_I2C.readRegister(kAddr, 0x8D, p, 2, kI2cHz) || !M5.In_I2C.readRegister(kAddr, 0x88, b, 4, kI2cHz))
    return false;
  current.ok = true;
  current.prox = p[0] | (p[1] & 0x07) << 8;
  uint16_t ch1 = b[0] | b[1] << 8, ch0 = b[2] | b[3] << 8;
  lastCh0 = ch0;
  lastCh1 = ch1;
  lastStatus = M5.In_I2C.readRegister8(kAddr, 0x8C, kI2cHz);
  // A saturated or invalid light sample (seen: 65535 on both channels for a
  // moment) would sit in the average for seconds: skip it.
  if (ch0 < 0xFFF0 && ch1 < 0xFFF0 && !(lastStatus & 0x80)) {
    float l = lux(ch0, ch1);
    current.lux = current.lux <= 0 ? l : current.lux * 0.8f + l * 0.2f;
  }
  if (resting <= 0) resting = current.prox;

  bool changed = false;
  if (!current.near && current.prox > resting + kNearAbove) {
    current.near = true;
    nearSince = now;
    changed = true;
  } else if (current.near && current.prox < resting + kFarBelow) {
    current.near = false;
    waved = now - nearSince <= kWaveMs;
    changed = true;
  }
  // Nothing near: the resting echo follows, down quickly, up slowly (over
  // minutes, a moved robot, a wall), so an approaching hand doesn't become it.
  if (!current.near) resting = current.prox < resting ? resting * 0.9f + current.prox * 0.1f
                                                       : resting * 0.999f + current.prox * 0.001f;
  // A hand that stays for minutes is the new resting level.
  if (current.near && now - nearSince > 120000) {
    resting = current.prox;
    current.near = false;
    changed = true;
  }
  if (now >= nextLux) {
    float moved = fabsf(current.lux - reportedLux);
    if (reportedLux < 0 || moved > max(5.0f, reportedLux * 0.15f)) {
      reportedLux = current.lux;
      nextLux = now + kLuxEveryMs;
      changed = true;
    }
  }
  out = current;
  return changed;
}

const Reading& latest() { return current; }

void raw(uint16_t& ch0, uint16_t& ch1, uint8_t& status, uint16_t& prox, float& rest) {
  ch0 = lastCh0;
  ch1 = lastCh1;
  status = lastStatus;
  prox = current.prox;
  rest = resting;
}

}  // namespace senses
