#include "telemetry.h"

#include <Arduino.h>
#include <stdio.h>
#include <string.h>
#include <sys/time.h>

namespace kws {

namespace {

/// Buffer sized for the largest frame the protocol allows. The `ram` frame is
/// the worst case: seven segment fields plus a full envelope. Truncation is
/// impossible at this size, and the host relay would reject a malformed frame
/// anyway -- better to fail loudly here than silently ship a partial one.
constexpr size_t kFrameCapacity = 320;

const char* classNameForIndex(ClassOrder order, uint8_t index) {
  switch (order) {
    case ClassOrder::kFridayNegativeSilence:
      static const char* kDocOrder[3] = {"friday", "negative", "silence"};
      return kDocOrder[index < 3 ? index : 0];
    case ClassOrder::kSilenceUnknownFriday:
    default:
      static const char* kFirmwareOrder[3] = {"silence", "unknown", "friday"};
      return kFirmwareOrder[index < 3 ? index : 0];
  }
}

}  // namespace

void TelemetryEmitter::appendClassOrder(char* out, size_t capacity) const {
  snprintf(out, capacity, "\"%s\",\"%s\",\"%s\"", classNameForIndex(order_, 0),
           classNameForIndex(order_, 1), classNameForIndex(order_, 2));
}

void TelemetryEmitter::sendFrame(const char* event, const char* body) {
  if (!socket_.isConnected()) {
    return;  // telemetry is best-effort; never stall the DSP on a dead socket
  }
  struct timeval now {};
  gettimeofday(&now, nullptr);
  const uint64_t wallMs = static_cast<uint64_t>(now.tv_sec) * 1000ull + now.tv_usec / 1000ull;

  char frame[kFrameCapacity];
  const int written =
      snprintf(frame, sizeof(frame), "{\"v\":1,\"ev\":\"%s\",\"seq\":%u,\"t\":%lu,\"wall\":%llu%s%s}",
               event, ++sequence_, static_cast<unsigned long>(millis()),
               static_cast<unsigned long long>(wallMs), body[0] == 0 ? "" : ",", body);
  if (written > 0 && written < static_cast<int>(sizeof(frame))) {
    socket_.sendTXT(frame);
  }
}

void TelemetryEmitter::beginBoot(const char* firmwareVersion, uint32_t arenaBytes, float inputScale,
                                 int inputZeroPoint, float outputScale, int outputZeroPoint) {
  char classes[96];
  appendClassOrder(classes, sizeof(classes));

  char body[224];
  snprintf(body, sizeof(body),
           ",\"fw\":\"%s\",\"chip\":\"%s\",\"arena_bytes\":%u,\"class_order\":[%s],"
           "\"quant\":{\"input_scale\":%.10f,\"input_zero_point\":%d,\"output_scale\":%.8f,"
           "\"output_zero_point\":%d},\"free_heap\":%u,\"flash_used\":%u,\"sdk\":\"%s\"",
           firmwareVersion, ESP.getChipModel(), arenaBytes, classes, inputScale, inputZeroPoint,
           outputScale, outputZeroPoint, ESP.getFreeHeap(),
           static_cast<uint32_t>(ESP.getSketchSize()), ESP.getSdkVersion());
  sendFrame("boot", body);
}

void TelemetryEmitter::stage(const char* stage, const char* detail) {
  char body[160];
  if (detail != nullptr) {
    snprintf(body, sizeof(body), ",\"stage\":\"%s\",\"detail\":\"%s\"", stage, detail);
  } else {
    snprintf(body, sizeof(body), ",\"stage\":\"%s\"", stage);
  }
  sendFrame("stage", body);
}

void TelemetryEmitter::gate(float rms, float threshold, bool open, uint32_t windowMs) {
  char body[96];
  snprintf(body, sizeof(body), ",\"rms\":%.1f,\"threshold\":%.1f,\"open\":%s,\"window_ms\":%u", rms,
           threshold, open ? "true" : "false", windowMs);
  sendFrame("gate", body);
}

void TelemetryEmitter::aiResult(const ResultSlot& slot) {
  float probabilities[3] = {0.0f, 0.0f, 0.0f};
  float inferenceMs = 0.0f;
  uint32_t dropped = 0;
  if (!slot.read(probabilities, &inferenceMs, &dropped)) {
    return;  // no inference completed yet; an all-zero frame would be a lie
  }
  char body[128];
  snprintf(body, sizeof(body),
           ",\"p\":[%.4f,%.4f,%.4f],\"inference_ms\":%.1f,\"arena_bytes\":%u,\"dropped\":%u",
           probabilities[0], probabilities[1], probabilities[2], inferenceMs,
           static_cast<uint32_t>(TENSOR_ARENA_SIZE), dropped);
  sendFrame("ai_result", body);
}

void TelemetryEmitter::decision(bool accepted, const char* reason, float keywordProbability,
                                float threshold, uint32_t sinceLastMs, uint32_t debounceMs) {
  char body[176];
  snprintf(body, sizeof(body),
           ",\"accept\":%s,\"reason\":\"%s\",\"keyword_prob\":%.4f,\"threshold\":%.4f,"
           "\"since_last_ms\":%u,\"debounce_ms\":%u",
           accepted ? "true" : "false", reason, keywordProbability, threshold, sinceLastMs,
           debounceMs);
  sendFrame("decision", body);
}

void TelemetryEmitter::systemHealth(const SystemHealthSample& health) {
  // Fields are appended conditionally. `cpu_idle_pct` and `rssi` are only
  // present when the build can actually measure them.
  char body[288];
  int written = snprintf(body, sizeof(body),
                         ",\"free_heap\":%u,\"min_free_heap\":%u,\"largest_free\":%u",
                         health.freeHeap, health.minFreeHeap, health.largestFreeBlock);
  if (health.cpuIdleAvailable) {
    written += snprintf(body + written, sizeof(body) - written, ",\"cpu_idle_pct\":%.1f",
                        health.cpuIdlePct);
  }
  if (health.rssiAvailable) {
    written += snprintf(body + written, sizeof(body) - written, ",\"rssi\":%d", health.rssiDbm);
  }
  if (written > 0 && written < static_cast<int>(sizeof(body))) {
    snprintf(body + written, sizeof(body) - written,
             ",\"uptime_ms\":%lu,\"audio_dropped_chunks\":%u,\"serial_overruns\":%u",
             static_cast<unsigned long>(millis()), health.audioDroppedChunks,
             health.serialOverruns);
  }
  sendFrame("sys", body);
}

void TelemetryEmitter::ramLedger(const RamLedgerSample& ram) {
  char body[256];
  snprintf(body, sizeof(body),
           ",\"budget_bytes\":%u,\"segments\":{\"tensor_arena\":%u,\"mel_db\":%u,\"pcm_ring\":%u,"
           "\"preroll_ring\":%u,\"opus_encoder\":%u,\"rtos_stacks\":%u,\"other\":0}",
           ram.budgetBytes, ram.tensorArena, ram.melDb, ram.pcmRing, ram.preRollRing,
           ram.opusEncoder, ram.rtosStacks);
  sendFrame("ram", body);
}

void TelemetryEmitter::error(const char* code, const char* message) {
  char body[160];
  snprintf(body, sizeof(body), ",\"code\":\"%s\",\"message\":\"%s\"", code, message);
  sendFrame("error", body);
}

}  // namespace kws
