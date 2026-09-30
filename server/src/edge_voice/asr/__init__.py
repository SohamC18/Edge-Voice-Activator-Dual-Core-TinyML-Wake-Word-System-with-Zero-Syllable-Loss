"""Command-utterance ASR: pre-roll assembly, Opus decode and recognition."""

from edge_voice.asr.preroll import (
    PREROLL_DECODE_FAILED,
    PREROLL_MISSING,
    PREROLL_OUT_OF_ORDER,
    PREROLL_TOO_SHORT,
    UtteranceBuffer,
    decode_opus,
)
from edge_voice.asr.service import (
    AsrEngine,
    NullAsrEngine,
    Transcript,
    build_engine,
    pcm_to_float32,
    run_in_thread,
)

__all__ = [
    "AsrEngine",
    "NullAsrEngine",
    "PREROLL_DECODE_FAILED",
    "PREROLL_MISSING",
    "PREROLL_OUT_OF_ORDER",
    "PREROLL_TOO_SHORT",
    "Transcript",
    "UtteranceBuffer",
    "build_engine",
    "decode_opus",
    "pcm_to_float32",
    "run_in_thread",
]
