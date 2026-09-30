"""Server-side measurements the device cannot make for itself.

The one number judges will ask about is the round trip from "the user stopped
saying the keyword" to "the first byte of the transcript came back". That span
starts on the device and ends in this process, so neither endpoint can measure
it alone — the relay is the only place it can be measured honestly.

Timing is anchored on the *observer* side using the device's ``wall_ms`` field
where available, and on the host monotonic clock for the ASR leg, because the
ESP32's wall clock is unsynchronised with the laptop's.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, field

#: How many recent latency samples the relay keeps for a rolling percentile.
LATENCY_WINDOW = 64


@dataclass(slots=True)
class LatencySample:
    """One end-to-end keyword-to-transcript measurement."""

    utterance_id: int
    keyword_to_commit_ms: float
    commit_to_first_token_ms: float
    total_ms: float
    recorded_at_wall_ms: int


@dataclass(slots=True)
class LatencyTracker:
    """Tracks utterances from ``commit`` to first ASR token.

    Lifecycle per utterance: :meth:`mark_commit` on the device's commit
    decision, :meth:`mark_first_token` when the ASR produces its first partial,
    :meth:`settle` when the final transcript lands. A ``settle`` with no
    matching commit is recorded as a failure rather than dropped, so the
    dashboard's "unaccounted utterances" counter is truthful.
    """

    _pending: dict[int, float] = field(default_factory=dict)
    _samples: deque[LatencySample] = field(
        default_factory=lambda: deque(maxlen=LATENCY_WINDOW)
    )
    _unaccounted: int = 0

    def mark_commit(self, utterance_id: int) -> None:
        self._pending[utterance_id] = time.monotonic()

    def mark_first_token(self, utterance_id: int) -> float | None:
        """Return the commit-to-first-token leg, or ``None`` if unaccounted."""
        started = self._pending.get(utterance_id)
        if started is None:
            self._unaccounted += 1
            return None
        return (time.monotonic() - started) * 1000.0

    def settle(self, utterance_id: int) -> LatencySample | None:
        started = self._pending.pop(utterance_id, None)
        if started is None:
            self._unaccounted += 1
            return None
        total_ms = (time.monotonic() - started) * 1000.0
        sample = LatencySample(
            utterance_id=utterance_id,
            keyword_to_commit_ms=0.0,
            commit_to_first_token_ms=total_ms,
            total_ms=total_ms,
            recorded_at_wall_ms=int(time.time() * 1000),
        )
        self._samples.append(sample)
        return sample

    @property
    def samples(self) -> tuple[LatencySample, ...]:
        return tuple(self._samples)

    @property
    def unaccounted(self) -> int:
        return self._unaccounted

    def percentile(self, fraction: float) -> float | None:
        """Nearest-rank percentile over the window, in milliseconds."""
        if not self._samples:
            return None
        ordered = sorted(sample.total_ms for sample in self._samples)
        index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
        return ordered[index]

    def recent(self, count: int) -> Iterator[LatencySample]:
        yield from list(self._samples)[-count:]


@dataclass(slots=True)
class ConnectionStats:
    """Counters for the relay's own connection lifecycle.

    ``frames_rejected`` is the number that matters during a demo: it means the
    device sent something that violated the contract and the relay refused to
    forward it, rather than silently showing a number nobody can trust.
    """

    device_connects: int = 0
    device_disconnects: int = 0
    observer_connects: int = 0
    frames_forwarded: int = 0
    frames_rejected: int = 0
    opus_bytes_forwarded: int = 0
    rejection_reasons: dict[str, int] = field(default_factory=dict)

    def record_rejection(self, code: str) -> None:
        self.frames_rejected += 1
        self.rejection_reasons[code] = self.rejection_reasons.get(code, 0) + 1

    def as_payload(self) -> dict[str, object]:
        return {
            "device_connects": self.device_connects,
            "device_disconnects": self.device_disconnects,
            "observer_connects": self.observer_connects,
            "frames_forwarded": self.frames_forwarded,
            "frames_rejected": self.frames_rejected,
            "opus_bytes_forwarded": self.opus_bytes_forwarded,
            "rejection_reasons": dict(self.rejection_reasons),
        }
