"""End-to-end relay tests against a real WebSocket server.

These exercise the parts that unit tests cannot: path routing, fan-out to
multiple observers, the Opus -> transcript pipeline, and liveness. Each test
binds an ephemeral port so they never collide with a relay the team is
actually running on 8765.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import struct

import pytest
import websockets
from websockets.asyncio.client import connect

from edge_voice.config import AppConfig
from edge_voice.protocol import (
    OPUS_FLAG_FIRST_PREROLL,
    OPUS_KIND_AUDIO,
    OPUS_KIND_PREROLL,
    OPUS_KIND_UTTERANCE_END,
)
from edge_voice.relay import TelemetryRelay
from tests.conftest import (
    StubAsrEngine,
    ai_result_text,
    boot_frame_text,
    decision_text,
    gate_text,
    make_config,
)


class RelayHarness:
    """Runs a :class:`TelemetryRelay` in the background for the duration of a test."""

    def __init__(self, relay: TelemetryRelay) -> None:
        self.relay = relay
        self._task: asyncio.Task[None] | None = None
        self._heartbeat: asyncio.Task[None] | None = None

    async def __aenter__(self) -> RelayHarness:
        self._heartbeat = self.relay.start_heartbeat()
        self._task = asyncio.create_task(self.relay.serve_forever())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if self.relay.bound_port != 0 or (self._task is not None and self._task.done()):
                break
        return self

    async def __aexit__(self, *exc: object) -> None:
        self.relay.request_stop()
        for task in (self._task, self._heartbeat):
            if task is not None:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    def url(self, path: str) -> str:
        return f"ws://127.0.0.1:{self.relay.bound_port}{path}"


@pytest.fixture
async def harness(relay: TelemetryRelay):
    async with RelayHarness(relay) as running:
        yield running


async def drain(socket, *, want: int = 1, timeout_s: float = 2.0, of_type: str | None = None) -> list:
    """Collect up to ``want`` messages, skipping heartbeats and other types.

    Filtering by type rather than by position is what keeps these tests stable:
    the relay legitimately emits interleaved ``device_status`` and ``ping``
    frames, and a test that asserted on arrival order would really be asserting
    on scheduling accidents.

    Returns whatever arrived before the deadline rather than raising, so a test
    asserting "nothing was sent" reads as ``assert await drain(...) == []``.
    """
    collected: list = []
    deadline = asyncio.get_running_loop().time() + timeout_s
    while len(collected) < want:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            break
        try:
            raw = await asyncio.wait_for(socket.recv(), timeout=remaining)
        except (TimeoutError, asyncio.CancelledError):
            break
        envelope = json.loads(raw)
        if envelope.get("type") == "ping":
            continue
        if of_type is not None and envelope.get("type") != of_type:
            continue
        collected.append(envelope)
    return collected


def opus_frame(kind: int, flags: int, payload: bytes, utterance_id: int = 1, seq: int = 0) -> bytes:
    return struct.pack("<BBHI", kind, flags, utterance_id, seq) + payload


class TestRouting:
    async def test_rejects_an_unknown_path(self, harness: RelayHarness) -> None:
        with pytest.raises(websockets.exceptions.InvalidStatus) as excinfo:
            async with connect(harness.url("/nope")):
                pass
        assert excinfo.value.response.status_code == 404

    async def test_accepts_the_device_path(self, harness: RelayHarness) -> None:
        """/device is wired to the device handler, not the observer handler.

        A garbage frame sent from /device produces a rejection notice, which is
        something only the device handler can do.
        """
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send("total garbage")

            notice = await drain(observer, of_type="relay_notice")
            assert notice[0]["payload"]["code"] == "BAD_JSON"

    async def test_does_not_echo_device_frames_back_to_the_device(
        self, harness: RelayHarness
    ) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send(boot_frame_text())
            await drain(observer, of_type="telemetry")

            echoed = await drain(device, of_type="telemetry", timeout_s=0.3)
            assert echoed == [], "the device link must not receive its own telemetry"
    async def test_accepts_several_observers_on_one_device_link(self, harness: RelayHarness) -> None:
        """The single-client limitation of the original server is the whole point."""
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as first, connect(
            harness.url("/ws")
        ) as second:
            await device.send(boot_frame_text())
            await drain(first, of_type="telemetry", timeout_s=1.0)
            await drain(second, of_type="telemetry", timeout_s=1.0)

            await device.send(gate_text(900.0))
            for observer in (first, second):
                seen = await drain(observer, of_type="telemetry")
                assert seen[0]["payload"]["rms"] == 900.0

    async def test_refuses_observers_past_the_configured_limit(self, relay_config: AppConfig) -> None:
        """A crowded demo venue must not exhaust the relay's memory."""
        relay = TelemetryRelay(make_config(relay_config, max_observer_clients=1), StubAsrEngine())
        async with RelayHarness(relay) as running:
            async with connect(running.url("/ws")) as accepted:
                await drain(accepted, of_type="device_status", timeout_s=1.0)
                assert relay.status_snapshot()["observers"] == 1

                async with connect(running.url("/ws")) as refused:
                    with pytest.raises(websockets.exceptions.ConnectionClosed) as excinfo:
                        await drain(refused, timeout_s=1.0)
                    assert excinfo.value.rcvd is not None
                    assert excinfo.value.rcvd.code == 1008

                assert relay.status_snapshot()["observers"] == 1, "the refused client must not be tracked"


