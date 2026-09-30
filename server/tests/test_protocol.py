"""Contract tests for the wire protocol.

These are the executable form of ``docs/protocol.md``. If the two disagree,
one of them is the bug — the spec says the tests win.
"""

from __future__ import annotations

import json

import pytest

from edge_voice.protocol import (
    OPUS_FLAG_FIRST_PREROLL,
    OPUS_HEADER_BYTES,
    OPUS_KIND_AUDIO,
    OPUS_KIND_PREROLL,
    OPUS_KIND_UTTERANCE_END,
    OPUS_MAX_FRAME_BYTES,
    PROTOCOL_VERSION,
    ActivationDecision,
    EnergyGate,
    ProtocolError,
    RamLedger,
    canonical_class_name,
    device_status_payload,
    observer_envelope,
    parse_device_frame,
    parse_opus_frame,
    transcript_payload,
)
from tests.conftest import _envelope, ai_result_text, boot_frame_text, decision_text, gate_text


class TestEnvelope:
    def test_parses_a_complete_frame(self) -> None:
        frame = parse_device_frame(gate_text(812.4))

        assert frame.version == PROTOCOL_VERSION
        assert frame.event == "gate"
        assert frame.seq == 3
        assert frame.uptime_ms == 1000

    def test_preserves_verbatim_wire_text_for_lossless_fanout(self) -> None:
        wire = gate_text(812.4)
        assert parse_device_frame(wire).raw == wire

    def test_keeps_unknown_vendor_fields_in_the_body(self) -> None:
        frame = parse_device_frame(gate_text(500.0, seq=9))
        assert "v" not in frame.body and "ev" not in frame.body

    @pytest.mark.parametrize(
        ("wire", "expected_code"),
        [
            ("not json at all", "BAD_JSON"),
            ("[1,2,3]", "BAD_JSON"),
            ('{"ev":"gate","seq":1,"t":1,"wall":1}', "MISSING_FIELD"),
            ('{"v":2,"ev":"gate","seq":1,"t":1,"wall":1}', "BAD_VERSION"),
            ('{"v":1,"ev":"nope","seq":1,"t":1,"wall":1}', "BAD_EVENT"),
        ],
    )
    def test_rejects_malformed_envelopes(self, wire: str, expected_code: str) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_device_frame(wire)
        assert excinfo.value.code == expected_code


class TestBoot:
    def test_exposes_class_order_as_names_not_indices(self) -> None:
        boot = parse_device_frame(boot_frame_text()).boot()

        assert boot is not None
        assert boot.class_order == ("silence", "unknown", "friday")
        assert boot.arena_bytes == 30720

    @pytest.mark.parametrize(
        "class_order",
        [
            ["silence", "unknown", "sunday"],
            ["silence", "silence", "friday"],
            [],
            "silence",
        ],
    )
    def test_rejects_unusable_class_orders(self, class_order: object) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_device_frame(boot_frame_text(class_order=class_order))
        assert excinfo.value.code == "BAD_CLASS_ORDER"

    def test_normalises_the_negative_and_unknown_spellings(self) -> None:
        """'negative' (handoff note) and 'unknown' (firmware) are one series."""
        from_doc = parse_device_frame(
            boot_frame_text(class_order=["friday", "negative", "silence"])
        ).boot()
        from_firmware = parse_device_frame(
            boot_frame_text(class_order=["silence", "unknown", "friday"])
        ).boot()
        assert from_doc is not None and from_firmware is not None

        assert canonical_class_name(from_doc.class_order[1]) == "unknown"
        assert canonical_class_name(from_firmware.class_order[1]) == "unknown"

    def test_boot_frame_built_from_the_documented_alternative_order(self) -> None:
        """The kws_model_progress.md ordering must be accepted verbatim.

        The model owner documented index 0 = friday while the firmware treats
        index 0 = silence. Rather than guess, the protocol carries whichever
        order the device declares.
        """
        boot = parse_device_frame(
            boot_frame_text(class_order=["friday", "negative", "silence"])
        ).boot()
        assert boot is not None
        assert boot.class_order[0] == "friday"


