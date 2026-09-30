"""Tests for pre-roll assembly.

The pre-roll buffer is the only mechanism backing the "no clipped words" claim,
so these tests are deliberately strict about the failure cases: a pre-roll
block we cannot vouch for must demote the claim rather than let it through.
"""

from __future__ import annotations

import numpy as np
import pytest

from edge_voice.asr.preroll import (
    FRAME_DURATION_MS,
    PREROLL_MISSING,
    PREROLL_OUT_OF_ORDER,
    PREROLL_TOO_SHORT,
    SAMPLE_RATE_HZ,
    SAMPLES_PER_OPUS_FRAME,
    UtteranceBuffer,
    decode_opus,
)
from edge_voice.asr.service import pcm_to_float32

OPUS_FRAME = b"\xfc" + bytes(range(1, 39))  # 40 bytes, a structurally valid packet

#: 0xFF is an invalid Opus TOC configuration code, so libopus rejects it
#: outright rather than silently decoding it as silence.
CORRUPT_FRAME = b"\xff" + b"not-an-opus-packet" * 2


def build(preroll: int, live: int, *, first_flag: bool = True) -> UtteranceBuffer:
    buffer = UtteranceBuffer(utterance_id=7)
    for index in range(preroll):
        buffer.add_preroll(OPUS_FRAME, is_first=first_flag and index == 0)
    for _ in range(live):
        buffer.add_live(OPUS_FRAME)
    return buffer


class TestPrerollAssessment:
    def test_accepts_a_sufficient_markerless_free_pre_roll(self) -> None:
        buffer = build(preroll=10, live=5)

        buffer.finalise(minimum_preroll_ms=200)
        assert buffer.pre_roll_complete is True
        assert buffer.preroll_issue is None

    def test_rejects_an_utterance_with_no_pre_roll_at_all(self) -> None:
        buffer = build(preroll=0, live=5)

        buffer.finalise(minimum_preroll_ms=200)
        assert buffer.pre_roll_complete is False
        assert buffer.preroll_issue == PREROLL_MISSING

    def test_rejects_a_pre_roll_block_missing_its_first_marker(self) -> None:
        """Without the marker we cannot know the device did not clip itself."""
        buffer = build(preroll=10, live=5, first_flag=False)

        buffer.finalise(minimum_preroll_ms=200)
        assert buffer.pre_roll_complete is False
        assert buffer.preroll_issue == PREROLL_OUT_OF_ORDER

    def test_rejects_a_pre_roll_block_shorter_than_configured(self) -> None:
        buffer = build(preroll=2, live=20)

        buffer.finalise(minimum_preroll_ms=200)
        assert buffer.pre_roll_complete is False
        assert buffer.preroll_issue == PREROLL_TOO_SHORT

    def test_boundary_is_exactly_the_configured_minimum(self) -> None:
        exact = build(preroll=10, live=0)  # 10 * 20 ms = 200 ms
        exact.finalise(minimum_preroll_ms=200)
        assert exact.pre_roll_complete is True

        one_short = build(preroll=9, live=0)
        one_short.finalise(minimum_preroll_ms=200)
        assert one_short.pre_roll_complete is False


class TestFrameOrdering:
    def test_preroll_precedes_live_audio(self) -> None:
        buffer = UtteranceBuffer(utterance_id=1)
        buffer.add_preroll(b"pre1", is_first=True)
        buffer.add_preroll(b"pre2", is_first=False)
        buffer.add_live(b"live1")

        assert buffer.ordered_frames() == [b"pre1", b"pre2", b"live1"]

    def test_reports_preroll_and_total_durations_in_milliseconds(self) -> None:
        buffer = build(preroll=15, live=25)

        assert buffer.preroll_duration_ms() == 15 * FRAME_DURATION_MS
        assert buffer.duration_ms() == 40 * FRAME_DURATION_MS

    def test_frame_duration_matches_the_20ms_opus_packet(self) -> None:
        assert FRAME_DURATION_MS == 20
        assert SAMPLES_PER_OPUS_FRAME * 1000 // 16_000 == FRAME_DURATION_MS


