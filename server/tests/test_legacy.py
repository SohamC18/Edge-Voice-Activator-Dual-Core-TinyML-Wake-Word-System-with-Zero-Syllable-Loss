"""Tests for the pre-telemetry firmware adapter.

The point of this module is graceful degradation: an un-flashed board should
still drive the dashboard, with the gaps labelled rather than hidden.
"""

from __future__ import annotations

import itertools

import pytest

from edge_voice.legacy import LEGACY_CLASS_ORDER, translate_legacy_text


@pytest.fixture
def sequence() -> itertools.count:
    return itertools.count(1)


@pytest.fixture
def translate(sequence: itertools.count):
    def _translate(line: str):
        return translate_legacy_text(line, next_seq=lambda: next(sequence), now_ms=1000)

    return _translate


class TestScoreLine:
    def test_parses_the_shipped_firmware_format(self, translate) -> None:
        frame = translate("[AI] Sil: 12% | Unk: 08% | Fri: 80%")

        assert frame is not None
        assert frame.event == "ai_result"
        assert frame.body["p"] == [0.12, 0.08, 0.8]
        assert frame.body["dropped"] == 0

    def test_declares_the_legacy_class_order(self) -> None:
        assert LEGACY_CLASS_ORDER == ("silence", "unknown", "friday")

    @pytest.mark.parametrize(
        ("line", "expected"),
        [
            ("[AI] Sil: 00% | Unk: 00% | Fri: 100%", [0.0, 0.0, 1.0]),
            ("[AI] Sil: 100% | Unk: 00% | Fri: 00%", [1.0, 0.0, 0.0]),
            ("[AI] Sil: 34% | Unk: 33% | Fri: 33%", [0.34, 0.33, 0.33]),
        ],
    )
    def test_handles_every_class_taking_the_argmax(self, translate, line: str, expected) -> None:
        frame = translate(line)
        assert frame is not None
        assert frame.body["p"] == expected

    def test_tolerates_extra_whitespace_from_snprintf_padding(self, translate) -> None:
        frame = translate("[AI] Sil:  7% | Unk:  3% | Fri: 90%")
        assert frame is not None
        assert frame.body["p"] == [0.07, 0.03, 0.9]

    def test_marks_the_frame_synthetic_so_the_ui_can_badge_it(self, translate) -> None:
        frame = translate("[AI] Sil: 12% | Unk: 08% | Fri: 80%")
        assert frame is not None
        assert frame.body["synthetic"] is True
        assert "legacy" in frame.body["note"].lower()

    def test_the_translated_frame_passes_v1_validation(self, translate) -> None:
        """A legacy string must survive the v1 parser, or the relay drops it."""
        from edge_voice.protocol import parse_device_frame

        frame = translate("[AI] Sil: 12% | Unk: 08% | Fri: 80%")
        assert frame is not None
        reparsed = parse_device_frame(frame.raw)
        assert reparsed.ai_result() is not None


class TestGateAndBusyLines:
    def test_parses_the_idle_gate_line(self, translate) -> None:
        frame = translate("[IDLE] Audio too quiet (RMS < 300)")

        assert frame is not None
        assert frame.event == "gate"
        assert frame.body["open"] is False
        assert frame.body["threshold"] == 300.0

    def test_parses_the_ai_busy_warning_as_a_rejection(self, translate) -> None:
        frame = translate("[WARN] AI Core Busy, dropping frame")

        assert frame is not None
        assert frame.event == "decision"
        assert frame.body["accept"] is False
        assert frame.body["reason"] == "ai_busy"

    @pytest.mark.parametrize(
        "line", ["🔥 FRIDAY TRIGGERED!", "FRIDAY TRIGGERED!"]
    )
    def test_parses_the_trigger_banner(self, translate, line: str) -> None:
        frame = translate(line)

        assert frame is not None
        assert frame.event == "decision"
        assert frame.body["accept"] is True

    def test_flags_that_the_trigger_probability_was_invented(self, translate) -> None:
        """The legacy banner carries no probability, so 1.0 is a fabrication."""
        frame = translate("🔥 FRIDAY TRIGGERED!")
        assert frame is not None
        assert frame.body["synthetic"] is True
        assert "does not report" in frame.body["note"]


class TestRejection:
    @pytest.mark.parametrize(
        "line",
        [
            "",
            "   ",
            "Booting Neural Network...",
            "Wi-Fi Connected!",
            "[AI] Sil: 80% | Fri: 20%",  # missing the Unk field
            "[AI] Garbage",
        ],
    )
    def test_returns_none_for_anything_unrecognised(self, translate, line: str) -> None:
        assert translate(line) is None
