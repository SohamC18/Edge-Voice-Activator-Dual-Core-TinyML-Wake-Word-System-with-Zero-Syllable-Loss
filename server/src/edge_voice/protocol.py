"""Wire protocol v1 for the Edge Voice Activator.

Single source of truth for ``docs/protocol.md``. Everything the relay accepts
from the device and everything it emits to observers is parsed, validated and
re-emitted here, so neither the server nor the dashboard carries its own
ad-hoc interpretation of the wire.

Design notes that matter when editing:

* The device sends **probabilities in wire order** plus a separate
  ``class_order`` declaration in its ``boot`` frame. Nothing in this module, or
  in the dashboard, is allowed to hardcode which index means "friday" — see
  ADR-0001.
* A value the device cannot measure is **absent from the frame**, not zero.
  ``None`` here must stay ``None`` all the way to the UI so it can render
  "unavailable" rather than a fabricated number.
* Every parsed frame keeps the verbatim wire text in :attr:`DeviceFrame.raw` so
  fan-out to observers is lossless.
"""

from __future__ import annotations

import json
import struct
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

PROTOCOL_VERSION: Final = 1

DEVICE_ENDPOINT: Final = "/device"
OBSERVER_ENDPOINT: Final = "/ws"

#: Opus frames are 20 ms @ 16 kHz mono VOIP; the device caps them at 1000 bytes.
#: Anything larger is a protocol violation and is rejected, not buffered.
OPUS_MAX_FRAME_BYTES: Final = 1024
OPUS_HEADER_BYTES: Final = 8
OPUS_KIND_AUDIO: Final = 0x01
OPUS_KIND_PREROLL: Final = 0x02
OPUS_KIND_UTTERANCE_END: Final = 0x03
OPUS_FLAG_FIRST_PREROLL: Final = 0x01

ClassName = Literal["silence", "unknown", "friday"]
Stage = Literal["idle", "gate_open", "stage1_ai", "stage2_verify", "commit", "streaming"]
DecisionReason = Literal["friday>=thr", "friday<thr", "debounce", "gate_closed", "ai_busy"]

VALID_CLASS_NAMES: Final[frozenset[str]] = frozenset(
    {"silence", "unknown", "negative", "friday"}
)

#: The model handoff note calls index 1 "negative / other word" while the
#: firmware calls it "unknown". Both are accepted on the wire and normalised to
#: one name, so the UI never has to render two labels for the same series.
CLASS_NAME_ALIASES: Final[Mapping[str, str]] = {
    "negative": "unknown",
    "unknown": "unknown",
    "silence": "silence",
    "friday": "friday",
}
VALID_STAGES: Final[frozenset[str]] = frozenset(
    {"idle", "gate_open", "stage1_ai", "stage2_verify", "commit", "streaming"}
)
VALID_DECISION_REASONS: Final[frozenset[str]] = frozenset(
    {"friday>=thr", "friday<thr", "debounce", "gate_closed", "ai_busy"}
)
VALID_DEVICE_EVENTS: Final[frozenset[str]] = frozenset(
    {"boot", "stage", "gate", "ai_result", "decision", "sys", "ram", "pong", "error"}
)

RAM_SEGMENT_NAMES: Final[tuple[str, ...]] = (
    "tensor_arena",
    "mel_db",
    "pcm_ring",
    "preroll_ring",
    "opus_encoder",
    "rtos_stacks",
    "other",
)


