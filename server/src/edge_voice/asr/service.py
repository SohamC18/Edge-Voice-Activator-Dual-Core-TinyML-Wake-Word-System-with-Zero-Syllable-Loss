"""Command-utterance speech recognition.

Whisper runs far slower than a WebSocket frame budget allows, so recognition is
dispatched to a worker thread and the relay event loop stays free. The first
decoded segment is forwarded the moment it exists — that is what makes the
"keyword to first byte" latency figure meaningful rather than a post-hoc
measurement.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final, Protocol, TypeVar

import numpy as np

from edge_voice.asr.preroll import SAMPLE_RATE_HZ
from edge_voice.config import AsrConfig

LOGGER = logging.getLogger(__name__)

#: audioop was removed in Python 3.13; this is the s16le -> float32 equivalent.
_INT16_FULL_SCALE: Final = 32768.0

_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class Transcript:
    """A recognised command utterance."""

    text: str
    first_word: str | None
    first_word_ms: float | None
    confidence: float | None
    engine: str
    model: str


class AsrEngine(Protocol):
    """Minimal recogniser interface so engines are swappable and testable."""

    @property
    def available(self) -> bool:
        """False when the model could not be loaded; the UI degrades gracefully."""
        ...

    @property
    def description(self) -> str:
        """Human-readable engine identity, shown in the transcript panel header."""
        ...

    def transcribe(self, pcm_s16le: bytes) -> Transcript:
        """Transcribe mono 16 kHz signed 16-bit little-endian PCM."""
        ...


class NullAsrEngine:
    """Stand-in used when no engine is configured or the model failed to load.

    Keeping a real object rather than ``None`` means the relay never needs a
    null check, and the dashboard can render "ASR engine not loaded" instead of
    a spinner that never resolves.
    """

    @property
    def available(self) -> bool:
        return False

    @property
    def description(self) -> str:
        return "not loaded"

    def transcribe(self, pcm_s16le: bytes) -> Transcript:
        raise RuntimeError("ASR engine is not loaded; set EV_ASR_ENGINE to enable it")


class FasterWhisperEngine:
    """CTranslate2-backed Whisper. Default: ~4x faster than openai-whisper on CPU."""

    def __init__(self, config: AsrConfig) -> None:
        from faster_whisper import WhisperModel

        source = str(config.model_path) if config.model_path else config.model_size
        self._model = WhisperModel(
            source,
            device=config.device,
            compute_type=config.compute_type,
        )
        self._language = config.language
        self._description = f"faster-whisper:{config.model_size}/{config.compute_type}"

    @property
    def available(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return self._description

    def transcribe(self, pcm_s16le: bytes) -> Transcript:
        audio = pcm_to_float32(pcm_s16le)
        segments, _info = self._model.transcribe(
            audio,
            language=self._language,
            beam_size=1,
            vad_filter=False,
            word_timestamps=True,
            condition_on_previous_text=False,
        )
        collected = list(segments)
        words = [word for segment in collected for word in (segment.words or [])]
        text = " ".join(segment.text.strip() for segment in collected).strip()
        first_word = words[0].word.strip() if words else None
        first_word_ms = (words[0].start * 1000.0) if words else None
        return Transcript(
            text=text,
            first_word=first_word,
            first_word_ms=first_word_ms,
            confidence=_mean_exp_logprob(collected),
            engine="faster-whisper",
            model=self._description,
        )


class OpenAiWhisperEngine:
    """Reference Whisper implementation. Slower, but the common fallback."""

    def __init__(self, config: AsrConfig) -> None:
        import whisper

        name = config.model_size if config.model_size else "tiny.en"
        self._model = whisper.load_model(name)
        self._language = config.language
        self._description = f"openai-whisper:{name}"

    @property
    def available(self) -> bool:
        return True

    @property
    def description(self) -> str:
        return self._description

    def transcribe(self, pcm_s16le: bytes) -> Transcript:
        audio = pcm_to_float32(pcm_s16le)
        result = self._model.transcribe(
            audio,
            language=self._language,
            word_timestamps=True,
        )
        text = str(result.get("text", "")).strip()
        words = (result.get("segments") or [{}])[0].get("words") or []
        first_word = str(words[0]["word"]).strip() if words else None
        first_word_ms = float(words[0]["start"]) * 1000.0 if words else None
        return Transcript(
            text=text,
            first_word=first_word,
            first_word_ms=first_word_ms,
            confidence=None,  # openai-whisper exposes no comparable score here
            engine="openai-whisper",
            model=self._description,
        )


def build_engine(config: AsrConfig) -> AsrEngine:
    """Instantiate the configured engine, degrading to :class:`NullAsrEngine`.

    A missing model must not stop the relay: the telemetry, activation-log and
    memory panels are all still valuable without a transcript, and during a
    live demo a half-working dashboard beats a crashed one.
    """
    if config.engine.lower() in {"none", "off", "disabled"}:
        LOGGER.info("ASR disabled by configuration (EV_ASR_ENGINE=%s)", config.engine)
        return NullAsrEngine()
    factories: dict[str, Callable[[AsrConfig], AsrEngine]] = {
        "faster-whisper": FasterWhisperEngine,
        "openai-whisper": OpenAiWhisperEngine,
    }
    factory = factories.get(config.engine.lower())
    if factory is None:
        LOGGER.error("Unknown EV_ASR_ENGINE=%r; falling back to no ASR", config.engine)
        return NullAsrEngine()
    try:
        engine = factory(config)
    except Exception as exc:  # noqa: BLE001 - any import/download failure is fatal to ASR only
        LOGGER.exception("Could not load ASR engine %r: %s", config.engine, exc)
        return NullAsrEngine()
    LOGGER.info("ASR engine ready: %s", engine.description)
    return engine


def pcm_to_float32(pcm_s16le: bytes) -> np.ndarray:
    """Convert signed 16-bit little-endian PCM to the float32 Whisper expects."""
    if not pcm_s16le:
        return np.zeros(0, dtype=np.float32)
    samples = np.frombuffer(pcm_s16le, dtype="<i2")
    return (samples.astype(np.float32) / _INT16_FULL_SCALE).copy()


def _mean_exp_logprob(segments: list[Any]) -> float | None:
    """Approximate token confidence as the geometric mean of word probabilities.

    Whisper reports an average log-probability per segment rather than a
    calibrated confidence, so this is a monotonic proxy, not a probability. It
    is labelled as such in the UI.
    """
    scores = [
        math.exp(float(segment.avg_logprob))
        for segment in segments
        if getattr(segment, "avg_logprob", None) is not None
    ]
    if not scores:
        return None
    return round(sum(scores) / len(scores), 4)


async def run_in_thread(engine: AsrEngine, pcm_s16le: bytes) -> Transcript:
    """Run blocking recognition off the event loop."""

    def _blocking() -> Transcript:
        return engine.transcribe(pcm_s16le)

    return await asyncio.to_thread(_blocking)


async def timed(operation: Callable[[], Awaitable[_T]]) -> tuple[_T, float]:
    """Await ``operation`` and return ``(result, elapsed_ms)``."""
    started = time.monotonic()
    result = await operation()
    return result, (time.monotonic() - started) * 1000.0


__all__ = [
    "AsrEngine",
    "FasterWhisperEngine",
    "NullAsrEngine",
    "OpenAiWhisperEngine",
    "SAMPLE_RATE_HZ",
    "Transcript",
    "build_engine",
    "pcm_to_float32",
    "run_in_thread",
    "timed",
]