class TestClassIndependentLookups:
    def test_probability_is_addressed_by_name_not_index(self) -> None:
        """The whole point of carrying class_order: no hardcoded indices."""
        result = parse_device_frame(ai_result_text([0.02, 0.11, 0.87])).ai_result()
        assert result is not None

        assert result.probability_of(("silence", "unknown", "friday"), "friday") == 0.87
        assert result.probability_of(("friday", "negative", "silence"), "friday") == 0.02
        assert result.probability_of(("silence", "unknown"), "friday") is None
        assert result.probability_of(("silence", "unknown", "friday"), "monday") is None


class TestAiResult:
    def test_parses_probabilities_and_counters(self) -> None:
        result = parse_device_frame(ai_result_text([0.02, 0.11, 0.87], dropped=3)).ai_result()

        assert result is not None
        assert result.inference_ms == 41.0
        assert result.dropped == 3

    def test_rejects_probabilities_that_do_not_sum_to_one(self) -> None:
        """A torn cross-core float read produces exactly this signature.

        The relay must refuse the frame rather than render three percentages
        that visibly do not add to 100 in front of a judging panel.
        """
        with pytest.raises(ProtocolError) as excinfo:
            parse_device_frame(ai_result_text([0.62, 0.07, 1.0]))
        assert excinfo.value.code == "PROBABILITIES_NOT_NORMALISED"

    def test_rejects_an_empty_probability_vector(self) -> None:
        with pytest.raises(ProtocolError):
            parse_device_frame(ai_result_text([]))

    def test_tolerates_rounding_drift_from_percent_printed_floats(self) -> None:
        frame = parse_device_frame(ai_result_text([0.334, 0.333, 0.332]))
        assert frame.ai_result() is not None


