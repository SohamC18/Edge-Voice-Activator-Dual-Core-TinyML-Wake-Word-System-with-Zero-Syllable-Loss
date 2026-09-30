"""Shared fixtures for the relay test-suite."""

from __future__ import annotations

from dataclasses import replace

import pytest

from edge_voice.asr.service import NullAsrEngine, Transcript
from edge_voice.config import AppConfig, AsrConfig, RelayConfig
from edge_voice.relay import TelemetryRelay


def make_config(base: AppConfig, **relay_overrides: object) -> AppConfig:
    """Clone an :class:`AppConfig` with individual ``RelayConfig`` fields changed.

    ``RelayConfig`` is a slotted frozen dataclass, so ``__dict__`` is not
    available; ``dataclasses.replace`` is the supported way to derive one.
    """
    return replace(base, relay=replace(base.relay, **relay_overrides))


@pytest.fixture
def relay_config() -> AppConfig:
    """A config with a fast heartbeat so liveness tests do not sleep for 2 s.

    ``device_stale_after_s`` is kept generous: the fake device in these tests
    does not answer pings, so a tight stale window would emit ``device_status``
    frames that race unrelated assertions. The stale-detection test builds its
    own relay with a deliberately tiny window.
    """
    return AppConfig(
        relay=RelayConfig(
            host="127.0.0.1",
            port=0,
            device_path="/device",
            observer_path="/ws",
            heartbeat_interval_s=0.05,
            device_stale_after_s=5.0,
            max_observer_clients=4,
        ),
        asr=_asr_config(),
        ram_budget_bytes=256 * 1024,
        log_telemetry_to_stdout=False,
        legacy_text_fallback=True,
    )


def _asr_config() -> AsrConfig:
    return AsrConfig(
        engine="none",
        model_size="tiny.en",
        compute_type="int8",
        device="cpu",
        language="en",
        preroll_ms=100,
        utterance_timeout_s=0.1,
        min_utterance_ms=0,
        model_path=None,
    )


class StubAsrEngine:
    """Returns a canned transcript and records what PCM it was handed."""

    def __init__(self, transcript: Transcript | None = None) -> None:
        self.transcript = transcript or Transcript(
            text="play some jazz",
            first_word="play",
            first_word_ms=180.0,
            confidence=0.93,
            engine="stub",
            model="stub",
        )
        self.received_pcm: list[bytes] = []
        self.calls = 0

    @property
    def available(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return "stub"

    def transcribe(self, pcm_s16le: bytes) -> Transcript:
        self.calls += 1
        self.received_pcm.append(pcm_s16le)
        return self.transcript


@pytest.fixture
def stub_asr() -> StubAsrEngine:
    return StubAsrEngine()


@pytest.fixture
def relay(relay_config: AppConfig, stub_asr: StubAsrEngine) -> TelemetryRelay:
    return TelemetryRelay(relay_config, stub_asr)


@pytest.fixture
def no_asr_relay(relay_config: AppConfig) -> TelemetryRelay:
    return TelemetryRelay(relay_config, NullAsrEngine())


def boot_frame_text(**overrides: object) -> str:
    """Build a well-formed ``boot`` frame body for tests."""
    payload: dict[str, object] = {
        "fw": "edge-kws/1.0.0",
        "chip": "ESP32-D0WDQ6",
        "arena_bytes": 30720,
        "class_order": ["silence", "unknown", "friday"],
        "quant": {"input_scale": 0.0078431377, "input_zero_point": -128},
        "free_heap": 142336,
        "flash_used": 1088128,
        "sdk": "v4.4.7",
    }
    payload.update(overrides)
    return _envelope("boot", payload)


def _envelope(event: str, body: dict[str, object], *, seq: int = 1) -> str:
    import json

    return json.dumps(
        {"v": 1, "ev": event, "seq": seq, "t": 1000, "wall": 1756000000000, **body},
        separators=(",", ":"),
    )


def ai_result_text(p: list[float], *, seq: int = 2, **overrides: object) -> str:
    body: dict[str, object] = {
        "p": p,
        "inference_ms": 41.0,
        "arena_bytes": 30720,
        "dropped": 0,
    }
    body.update(overrides)
    return _envelope("ai_result", body, seq=seq)


def gate_text(rms: float, *, seq: int = 3, threshold: float = 300.0) -> str:
    return _envelope(
        "gate",
        {"rms": rms, "threshold": threshold, "open": rms > threshold, "window_ms": 600},
        seq=seq,
    )


def decision_text(accept: bool, *, seq: int = 4, **overrides: object) -> str:
    body: dict[str, object] = {
        "accept": accept,
        "reason": "friday>=thr" if accept else "friday<thr",
        "keyword_prob": 0.87 if accept else 0.11,
        "threshold": 0.5,
        "since_last_ms": 4100,
        "debounce_ms": 2000,
    }
    body.update(overrides)
    return _envelope("decision", body, seq=seq)
