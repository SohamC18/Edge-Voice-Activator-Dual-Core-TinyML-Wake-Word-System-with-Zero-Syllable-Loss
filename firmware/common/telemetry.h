// Telemetry frame emission for protocol v1 (see docs/protocol.md).
//
// Every frame the dashboard shows is built here. The rule enforced by this
// module: a measurement the build cannot make is OMITTED from the frame, never
// sent as zero. The dashboard renders "unavailable" for an absent field, which
// is honest; a fabricated 0% is not.

#pragma once

#include <WebSocketsClient.h>
#include <stdint.h>

#include "result_slot.h"

namespace kws {

/// Which output index carries which meaning.
///
/// The pre-patch firmware hardcoded index 2 = friday while
/// docs/legacy/kws_model_progress.md documents index 0 = friday. Rather than
/// guess, the mapping is a build-time constant that the device DECLARES in its
/// boot frame, and the dashboard labels series by name. Run
// `python tools/kws_model/get_params.py` to inspect the exported model's
// quantization, then set this to whichever order the interpreter reports.
enum class ClassOrder : uint8_t {
  kSilenceUnknownFriday = 0,  ///< the firmware's original assumption
  kFridayNegativeSilence = 1, ///< the model handoff note's ordering
};

/// FreeRTOS run-time statistics. A false availability flag means the build did
/// not enable `configGENERATE_RUN_TIME_STATS`, and the frame omits the field.
struct SystemHealthSample {
  uint32_t freeHeap = 0;
  uint32_t minFreeHeap = 0;
  uint32_t largestFreeBlock = 0;
  bool cpuIdleAvailable = false;
  float cpuIdlePct = 0.0f;
  bool rssiAvailable = false;
  int32_t rssiDbm = 0;
  uint32_t audioDroppedChunks = 0;
  uint32_t serialOverruns = 0;
};

/// Measured static memory attribution against the 256 KiB budget.
struct RamLedgerSample {
  uint32_t budgetBytes = 256u * 1024u;
  uint32_t tensorArena = 0;
  uint32_t melDb = 0;
  uint32_t pcmRing = 0;
  uint32_t preRollRing = 0;
  uint32_t opusEncoder = 0;
  uint32_t rtosStacks = 0;
};

/// Builds and sends protocol-v1 frames over the device WebSocket.
class TelemetryEmitter {
 public:
  TelemetryEmitter(WebSocketsClient& socket, ClassOrder order)
      : socket_(socket), order_(order) {}

  void beginBoot(const char* firmwareVersion, uint32_t arenaBytes, float inputScale,
                 int inputZeroPoint, float outputScale, int outputZeroPoint);

  void stage(const char* stage, const char* detail = nullptr);
  void gate(float rms, float threshold, bool open, uint32_t windowMs);
  void aiResult(const ResultSlot& slot);
  void decision(bool accepted, const char* reason, float keywordProbability, float threshold,
                uint32_t sinceLastMs, uint32_t debounceMs);
  void systemHealth(const SystemHealthSample& health);
  void ramLedger(const RamLedgerSample& ram);
  void error(const char* code, const char* message);

 private:
  void sendFrame(const char* event, const char* body);
  void appendClassOrder(char* out, size_t capacity) const;

  WebSocketsClient& socket_;
  ClassOrder order_;
  uint32_t sequence_ = 0;
};

}  // namespace kws
