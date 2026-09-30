"""Adapter for the pre-telemetry firmware.

The shipped ``main.ino`` before the v1 patch emits human-readable strings only::

    [AI] Sil: 12% | Unk: 08% | Fri: 80%
    [IDLE] Audio too quiet (RMS < 300)
    [WARN] AI Core Busy, dropping frame
    FRIDAY TRIGGERED!

None of that carries a timestamp, an RMS value or a gate state, so the
Activation Log and RAM Ledger cannot be honestly populated from it. This module
translates those strings into well-formed v1 frames anyway, so an un-flashed
board still drives the dashboard instead of showing a blank screen during a
rehearsal, and so the team can see exactly which panels are degraded.

Frames produced here are tagged ``"synthetic": True`` in the body; the dashboard
badges anything synthetic rather than passing it off as measured.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any, Final

from edge_voice.protocol import PROTOCOL_VERSION, DeviceFrame

#: ``[AI] Sil: 12% | Unk: 08% | Fri: 80%``
_AI_SCORES = re.compile(
    r"^\[AI\]\s*Sil:\s*(?P<sil>\d+(?:\.\d+)?)%\s*\|\s*Unk:\s*(?P<unk>\d+(?:\d+)?)%\s*"
    r"\|\s*Fri:\s*(?P<fri>\d+(?:\.\d+)?)%",
    re.IGNORECASE,
)

_IDLE_GATE = re.compile(r"^\[IDLE\].*RMS\s*<\s*(?P<threshold>\d+)", re.IGNORECASE)
_AI_BUSY = re.compile(r"^\[WARN\].*(busy|dropping)", re.IGNORECASE)
_TRIGGERED = re.compile(r"FRIDAY\s+TRIGGERED", re.IGNORECASE)

#: The legacy string always lists silence, unknown, friday in that order.
LEGACY_CLASS_ORDER: Final[tuple[str, ...]] = ("silence", "unknown", "friday")


def translate_legacy_text(
    wire_text: str,
    *,
    next_seq: Callable[[], int] | None = None,
    now_ms: int | None = None,
) -> DeviceFrame | None:
    """Convert one legacy firmware string into a v1 frame.

    Returns ``None`` for anything unrecognised so the relay can log-and-drop
    instead of crashing on a stray serial banner.
    """
    line = wire_text.strip()
    if not line:
        return None

    allocate_seq = next_seq or _wall_clock_seq
    uptime_ms = now_ms if now_ms is not None else int(time.monotonic() * 1000)
    wall_ms = int(time.time() * 1000)

    if (match := _AI_SCORES.match(line)) is not None:
        probabilities = (
            float(match.group("sil")) / 100.0,
            float(match.group("unk")) / 100.0,
            float(match.group("fri")) / 100.0,
        )
        return _frame(
            event="ai_result",
            seq=allocate_seq(),
            uptime_ms=uptime_ms,
            wall_ms=wall_ms,
            body={
                "p": [round(value, 4) for value in probabilities],
                "inference_ms": 0.0,
                "arena_bytes": 0,
                "dropped": 0,
                "synthetic": True,
                "note": "no inference timing in the legacy protocol",
            },
        )

    if (match := _IDLE_GATE.match(line)) is not None:
        threshold = float(match.group("threshold"))
        return _frame(
            event="gate",
            seq=allocate_seq(),
            uptime_ms=uptime_ms,
            wall_ms=wall_ms,
            body={
                "rms": 0.0,
                "threshold": threshold,
                "open": False,
                "window_ms": 600,
                "synthetic": True,
                "note": "legacy firmware reports only that the gate stayed closed",
            },
        )

    if _AI_BUSY.match(line) is not None:
        return _frame(
            event="decision",
            seq=allocate_seq(),
            uptime_ms=uptime_ms,
            wall_ms=wall_ms,
            body={
                "accept": False,
                "reason": "ai_busy",
                "keyword_prob": 0.0,
                "threshold": 0.5,
                "since_last_ms": 0,
                "debounce_ms": 2000,
                "synthetic": True,
            },
        )

    if _TRIGGERED.search(line) is not None:
        return _frame(
            event="decision",
            seq=allocate_seq(),
            uptime_ms=uptime_ms,
            wall_ms=wall_ms,
            body={
                "accept": True,
                "reason": "friday>=thr",
                "keyword_prob": 1.0,
                "threshold": 0.5,
                "since_last_ms": 0,
                "debounce_ms": 2000,
                "synthetic": True,
                "note": "legacy firmware does not report the triggering probability",
            },
        )

    return None


def _wall_clock_seq() -> int:
    """Sequence source for legacy frames.

    The legacy protocol has no counter of its own, so a millisecond clock is
    the best available monotonic-ish key. It is not strictly increasing under
    load, which is exactly why the protocol upgrade added a real ``seq``.
    """
    return int(time.monotonic() * 1000)


def _frame(*, event: str, seq: int, uptime_ms: int, wall_ms: int, body: dict[str, Any]) -> DeviceFrame:
    """Build a :class:`DeviceFrame` with a synthesised ``raw`` rendering."""
    import json

    payload = {"v": PROTOCOL_VERSION, "ev": event, "seq": seq, "t": uptime_ms, "wall": wall_ms, **body}
    return DeviceFrame(
        version=PROTOCOL_VERSION,
        event=event,
        seq=seq,
        uptime_ms=uptime_ms,
        wall_ms=wall_ms,
        body=body,
        raw=json.dumps(payload, separators=(",", ":")),
    )
