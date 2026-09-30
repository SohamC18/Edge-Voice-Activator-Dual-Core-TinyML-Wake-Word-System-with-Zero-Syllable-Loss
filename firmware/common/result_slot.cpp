#include "result_slot.h"

namespace kws {

namespace {
/// Full memory barrier. Required around the payload writes: the compiler and
/// both cores must agree that the sequence bump and the float stores are not
/// reordered relative to each other.
inline void barrier() { __atomic_thread_fence(__ATOMIC_SEQ_CST); }
}  // namespace

void ResultSlot::publish(const float* probabilities, float inferenceMs,
                         uint32_t droppedWindows) volatile {
  barrier();
  sequence_ += 1;  // odd => a write is in flight; readers must back off
  barrier();
  for (int index = 0; index < 3; ++index) {
    probabilities_[index] = probabilities[index];
  }
  inferenceMs_ = inferenceMs;
  dropped_ = droppedWindows;
  barrier();
  sequence_ += 1;  // even => stable and safe to read
  barrier();
}

bool ResultSlot::read(float* outProbabilities, float* outInferenceMs,
                      uint32_t* outDropped) const volatile {
  if (sequence_ < 2) {
    return false;  // nothing published yet
  }
  for (;;) {
    const uint32_t before = sequence_;
    if (before & 1u) {
      continue;  // a write is in progress; retry
    }
    barrier();
    for (int index = 0; index < 3; ++index) {
      outProbabilities[index] = probabilities_[index];
    }
    *outInferenceMs = inferenceMs_;
    *outDropped = dropped_;
    barrier();
    if (sequence_ == before) {
      return true;  // the payload we copied was never being rewritten
    }
  }
}

}  // namespace kws