class TestGate:
    def test_computes_headroom_against_the_threshold(self) -> None:
        gate = parse_device_frame(gate_text(600.0, threshold=300.0)).gate()

        assert isinstance(gate, EnergyGate)
        assert gate.headroom == pytest.approx(2.0)
        assert gate.is_open is True

    def test_rejects_a_negative_rms(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_device_frame(gate_text(-1.0))
        assert excinfo.value.code == "NEGATIVE_RMS"

    def test_guard_against_division_by_zero_threshold(self) -> None:
        gate = parse_device_frame(gate_text(100.0, threshold=0.0)).gate()
        assert gate is not None
        assert gate.headroom == 0.0


class TestDecision:
    @pytest.mark.parametrize("accepted", [True, False])
    def test_parses_the_verdict(self, accepted: bool) -> None:
        decision = parse_device_frame(decision_text(accepted)).decision()

        assert isinstance(decision, ActivationDecision)
        assert decision.accepted is accepted
        assert decision.debounce_ms == 2000

    def test_rejects_an_unknown_reason_token(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_device_frame(decision_text(True, reason="because_i_said_so"))
        assert excinfo.value.code == "BAD_REASON"

    @pytest.mark.parametrize("reason", ["friday>=thr", "friday<thr", "debounce", "gate_closed", "ai_busy"])
    def test_accepts_every_documented_reason(self, reason: str) -> None:
        assert parse_device_frame(decision_text(True, reason=reason)).decision() is not None


class TestRam:
    def test_totals_segments_against_the_budget(self) -> None:
        frame = _envelope(
            "ram",
            {
                "budget_bytes": 262144,
                "segments": {
                    "tensor_arena": 30720,
                    "mel_db": 16160,
                    "pcm_ring": 32000,
                    "opus_encoder": 12288,
                },
            },
        )
        ledger = parse_device_frame(frame).ram()

        assert isinstance(ledger, RamLedger)
        assert ledger.total_bytes == 91168
        assert ledger.utilisation == pytest.approx(0.3477, abs=1e-3)

    def test_rejects_an_unknown_segment_name(self) -> None:
        frame = _envelope("ram", {"budget_bytes": 1, "segments": {"mystery_pool": 1}})
        with pytest.raises(ProtocolError) as excinfo:
            parse_device_frame(frame)
        assert excinfo.value.code == "BAD_RAM_SEGMENT"


class TestSystemHealth:
    def test_absent_measurements_stay_none_rather_than_zero(self) -> None:
        """An unmeasurable counter must render 'unavailable', never 0%."""
        frame = _envelope("sys", {"uptime_ms": 91800, "free_heap": 142336})
        health = parse_device_frame(frame).system()

        assert health is not None
        assert health.free_heap == 142336
        assert health.cpu_idle_pct is None
        assert health.rssi_dbm is None

    def test_reads_cpu_idle_when_the_build_enables_run_time_stats(self) -> None:
        frame = _envelope("sys", {"uptime_ms": 91800, "cpu_idle_pct": 92.4, "rssi": -58})
        health = parse_device_frame(frame).system()

        assert health is not None
        assert health.cpu_idle_pct == 92.4
        assert health.rssi_dbm == -58


class TestStage:
    @pytest.mark.parametrize(
        "stage", ["idle", "gate_open", "stage1_ai", "stage2_verify", "commit", "streaming"]
    )
    def test_accepts_every_documented_stage(self, stage: str) -> None:
        frame = parse_device_frame(_envelope("stage", {"stage": stage}))
        assert frame.stage() == (stage, None)

    def test_rejects_an_unknown_stage(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_device_frame(_envelope("stage", {"stage": "teleporting"}))
        assert excinfo.value.code == "BAD_STAGE"


class TestOpusFraming:
    def _frame(self, kind: int, flags: int, payload: bytes, utterance_id: int = 7) -> bytes:
        import struct

        return struct.pack("<BBHI", kind, flags, utterance_id, 42) + payload

    def test_splits_the_documented_header(self) -> None:
        parsed = parse_opus_frame(self._frame(OPUS_KIND_AUDIO, 0, b"\x01\x02\x03", 9))

        kind, flags, utterance_id, seq, opus = parsed
        assert (kind, flags, utterance_id, seq, opus) == (OPUS_KIND_AUDIO, 0, 9, 42, b"\x01\x02\x03")

    def test_reads_the_first_preroll_flag(self) -> None:
        _kind, flags, *_ = parse_opus_frame(
            self._frame(OPUS_KIND_PREROLL, OPUS_FLAG_FIRST_PREROLL, b"")
        )
        assert flags & OPUS_FLAG_FIRST_PREROLL

    def test_accepts_an_utterance_end_marker_with_no_payload(self) -> None:
        kind, _flags, _id, _seq, opus = parse_opus_frame(self._frame(OPUS_KIND_UTTERANCE_END, 0, b""))
        assert kind == OPUS_KIND_UTTERANCE_END
        assert opus == b""

    def test_rejects_a_truncated_header(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_opus_frame(b"\x01\x00")
        assert excinfo.value.code == "OPUS_HEADER_TRUNCATED"

    def test_rejects_an_oversized_frame_instead_of_buffering_it(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_opus_frame(b"\x00" * (OPUS_MAX_FRAME_BYTES + 1))
        assert excinfo.value.code == "OPUS_FRAME_TOO_LARGE"

    def test_rejects_an_unknown_kind(self) -> None:
        with pytest.raises(ProtocolError) as excinfo:
            parse_opus_frame(self._frame(0x7F, 0, b""))
        assert excinfo.value.code == "OPUS_BAD_KIND"

    def test_header_constant_matches_the_spec(self) -> None:
        assert OPUS_HEADER_BYTES == 8


class TestObserverEnvelopes:
    def test_device_status_carries_the_boot_contract_to_late_joiners(self) -> None:
        boot = parse_device_frame(boot_frame_text()).boot()
        assert boot is not None

        payload = device_status_payload(connected=True, reason=None, boot=boot)
        assert payload["boot"]["class_order"] == ["silence", "unknown", "friday"]

    def test_device_status_handles_a_device_that_never_booted(self) -> None:
        payload = device_status_payload(connected=True, reason=None, boot=None)
        assert payload["boot"] is None

    def test_transcript_payload_defaults_to_no_preroll_claim(self) -> None:
        payload = transcript_payload(
            utterance_id=1,
            text="hello",
            first_word=None,
            first_word_ms=None,
            preroll_ms=0,
            pre_roll_complete=False,
            asr_ms=None,
            confidence=None,
        )
        assert payload["pre_roll_complete"] is False
        assert payload["first_word"] is None

    def test_observer_envelope_is_compact_json(self) -> None:
        wire = observer_envelope("ping", {"t": 5})
        assert ", " not in wire
        assert json.loads(wire) == {"v": 1, "type": "ping", "payload": {"t": 5}}