class ProtocolError(ValueError):
    """Raised when a frame violates the wire contract.

    ``code`` is a stable token the dashboard can group by; the message is for
    humans reading the relay log.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --------------------------------------------------------------------------- #
# Typed views over validated event bodies
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EnergyGate:
    """Result of one 600 ms energy-gate evaluation."""

    rms: float
    threshold: float
    is_open: bool
    window_ms: int

    @property
    def headroom(self) -> float:
        """RMS as a fraction of the threshold; ``>1.0`` means the gate is open."""
        if self.threshold <= 0:
            return 0.0
        return self.rms / self.threshold


@dataclass(frozen=True, slots=True)
class AiResult:
    """One completed DS-CNN inference, with probabilities in wire order."""

    probabilities: tuple[float, ...]
    inference_ms: float
    arena_bytes: int
    dropped: int

    def probability_of(self, class_order: Sequence[str], name: str) -> float | None:
        """Look up a probability by *name* using the declared class order."""
        try:
            index = list(class_order).index(name)
        except ValueError:
            return None
        if index >= len(self.probabilities):
            return None
        return self.probabilities[index]


@dataclass(frozen=True, slots=True)
class ActivationDecision:
    """The accept/reject verdict for one evaluated window."""

    accepted: bool
    reason: str
    keyword_probability: float
    threshold: float
    since_last_ms: int
    debounce_ms: int


@dataclass(frozen=True, slots=True)
class SystemHealth:
    """Periodic device health counters. Any field may be ``None`` when the
    firmware build does not enable the corresponding measurement."""

    free_heap: int | None
    min_free_heap: int | None
    largest_free_block: int | None
    cpu_idle_pct: float | None
    uptime_ms: int
    rssi_dbm: int | None
    audio_dropped_chunks: int | None
    serial_overruns: int | None


@dataclass(frozen=True, slots=True)
class RamLedger:
    """Measured static memory attribution against the 256 KiB budget."""

    budget_bytes: int
    segments: Mapping[str, int]

    @property
    def total_bytes(self) -> int:
        return sum(self.segments.values())

    @property
    def utilisation(self) -> float:
        """Fraction of the budget consumed; ``>1.0`` is a budget violation."""
        if self.budget_bytes <= 0:
            return 0.0
        return self.total_bytes / self.budget_bytes


@dataclass(frozen=True, slots=True)
class BootInfo:
    """Identity and contract declaration sent once by the device."""

    firmware: str
    chip: str
    arena_bytes: int
    class_order: tuple[str, ...]
    free_heap: int | None
    flash_used: int | None
    sdk: str | None


@dataclass(frozen=True, slots=True)
class DeviceFrame:
    """A validated device -> host frame.

    ``body`` holds only the event-specific fields, already coerced and
    checked. ``raw`` is the verbatim wire text, so the relay can fan out
    without re-serialising and losing unknown/vendor fields.
    """

    version: int
    event: str
    seq: int
    uptime_ms: int
    wall_ms: int
    body: Mapping[str, Any]
    raw: str

    # -- typed accessors; return None when the frame is a different event ---- #

    def boot(self) -> BootInfo | None:
        return _typed(self, "boot", lambda b: BootInfo(
            firmware=_as_str(b, "fw"),
            chip=_as_str(b, "chip"),
            arena_bytes=_as_int(b, "arena_bytes"),
            class_order=_as_class_order(b),
            free_heap=_as_opt_int(b, "free_heap"),
            flash_used=_as_opt_int(b, "flash_used"),
            sdk=_as_opt_str(b, "sdk"),
        ))

    def stage(self) -> tuple[str, str | None]:
        return _typed(self, "stage", lambda b: (_as_stage(b), _as_opt_str(b, "detail")))

    def gate(self) -> EnergyGate | None:
        return _typed(self, "gate", lambda b: EnergyGate(
            rms=_as_float(b, "rms"),
            threshold=_as_float(b, "threshold"),
            is_open=_as_bool(b, "open"),
            window_ms=_as_int(b, "window_ms"),
        ))

    def ai_result(self) -> AiResult | None:
        return _typed(self, "ai_result", lambda b: AiResult(
            probabilities=_as_float_tuple(b, "p"),
            inference_ms=_as_float(b, "inference_ms"),
            arena_bytes=_as_int(b, "arena_bytes"),
            dropped=_as_int(b, "dropped"),
        ))

    def decision(self) -> ActivationDecision | None:
        return _typed(self, "decision", lambda b: ActivationDecision(
            accepted=_as_bool(b, "accept"),
            reason=_as_decision_reason(b),
            keyword_probability=_as_float(b, "keyword_prob"),
            threshold=_as_float(b, "threshold"),
            since_last_ms=_as_int(b, "since_last_ms"),
            debounce_ms=_as_int(b, "debounce_ms"),
        ))

    def system(self) -> SystemHealth | None:
        return _typed(self, "sys", lambda b: SystemHealth(
            free_heap=_as_opt_int(b, "free_heap"),
            min_free_heap=_as_opt_int(b, "min_free_heap"),
            largest_free_block=_as_opt_int(b, "largest_free"),
            cpu_idle_pct=_as_opt_float(b, "cpu_idle_pct"),
            uptime_ms=_as_int(b, "uptime_ms"),
            rssi_dbm=_as_opt_int(b, "rssi"),
            audio_dropped_chunks=_as_opt_int(b, "audio_dropped_chunks"),
            serial_overruns=_as_opt_int(b, "serial_overruns"),
        ))

    def ram(self) -> RamLedger | None:
        return _typed(self, "ram", lambda b: RamLedger(
            budget_bytes=_as_int(b, "budget_bytes"),
            segments=_as_ram_segments(b),
        ))

    def error(self) -> tuple[str, str]:
        return _typed(self, "error", lambda b: (_as_str(b, "code"), _as_str(b, "message")))


def _typed(frame: DeviceFrame, event: str, build: Callable[[Mapping[str, Any]], Any]) -> Any:
    """Apply ``build`` only when ``frame`` is of type ``event``.

    The return type is ``Any`` because the callers below resolve to a different
    concrete dataclass (or ``None``) per event; a single generic would only hide
    that from the type checker rather than express it.
    """
    if frame.event != event:
        return None
    return build(frame.body)


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #

_ENVELOPE_KEYS = frozenset({"v", "ev", "seq", "t", "wall"})

_VALIDATORS = {
    "boot": lambda b: _validate_boot(b),
    "stage": lambda b: _validate_stage(b),
    "gate": lambda b: _validate_gate(b),
    "ai_result": lambda b: _validate_ai_result(b),
    "decision": lambda b: _validate_decision(b),
    "sys": lambda b: None,
    "ram": lambda b: _validate_ram(b),
    "pong": lambda b: None,
    "error": lambda b: _validate_error(b),
}


def parse_device_frame(wire_text: str) -> DeviceFrame:
    """Parse and validate one text frame from the device link.

    Raises:
        ProtocolError: if the JSON is malformed, the envelope is incomplete, the
            event name is unknown, or an event body fails validation.
    """
    try:
        decoded = json.loads(wire_text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("BAD_JSON", f"not valid JSON: {exc.msg}") from exc
    if not isinstance(decoded, dict):
        raise ProtocolError("BAD_JSON", "top level must be a JSON object")

    version = _require(decoded, "v")
    if version != PROTOCOL_VERSION:
        raise ProtocolError("BAD_VERSION", f"expected v={PROTOCOL_VERSION}, got {version!r}")

    event = _require(decoded, "ev")
    if event not in VALID_DEVICE_EVENTS:
        raise ProtocolError("BAD_EVENT", f"unknown event {event!r}")

    body = {k: v for k, v in decoded.items() if k not in _ENVELOPE_KEYS}
    try:
        validated = _VALIDATORS[event](body)
    except ProtocolError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise ProtocolError("BAD_BODY", f"invalid body for {event}: {exc}") from exc
    if validated is not None:
        body = validated

    return DeviceFrame(
        version=version,
        event=event,
        seq=_require(decoded, "seq"),
        uptime_ms=_require(decoded, "t"),
        wall_ms=_require(decoded, "wall"),
        body=body,
        raw=wire_text,
    )


def parse_opus_frame(payload: bytes) -> tuple[int, int, int, int, bytes]:
    """Split a binary device frame into ``(kind, flags, utterance_id, seq, opus)``.

    Raises:
        ProtocolError: on a short header, an unknown kind, or an over-long frame.
    """
    if len(payload) > OPUS_MAX_FRAME_BYTES:
        raise ProtocolError(
            "OPUS_FRAME_TOO_LARGE",
            f"{len(payload)} bytes exceeds the {OPUS_MAX_FRAME_BYTES}-byte cap",
        )
    if len(payload) < OPUS_HEADER_BYTES:
        raise ProtocolError(
            "OPUS_HEADER_TRUNCATED",
            f"{len(payload)} bytes is shorter than the {OPUS_HEADER_BYTES}-byte header",
        )
    kind, flags, utterance_id, seq = struct.unpack_from("<BBHI", payload)
    if kind not in (OPUS_KIND_AUDIO, OPUS_KIND_PREROLL, OPUS_KIND_UTTERANCE_END):
        raise ProtocolError("OPUS_BAD_KIND", f"unknown Opus frame kind 0x{kind:02x}")
    return kind, flags, utterance_id, seq, payload[OPUS_HEADER_BYTES:]


# --------------------------------------------------------------------------- #
# Host -> observer envelopes
# --------------------------------------------------------------------------- #


def observer_envelope(message_type: str, payload: Mapping[str, Any]) -> str:
    """Build one frame for the browser fan-out."""
    return json.dumps(
        {"v": PROTOCOL_VERSION, "type": message_type, "payload": payload},
        separators=(",", ":"),
    )


def device_status_payload(
    *,
    connected: bool,
    reason: str | None,
    boot: BootInfo | None,
) -> dict[str, Any]:
    """Build the ``device_status`` payload browsers render in the header."""
    boot_payload: dict[str, Any] | None = None
    if boot is not None:
        boot_payload = {
            "firmware": boot.firmware,
            "chip": boot.chip,
            "arena_bytes": boot.arena_bytes,
            "class_order": list(boot.class_order),
            "free_heap": boot.free_heap,
            "flash_used": boot.flash_used,
            "sdk": boot.sdk,
        }
    return {"connected": connected, "reason": reason, "boot": boot_payload}


def transcript_payload(
    *,
    utterance_id: int,
    text: str,
    first_word: str | None,
    first_word_ms: float | None,
    preroll_ms: int,
    pre_roll_complete: bool,
    asr_ms: float | None,
    confidence: float | None,
) -> dict[str, Any]:
    """Build the ``transcript`` payload.

    ``pre_roll_complete`` is what the UI keys its "no clipped words" badge off:
    it is ``True`` only when the pre-roll block was received *and* successfully
    decoded ahead of the live frames.
    """
    return {
        "utterance_id": utterance_id,
        "text": text,
        "first_word": first_word,
        "first_word_ms": first_word_ms,
        "preroll_ms": preroll_ms,
        "pre_roll_complete": pre_roll_complete,
        "asr_ms": asr_ms,
        "confidence": confidence,
        "final": True,
    }


# --------------------------------------------------------------------------- #
# Field coercion helpers
# --------------------------------------------------------------------------- #


def _require(mapping: Mapping[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ProtocolError("MISSING_FIELD", f"required field {key!r} is absent")
    return mapping[key]


def _as_str(mapping: Mapping[str, Any], key: str) -> str:
    value = _require(mapping, key)
    if not isinstance(value, str):
        raise ProtocolError("BAD_TYPE", f"{key!r} must be a string, got {type(value).__name__}")
    return value


def _as_opt_str(mapping: Mapping[str, Any], key: str) -> str | None:
    if mapping.get(key) is None:
        return None
    return _as_str(mapping, key)


def _as_float(mapping: Mapping[str, Any], key: str) -> float:
    value = _require(mapping, key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError("BAD_TYPE", f"{key!r} must be a number, got {type(value).__name__}")
    return float(value)


def _as_opt_float(mapping: Mapping[str, Any], key: str) -> float | None:
    if mapping.get(key) is None:
        return None
    return _as_float(mapping, key)


def _as_int(mapping: Mapping[str, Any], key: str) -> int:
    value = _require(mapping, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError("BAD_TYPE", f"{key!r} must be an integer, got {type(value).__name__}")
    return value


def _as_opt_int(mapping: Mapping[str, Any], key: str) -> int | None:
    if mapping.get(key) is None:
        return None
    return _as_int(mapping, key)


def _as_bool(mapping: Mapping[str, Any], key: str) -> bool:
    value = _require(mapping, key)
    if not isinstance(value, bool):
        raise ProtocolError("BAD_TYPE", f"{key!r} must be a boolean, got {type(value).__name__}")
    return value


def _as_float_tuple(mapping: Mapping[str, Any], key: str) -> tuple[float, ...]:
    value = _require(mapping, key)
    if not isinstance(value, list) or not value:
        raise ProtocolError("BAD_TYPE", f"{key!r} must be a non-empty array of numbers")
    return tuple(float(item) for item in value)


def _as_class_order(mapping: Mapping[str, Any]) -> tuple[str, ...]:
    value = _require(mapping, "class_order")
    if not isinstance(value, list) or not value:
        raise ProtocolError("BAD_CLASS_ORDER", "class_order must be a non-empty array")
    unknown = [name for name in value if name not in VALID_CLASS_NAMES]
    if unknown:
        raise ProtocolError("BAD_CLASS_ORDER", f"unknown class names {unknown}")
    if len(set(value)) != len(value):
        raise ProtocolError("BAD_CLASS_ORDER", "class_order contains duplicates")
    return tuple(value)


def canonical_class_name(name: str) -> str:
    """Normalise a wire class name to the label the UI renders."""
    return CLASS_NAME_ALIASES.get(name, name)


def _as_stage(mapping: Mapping[str, Any]) -> str:
    value = _as_str(mapping, "stage")
    if value not in VALID_STAGES:
        raise ProtocolError("BAD_STAGE", f"unknown stage {value!r}")
    return value


def _as_decision_reason(mapping: Mapping[str, Any]) -> str:
    value = _as_str(mapping, "reason")
    if value not in VALID_DECISION_REASONS:
        raise ProtocolError("BAD_REASON", f"unknown decision reason {value!r}")
    return value


def _as_ram_segments(mapping: Mapping[str, Any]) -> dict[str, int]:
    value = _require(mapping, "segments")
    if not isinstance(value, dict):
        raise ProtocolError("BAD_TYPE", "segments must be an object")
    unknown = set(value) - set(RAM_SEGMENT_NAMES)
    if unknown:
        raise ProtocolError("BAD_RAM_SEGMENT", f"unknown segments {sorted(unknown)}")
    return {name: int(count) for name, count in value.items() if count is not None}


# --------------------------------------------------------------------------- #
# Per-event body validation
# --------------------------------------------------------------------------- #


def _validate_boot(body: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("fw", "chip", "arena_bytes", "class_order"):
        _require(body, key)
    _as_str(body, "fw")
    _as_str(body, "chip")
    _as_int(body, "arena_bytes")
    _as_class_order(body)
    return body


def _validate_stage(body: Mapping[str, Any]) -> Mapping[str, Any]:
    _as_stage(body)
    return body


def _validate_gate(body: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("rms", "threshold", "open", "window_ms"):
        _require(body, key)
    if _as_float(body, "rms") < 0:
        raise ProtocolError("NEGATIVE_RMS", "rms cannot be negative")
    return body


def _validate_ai_result(body: Mapping[str, Any]) -> Mapping[str, Any]:
    probabilities = _as_float_tuple(body, "p")
    if not 0.0 <= sum(probabilities) <= 1.05:
        # A softmax output must sum to 1. This guard is the server-side
        # counterpart to the firmware's atomic-result fix: a torn read across
        # cores shows up here as a probability vector that does not sum to 1,
        # and we refuse to forward a frame we know is corrupt.
        raise ProtocolError(
            "PROBABILITIES_NOT_NORMALISED",
            f"p sums to {sum(probabilities):.4f}, expected ~1.0 (torn cross-core read?)",
        )
    _as_int(body, "dropped")
    return body


def _validate_decision(body: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("accept", "reason", "keyword_prob", "threshold", "since_last_ms", "debounce_ms"):
        _require(body, key)
    _as_decision_reason(body)
    return body


def _validate_ram(body: Mapping[str, Any]) -> Mapping[str, Any]:
    _as_int(body, "budget_bytes")
    _as_ram_segments(body)
    return body


def _validate_error(body: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("code", "message"):
        _require(body, key)
    return body
