"""Command-utterance capture: pre-roll assembly and Opus decode.

The claim this module exists to substantiate is *"our pre-roll buffer prevents
clipped words"*. Concretely, the device keeps a short ring of recent compressed
frames; when the energy gate trips and the keyword is accepted, it flushes that
ring *before* the live frames. The ASR then recognises one continuous buffer
whose first samples predate the gate trip.

If :attr:`UtteranceBuffer.pre_roll_complete` is ``False`` — because the pre-roll
block was truncated, arrived out of order, or failed to decode — the transcript
is still produced, but the dashboard refuses to show the "no clipped words"
badge. The count of utterances per demo that actually had clean pre-roll is the
auditable form of the claim.

Framing contract: the device sends exactly **one Opus packet per WebSocket
message**. A bare Opus byte stream has no length prefix, so the message
boundary is the only delimiter available; the host cannot split a concatenated
stream itself.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

#: Opus frames are 20 ms at 16 kHz, so this is the exact sample count per frame.
SAMPLES_PER_OPUS_FRAME = 320
SAMPLE_RATE_HZ = 16_000
FRAME_DURATION_MS = SAMPLES_PER_OPUS_FRAME * 1000 // SAMPLE_RATE_HZ

#: Reasons a pre-roll block is not trustworthy. Surfaced verbatim in the UI so
#: the failure is explainable rather than just "the badge is missing".
PREROLL_MISSING = "preroll_block_missing"
PREROLL_DECODE_FAILED = "preroll_decode_failed"
PREROLL_OUT_OF_ORDER = "preroll_out_of_order"
PREROLL_TOO_SHORT = "preroll_too_short"

VALID_PREROLL_ISSUES = frozenset(
    {PREROLL_MISSING, PREROLL_DECODE_FAILED, PREROLL_OUT_OF_ORDER, PREROLL_TOO_SHORT}
)


class OpusDecoderError(RuntimeError):
    """Raised when a compressed frame cannot be turned back into PCM."""


def load_av() -> Any:
    """Import PyAV, converting a missing dependency into a typed error.

    A demo machine without ``av`` installed must produce an actionable message
    rather than a bare ``ModuleNotFoundError`` from three frames deep.
    """
    try:
        import av
    except ImportError as exc:
        raise OpusDecoderError(
            "PyAV is not installed; run `python -m pip install av` (see requirements.txt)"
        ) from exc
    return av


def decode_opus(packets: Iterable[bytes]) -> bytes:
    """Decode raw Opus packets to signed 16-bit little-endian mono PCM at 16 kHz.

    Uses PyAV, which bundles its own libopus and therefore needs no system
    package installed on the demo machine.
    """
    av = load_av()  # ~200 ms import cost; not paid at module load

    # "r" gives a bare decoder with no container attached, which is what a
    # stream of one-packet-per-message needs. There is no "raw" mode: PyAV
    # maps mode onto AVCodecContext open flags and rejects anything but r/w.
    codec = av.CodecContext.create("opus", "r")
    resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE_HZ)
    pcm = bytearray()
    try:
        for packet_bytes in packets:
            for frame in codec.decode(av.Packet(packet_bytes)):
                for resampled in resampler.resample(frame):
                    pcm.extend(resampled.to_ndarray().tobytes())
        for frame in codec.decode(None):  # flush the encoder delay
            for resampled in resampler.resample(frame):
                pcm.extend(resampled.to_ndarray().tobytes())
    except Exception as exc:  # noqa: BLE001 - PyAV raises assorted bare subclasses
        raise OpusDecoderError(f"Opus decode failed: {exc}") from exc
    return bytes(pcm)


@dataclass(slots=True)
class UtteranceBuffer:
    """Accumulates one command utterance across pre-roll and live frames.

    Mirrors the device's wire behaviour: ``kind=0x02`` pre-roll frames arrive
    first, then ``kind=0x01`` live frames, then a ``0x03`` terminator.
    """

    utterance_id: int
    preroll_frames: deque[bytes] = field(default_factory=deque)
    live_frames: list[bytes] = field(default_factory=list)
    pre_roll_complete: bool = False
    preroll_issue: str | None = None
    _saw_first_marker: bool = False

    def add_preroll(self, opus_frame: bytes, *, is_first: bool) -> None:
        """Buffer a pre-roll frame. ``is_first`` sets the protocol's bit0 flag."""
        if is_first:
            self._saw_first_marker = True
        self.preroll_frames.append(opus_frame)

    def add_live(self, opus_frame: bytes) -> None:
        self.live_frames.append(opus_frame)

    def finalise(self, *, minimum_preroll_ms: int) -> None:
        """Decide whether the pre-roll block can be trusted, and say why not."""
        if not self.preroll_frames:
            self._settle(False, PREROLL_MISSING)
            return
        if not self._saw_first_marker:
            # Without the first-frame marker we cannot tell whether the head of
            # the block is the oldest audio the device still held, or whether
            # the device began mid-stream and clipped something itself.
            self._settle(False, PREROLL_OUT_OF_ORDER)
            return
        if self.preroll_duration_ms() < minimum_preroll_ms:
            self._settle(False, PREROLL_TOO_SHORT)
            return
        self._settle(True, None)

    def _settle(self, complete: bool, issue: str | None) -> None:
        self.pre_roll_complete = complete
        self.preroll_issue = issue

    def ordered_frames(self) -> list[bytes]:
        """Pre-roll first, then live — the order the recogniser must see."""
        return [*self.preroll_frames, *self.live_frames]

    def preroll_duration_ms(self) -> int:
        return len(self.preroll_frames) * FRAME_DURATION_MS

    def duration_ms(self) -> int:
        return len(self.ordered_frames()) * FRAME_DURATION_MS

    def decode(self) -> bytes:
        """Decode the whole utterance to PCM, pre-roll included.

        A decode failure demotes an otherwise-complete pre-roll block, because
        a transcript assembled from out-of-order audio is exactly the clipped
        first word we are claiming to have prevented.
        """
        try:
            return decode_opus(self.ordered_frames())
        except OpusDecoderError:
            if self.pre_roll_complete:
                self._settle(False, PREROLL_DECODE_FAILED)
            raise