class TestDecode:
    def test_round_trips_pcm_through_a_real_opus_stream(self) -> None:
        """Decode real Opus packets and check real audio comes back.

        This is the only test that would catch a silently broken decoder, and
        the transcript panel is worthless without one. Fixtures are generated
        through PyAV's muxer rather than checked in, so the test cannot rot
        against a stale .opus file.
        """
        pytest.importorskip("av")
        source = speech_like_tone()
        packets = encode_opus_packets(source)

        decoded = decode_opus(packets)
        assert decoded, "decoder returned no PCM"

        recovered = pcm_to_float32(decoded)
        assert recovered.shape[0] == pytest.approx(source.shape[0], rel=0.15)
        assert float(np.max(np.abs(recovered))) > 0.1, "decoded audio is effectively silent"
        assert dominant_frequency_hz(recovered) == pytest.approx(440.0, abs=60.0)

    def test_decodes_the_pre_roll_and_live_blocks_as_one_stream(self) -> None:
        """Pre-roll and live frames must concatenate into a single waveform."""
        pytest.importorskip("av")
        source = speech_like_tone(duration_ms=600)
        packets = encode_opus_packets(source)
        preroll_count = len(encode_opus_packets(speech_like_tone(duration_ms=200)))

        buffer = UtteranceBuffer(utterance_id=3)
        for index, packet in enumerate(packets):
            if index < preroll_count:
                buffer.add_preroll(packet, is_first=index == 0)
            else:
                buffer.add_live(packet)
        buffer.finalise(minimum_preroll_ms=200)

        assert buffer.pre_roll_complete is True
        assert len(buffer.ordered_frames()) == len(packets)
        assert len(buffer.decode()) > 0

    def test_raises_a_typed_error_on_corrupt_input(self) -> None:
        from edge_voice.asr.preroll import OpusDecoderError

        pytest.importorskip("av")
        with pytest.raises(OpusDecoderError):
            decode_opus([CORRUPT_FRAME])

    def test_empty_input_decodes_to_nothing_rather_than_raising(self) -> None:
        assert decode_opus([]) == b""

    def test_a_decode_failure_demotes_an_otherwise_complete_preroll(self) -> None:
        """A transcript from misordered audio is exactly the clipping we claim to prevent."""
        from edge_voice.asr.preroll import PREROLL_DECODE_FAILED, OpusDecoderError

        buffer = UtteranceBuffer(utterance_id=7)
        for index in range(10):
            buffer.add_preroll(CORRUPT_FRAME, is_first=index == 0)
        for _ in range(5):
            buffer.add_live(CORRUPT_FRAME)
        buffer.finalise(minimum_preroll_ms=200)
        assert buffer.pre_roll_complete is True

        with pytest.raises(OpusDecoderError):
            buffer.decode()
        assert buffer.pre_roll_complete is False
        assert buffer.preroll_issue == PREROLL_DECODE_FAILED

    def test_a_missing_decoder_reports_an_actionable_error(self) -> None:
        """A demo box without `av` must say so, not raise ImportError."""
        from edge_voice.asr.preroll import OpusDecoderError, load_av

        try:
            load_av()
        except OpusDecoderError as exc:
            assert "pip install av" in str(exc)
        else:  # pragma: no cover - only on a fully provisioned machine
            pytest.skip("PyAV is installed, so the missing-dependency path cannot run")


def speech_like_tone(*, duration_ms: int = 1000, freq_hz: float = 440.0) -> np.ndarray:
    """An amplitude-modulated tone: closer to speech than a pure sine, and it
    has an unambiguous spectral peak so the decode can be checked for identity
    rather than just non-silence."""
    total = int(SAMPLE_RATE_HZ * duration_ms / 1000)
    t = np.arange(total) / SAMPLE_RATE_HZ
    envelope = 0.6 + 0.4 * np.sin(2 * np.pi * 3.0 * t)
    return (envelope * np.sin(2 * np.pi * freq_hz * t) * 8000).astype(np.int16)


def encode_opus_packets(samples: np.ndarray) -> list[bytes]:
    """Encode mono int16 samples to raw Opus packets via PyAV's muxer."""
    import io
    from fractions import Fraction

    import av

    container = io.BytesIO()
    with av.open(container, mode="w", format="ogg") as sink:
        stream = sink.add_stream("libopus", rate=16_000)
        stream.layout = "mono"
        frame = av.AudioFrame.from_ndarray(
            samples.reshape(1, -1), format="s16", layout="mono"
        )
        frame.sample_rate = 16_000
        frame.pts = 0
        frame.time_base = Fraction(1, 16_000)
        for packet in stream.encode(frame):
            sink.mux(packet)

    with av.open(io.BytesIO(container.getvalue())) as source:
        return [bytes(packet) for packet in source.demux(audio=0) if packet.size]


def dominant_frequency_hz(samples: np.ndarray) -> float:
    """Crude spectral peak via zero-crossing rate — no numpy FFT dependency.

    ``f = crossings * fs / (2N)`` because a sine crosses zero twice per period.
    The diffs must be *counted* (absolute value), not summed: a plain sum
    cancels out and reports a frequency of nearly zero.
    """
    sign_changes = np.abs(np.diff(np.signbit(samples).astype(np.int8))).sum()
    return float(sign_changes) * SAMPLE_RATE_HZ / (2 * samples.shape[0])
