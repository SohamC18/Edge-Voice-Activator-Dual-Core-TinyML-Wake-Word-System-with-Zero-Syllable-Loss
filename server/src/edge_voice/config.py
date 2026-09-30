"""Runtime configuration, sourced from environment variables.

Every knob has a default that matches the shipped firmware so a fresh clone
runs with zero configuration. Set ``EV_*`` env vars to retune without editing
code — in particular during a live demo, where you do not want to re-flash.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

KIB = 1024

#: Whole-system RAM budget the SIH problem statement imposes on the device.
DEVICE_RAM_BUDGET_BYTES = 256 * KIB

#: Names the model owner documented for the three output indices. The firmware
#: asserts its own order at boot and it wins; this is only the fallback used to
#: label probabilities before a ``boot`` frame has arrived.
FALLBACK_CLASS_ORDER: tuple[str, ...] = ("silence", "unknown", "friday")


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class RelayConfig:
    """Transport-level settings for the ESP32 <-> browser fan-out relay."""

    host: str
    port: int
    device_path: str
    observer_path: str
    heartbeat_interval_s: float
    device_stale_after_s: float
    max_observer_clients: int

    @classmethod
    def from_env(cls) -> RelayConfig:
        return cls(
            host=_env_str("EV_HOST", "0.0.0.0"),  # noqa: S104 - the device is on the LAN
            port=_env_int("EV_PORT", 8765),
            device_path=_env_str("EV_DEVICE_PATH", "/device"),
            observer_path=_env_str("EV_OBSERVER_PATH", "/ws"),
            heartbeat_interval_s=_env_float("EV_HEARTBEAT_INTERVAL_S", 2.0),
            device_stale_after_s=_env_float("EV_DEVICE_STALE_AFTER_S", 5.0),
            max_observer_clients=_env_int("EV_MAX_OBSERVERS", 32),
        )


@dataclass(frozen=True, slots=True)
class AsrConfig:
    """Speech-recognition settings for the command-utterance transcript.

    ``engine`` is one of ``faster-whisper``, ``openai-whisper`` or ``none``.
    ``none`` disables ASR entirely: the relay still runs and every other panel
    works, the transcript panel just reports that no engine is loaded. That is
    the right setting on a machine that cannot download a Whisper model.
    """

    engine: str
    model_size: str
    compute_type: str
    device: str
    language: str
    preroll_ms: int
    utterance_timeout_s: float
    min_utterance_ms: int
    model_path: Path | None

    @classmethod
    def from_env(cls) -> AsrConfig:
        raw_path = _env_str("EV_ASR_MODEL_PATH", "")
        return cls(
            engine=_env_str("EV_ASR_ENGINE", "faster-whisper"),
            model_size=_env_str("EV_ASR_MODEL_SIZE", "tiny.en"),
            compute_type=_env_str("EV_ASR_COMPUTE_TYPE", "int8"),
            device=_env_str("EV_ASR_DEVICE", "cpu"),
            language=_env_str("EV_ASR_LANGUAGE", "en"),
            preroll_ms=_env_int("EV_ASR_PREROLL_MS", 300),
            utterance_timeout_s=_env_float("EV_ASR_UTTERANCE_TIMEOUT_S", 1.2),
            min_utterance_ms=_env_int("EV_ASR_MIN_UTTERANCE_MS", 180),
            model_path=Path(raw_path) if raw_path else None,
        )


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Top-level configuration bundle passed down to the services."""

    relay: RelayConfig
    asr: AsrConfig
    ram_budget_bytes: int
    log_telemetry_to_stdout: bool
    legacy_text_fallback: bool

    @classmethod
    def from_env(cls) -> AppConfig:
        return cls(
            relay=RelayConfig.from_env(),
            asr=AsrConfig.from_env(),
            ram_budget_bytes=_env_int("EV_RAM_BUDGET_BYTES", DEVICE_RAM_BUDGET_BYTES),
            log_telemetry_to_stdout=_env_bool("EV_LOG_TELEMETRY", True),
            # The un-flashed firmware only speaks ``[AI] Sil: 0% | ...`` strings.
            # Keeping a parser for them means the dashboard degrades gracefully
            # instead of showing an empty screen during a rehearsal.
            legacy_text_fallback=_env_bool("EV_LEGACY_TEXT_FALLBACK", True),
        )