class TestTelemetryForwarding:
    async def test_forwards_a_valid_frame_verbatim(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            wire = ai_result_text([0.02, 0.11, 0.87])
            await device.send(wire)

            received = await drain(observer, of_type="telemetry")
            assert received[0]["type"] == "telemetry"
            assert received[0]["payload"] == json.loads(wire)

    async def test_rejects_and_counts_a_corrupt_frame(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send(ai_result_text([0.62, 0.07, 1.0]))  # torn cross-core read

            notices = await drain(observer, of_type="relay_notice")
            assert notices[0]["type"] == "relay_notice"
            assert notices[0]["payload"]["code"] == "PROBABILITIES_NOT_NORMALISED"

        stats = harness.relay.status_snapshot()["stats"]
        assert stats["frames_rejected"] == 1
        assert stats["rejection_reasons"]["PROBABILITIES_NOT_NORMALISED"] == 1

    async def test_never_forwards_a_rejected_frame(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send(ai_result_text([0.5, 0.5, 0.9]))

            assert await drain(observer, of_type="telemetry", timeout_s=0.3) == []

    async def test_boot_frame_publishes_the_class_order_to_observers(self, harness: RelayHarness) -> None:
        """A late-joining browser must learn the class order without a reboot."""
        async with connect(harness.url("/device")) as device:
            await device.send(boot_frame_text(class_order=["friday", "negative", "silence"]))
            await asyncio.sleep(0.1)

        async with connect(harness.url("/ws")) as observer:
            status = await drain(observer, of_type="device_status")
            assert status[0]["type"] == "device_status"
            assert status[0]["payload"]["boot"]["class_order"] == ["friday", "negative", "silence"]


class TestLegacyFallback:
    async def test_translates_an_unflashed_firmware_string(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send("[AI] Sil: 12% | Unk: 08% | Fri: 80%")

            received = await drain(observer, of_type="telemetry")
            assert received[0]["type"] == "telemetry"
            assert received[0]["payload"]["ev"] == "ai_result"
            assert received[0]["payload"]["p"] == [0.12, 0.08, 0.8]

    async def test_bads_the_translated_frame_as_synthetic(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send("[AI] Sil: 12% | Unk: 08% | Fri: 80%")

            received = await drain(observer, of_type="telemetry")
            assert received[0]["payload"]["synthetic"] is True

    async def test_drops_an_unrecognised_string(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send("Booting Neural Network...")

            # One read, then assert: two sequential drains would discard each
            # other's messages because `drain` skips non-matching frames.
            everything = await drain(observer, want=3, timeout_s=0.5)
            kinds = [message["type"] for message in everything]
            assert "telemetry" not in kinds
            notice = next(m for m in everything if m["type"] == "relay_notice")
            assert notice["payload"]["code"] == "BAD_JSON"


class TestUtterancePipeline:
    async def test_assembles_pre_roll_then_live_into_one_transcript(
        self, harness: RelayHarness, stub_asr: StubAsrEngine
    ) -> None:
        pytest.importorskip("av")
        from tests.test_preroll import encode_opus_packets, speech_like_tone

        packets = encode_opus_packets(speech_like_tone(duration_ms=600))
        pre_roll_count = 10

        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send(decision_text(True))
            await drain(observer, of_type="telemetry")

            for index, packet in enumerate(packets):
                if index < pre_roll_count:
                    kind = OPUS_KIND_PREROLL
                    flags = OPUS_FLAG_FIRST_PREROLL if index == 0 else 0
                else:
                    kind, flags = OPUS_KIND_AUDIO, 0
                await device.send(opus_frame(kind, flags, packet, utterance_id=1, seq=index))
            await device.send(opus_frame(OPUS_KIND_UTTERANCE_END, 0, b"", utterance_id=1))

            transcript = await drain(observer, of_type="transcript", timeout_s=8.0)

        payload = transcript[0]["payload"]
        assert transcript[0]["type"] == "transcript"
        assert payload["text"] == "play some jazz"
        assert payload["first_word"] == "play"
        assert payload["pre_roll_complete"] is True
        assert stub_asr.calls == 1

    async def test_reports_a_utterance_with_no_pre_roll(
        self, harness: RelayHarness, stub_asr: StubAsrEngine
    ) -> None:
        pytest.importorskip("av")
        from tests.test_preroll import encode_opus_packets, speech_like_tone

        packets = encode_opus_packets(speech_like_tone(duration_ms=400))
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.send(decision_text(True))
            await drain(observer, of_type="telemetry")
            for index, packet in enumerate(packets):
                await device.send(opus_frame(OPUS_KIND_AUDIO, 0, packet, 1, index))
            await device.send(opus_frame(OPUS_KIND_UTTERANCE_END, 0, b"", 1))

            messages = await drain(observer, want=3, timeout_s=8.0)

        by_type = {message["type"]: message for message in messages}
        assert "transcript" in by_type
        assert by_type["transcript"]["payload"]["pre_roll_complete"] is False
        assert by_type["relay_notice"]["payload"]["code"] == "preroll_block_missing"

    async def test_rejects_an_oversized_binary_frame(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device:
            await device.send(b"\x01\x00\x01\x00" + b"\x00" * 2000)
            await asyncio.sleep(0.1)

        stats = harness.relay.status_snapshot()["stats"]
        assert stats["rejection_reasons"].get("OPUS_FRAME_TOO_LARGE") == 1

    async def test_still_serves_telemetry_when_no_asr_is_loaded(self, no_asr_relay: TelemetryRelay) -> None:
        async with RelayHarness(no_asr_relay) as running:
            async with connect(running.url("/device")) as device, connect(running.url("/ws")) as observer:
                await drain(observer, of_type="device_status", timeout_s=1.0)
                await device.send(ai_result_text([0.02, 0.11, 0.87]))
                received = await drain(observer, of_type="telemetry")
                assert received[0]["type"] == "telemetry"


class TestLiveness:
    async def test_reports_the_device_offline_when_it_disconnects(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await device.close()
            status = await drain(observer, of_type="device_status")
            assert status[0]["payload"]["connected"] is False
            assert status[0]["payload"]["reason"] == "closed"

    async def test_flags_a_silent_device_as_stale(self, relay_config: AppConfig) -> None:
        """ping_interval=None used to make a wedged ESP32 look healthy forever.

        The shared fixture keeps a generous stale window, so this test builds
        its own relay with a deliberately short one.
        """
        config = make_config(relay_config, heartbeat_interval_s=0.05, device_stale_after_s=0.15)
        relay = TelemetryRelay(config, StubAsrEngine())
        async with RelayHarness(relay) as running:
            async with connect(running.url("/device")), connect(running.url("/ws")) as observer:
                await drain(observer, of_type="device_status", timeout_s=1.0)
                await asyncio.sleep(0.4)

                statuses = await drain(observer, of_type="device_status", timeout_s=2.0)
                assert statuses[0]["payload"]["connected"] is False
                assert statuses[0]["payload"]["reason"] == "timeout"

    async def test_a_talking_device_is_never_marked_stale(self, harness: RelayHarness) -> None:
        """The device answers pings, so liveness must hold."""
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            for _ in range(6):
                await device.send(json.dumps({"v": 1, "ev": "pong", "seq": 1, "t": 0, "wall": 0}))
                await asyncio.sleep(0.05)

            stale = await drain(observer, of_type="device_status", timeout_s=0.3)
            assert stale == []

    async def test_pings_observers_on_a_fixed_cadence(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            raw = await asyncio.wait_for(observer.recv(), timeout=2.0)
            assert json.loads(raw)["type"] == "ping"


class TestCommands:
    async def test_forwards_a_console_command_to_the_device(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/device")) as device, connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await observer.send(json.dumps({"v": 1, "type": "command", "payload": {"cmd": "arm"}}))

            echoed = json.loads(await asyncio.wait_for(device.recv(), timeout=1.0))
            assert echoed["cmd"] == "arm"

    async def test_warns_when_a_command_cannot_be_delivered(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await observer.send(json.dumps({"v": 1, "type": "command", "payload": {"cmd": "arm"}}))

            notice = await drain(observer, of_type="relay_notice")
            assert notice[0]["payload"]["code"] == "DEVICE_OFFLINE"

    async def test_ignores_garbage_from_an_observer(self, harness: RelayHarness) -> None:
        async with connect(harness.url("/ws")) as observer:
            await drain(observer, of_type="device_status", timeout_s=1.0)
            await observer.send("not json")
            await observer.send(json.dumps({"type": "not-a-command"}))
            await asyncio.sleep(0.1)
            assert harness.relay.status_snapshot()["observers"] == 1
        await asyncio.sleep(0.15)
        assert harness.relay.status_snapshot()["observers"] == 0
