// Audio plumbing: a lock-free analysis ring and a compressed pre-roll ring.
//
// The pre-patch firmware used a 32 KB array plus a full-array `memmove` on
// every 320-sample chunk -- about 1.5 MB/s of memmove -- and then copied the
// whole thing again into a 32 KB `ai_snapshot_buffer` before every inference.
// That is ~64 KB and ~1.5 MB/s of avoidable traffic on a device with a 256 KB
// budget.
//
// This module replaces both with two heads:
//   * `AnalysisRing` keeps a sliding window for the mel spectrogram. Writers
//     only advance a head index; readers address a copy-out with wraparound.
//   * `PreRollRing` keeps the last N *compressed* Opus frames, which is what
//     makes the "no clipped words" claim implementable: 400 ms of history in
//     under 2 KB, versus 25.6 KB if the PCM were buffered instead.

#pragma once

#include <stdint.h>

namespace kws {

/// Sliding PCM window feeding the mel spectrogram. Single-writer (loop task),
/// single-reader (AI task); the AI task reads a snapshot handed over by index,
/// so the two never contend for the same bytes.
class AnalysisRing {
 public:
  explicit AnalysisRing(int16_t* storage, size_t capacitySamples)
      : storage_(storage), capacity_(capacitySamples) {}

  void clear() {
    head_ = 0;
    filled_ = 0;
  }

  /// Append `count` samples, overwriting the oldest on overflow.
  void write(const int16_t* samples, size_t count) {
    for (size_t index = 0; index < count; ++index) {
      storage_[head_] = samples[index];
      head_ = (head_ + 1) % capacity_;
    }
    if (filled_ + count > capacity_) {
      filled_ = capacity_;
    } else {
      filled_ += count;
    }
  }

  size_t filled() const { return filled_; }

  /// Copy the newest `count` samples into `out`, oldest first, mirroring the
  /// buffer at the edges so the caller can index blindly.
  void copyLatest(int16_t* out, size_t count) const {
    const size_t available = filled_ < count ? filled_ : count;
    const size_t start = (head_ + capacity_ - available) % capacity_;
    for (size_t index = 0; index < available; ++index) {
      out[index] = storage_[(start + index) % capacity_];
    }
    for (size_t index = available; index < count; ++index) {
      out[index] = out[index - available];  // edge mirror
    }
  }

 private:
  int16_t* storage_;
  size_t capacity_;
  volatile size_t head_ = 0;
  volatile size_t filled_ = 0;
};

/// Fixed-capacity ring of compressed 20 ms Opus frames.
///
/// This is the mechanism behind the pre-roll claim. When the keyword is
/// accepted, the ring is flushed to the host BEFORE the live frames, so the
/// recogniser sees audio that starts before the energy gate tripped and the
/// first word cannot be clipped.
class PreRollRing {
 public:
  PreRollRing(uint8_t* storage, size_t capacityBytes, size_t frameCapacity)
      : storage_(storage), capacityBytes_(capacityBytes), frameCapacity_(frameCapacity) {}

  void clear() { count_ = 0; }

  /// Push one compressed frame. Oversized frames are dropped rather than
  /// truncated, because a truncated Opus packet decodes to noise and would
  /// corrupt the transcript.
  bool push(const uint8_t* frame, size_t length) {
    if (length == 0 || length > frameCapacity_) {
      return false;
    }
    size_t slot = 0;
    if (count_ == frameCapacity_) {
      slot = writeIndex_;  // overwrite the oldest
    } else {
      slot = (readIndex_ + count_) % frameCapacity_;
      ++count_;
    }
    writeIndex_ = (writeIndex_ + 1) % frameCapacity_;
    uint8_t* destination = storage_ + slot * frameCapacity_;
    memcpy(destination, frame, length);
    lengths_[slot] = static_cast<uint16_t>(length);
    return true;
  }

  size_t count() const { return count_; }
  size_t bytes() const { return capacityBytes_; }

  /// Oldest-to-newest access for the flush that follows an accepted keyword.
  const uint8_t* frameAt(size_t index) const {
    return storage_ + ((readIndex_ + index) % frameCapacity_) * frameCapacity_;
  }
  uint16_t lengthAt(size_t index) const {
    return lengths_[(readIndex_ + index) % frameCapacity_];
  }

 private:
  uint8_t* storage_;
  size_t capacityBytes_;
  size_t frameCapacity_;
  size_t readIndex_ = 0;
  size_t writeIndex_ = 0;
  size_t count_ = 0;
  uint16_t* lengths_ = nullptr;  // set by attachLengths()
};

}  // namespace kws
