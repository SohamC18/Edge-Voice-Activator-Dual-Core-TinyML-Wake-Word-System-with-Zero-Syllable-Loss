// Cross-core result handoff.
//
// The pre-patch firmware published three `volatile float` results from the AI
// task on core 0 and read them from `loop()` on core 1. On the Xtensa LX6 a
// 32-bit float store is NOT atomic, so the reader could observe a torn set --
// e.g. Sil 62% | Unk 07% | Fri 100% -- which is instantly visible on the
// dashboard and destroys the credibility of every other number on screen.
//
// A seqlock fixes it without disabling interrupts or adding a mutex: the writer
// bumps a sequence counter to an odd value, writes, then bumps it to an even
// value. The reader retries if the counter changed or was odd while reading.
//
// Cost: the reader spins for the duration of three float stores (a handful of
// cycles). Benefit: no probability vector can ever fail to sum to 1, which is
// precisely the invariant the host relay checks in
// `protocol.py::_validate_ai_result`.

#pragma once

#include <stdint.h>

namespace kws {

/// Publishes a 3-class probability vector plus timing from the AI task to loop().
class ResultSlot {
 public:
  /// @param probabilities Softmax output in wire (index) order, not named order.
  /// @param inferenceMs  Wall time of the forward pass, for the dashboard.
  void publish(const float* probabilities, float inferenceMs, uint32_t droppedWindows) volatile;

  /// Copy the most recent stable result. Returns false if nothing has been
  /// published yet, so the caller must not render an all-zero frame: an
  /// all-zero vector would be indistinguishable from real silence.
  bool read(float* outProbabilities, float* outInferenceMs, uint32_t* outDropped) const volatile;

  /// True when a result has been published at least once.
  bool hasResult() const volatile { return sequence_ >= 2; }

  /// Total number of analysis windows dropped because the AI core was still busy.
  uint32_t droppedWindows() const volatile { return dropped_; }

 private:
  // Written by the AI task (core 0), read by loop() (core 1).
  volatile uint32_t sequence_ = 0;
  volatile float probabilities_[3] = {0.0f, 0.0f, 0.0f};
  volatile float inferenceMs_ = 0.0f;
  volatile uint32_t dropped_ = 0;
};

}  // namespace kws
