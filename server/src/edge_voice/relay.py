"""The ESP32 <-> browser fan-out relay.

Replaces the original print-only ``server.py`` with three things it lacked:

1. **Fan-out.** One device link, N observer links. A projector and every
   judge's laptop see the same stream.
2. **Contract enforcement.** Every device frame is parsed and validated before
   it is forwarded. Frames that violate the protocol — including the
   probabilities-don't-sum-to-1 signature of a torn cross-core read — are
   counted and dropped rather than rendered as truth.
3. **Liveness.** An application-level heartbeat replaces
   ``ping_interval=None``, so a wedged ESP32 shows as *stale* instead of
   looking connected forever.

The Opus uplink is consumed here and turned into transcripts; it is not
forwarded to browsers, because nothing in the dashboard should need an Opus
decoder.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from edge_voice import legacy
from edge_voice.asr.preroll import UtteranceBuffer
from edge_voice.asr.service import AsrEngine, run_in_thread
from edge_voice.config import AppConfig
from edge_voice.metrics import ConnectionStats, LatencyTracker
from edge_voice.protocol import (
    OPUS_FLAG_FIRST_PREROLL,
    OPUS_KIND_AUDIO,
    OPUS_KIND_PREROLL,
    OPUS_KIND_UTTERANCE_END,
    BootInfo,
    DeviceFrame,
    ProtocolError,
    device_status_payload,
    observer_envelope,
    parse_device_frame,
    parse_opus_frame,
    transcript_payload,
)

LOGGER = logging.getLogger(__name__)

HOST_CLOSE_CODE_POLICY = 1008  # "policy violation" — sent when we reject a client


def _resolve_bound_port(server: Any, configured_port: int) -> int:
    """Read the real listening port back off the server.

    Needed because the tests bind to port 0 and let the OS choose, and it is
    useful in production for logging when someone runs the relay behind a
    port-forward.
    """
    for socket in getattr(server, "sockets", ()):
        return int(socket.getsockname()[1])
    return configured_port


@dataclass(slots=True)
class UtteranceSession:
    """In-flight command utterance being assembled from Opus frames."""

    buffer: UtteranceBuffer
    finalise_task: asyncio.Task[None] | None = None


class ObserverHub:
    """Tracks observer connections and fans messages out to all of them.

    A slow or dead observer must never stall the device link, so sends are
    bounded by a timeout and dead sockets are evicted on first failure.
    """

    def __init__(self, *, max_clients: int, send_timeout_s: float = 2.0) -> None:
        self._observers: set[ServerConnection] = set()
        self._max_clients = max_clients
        self._send_timeout_s = send_timeout_s

    @property
    def count(self) -> int:
        return len(self._observers)

    @property
    def is_full(self) -> bool:
        return len(self._observers) >= self._max_clients

    def add(self, connection: ServerConnection) -> None:
        self._observers.add(connection)

    def remove(self, connection: ServerConnection) -> None:
        self._observers.discard(connection)

    async def broadcast_text(self, text: str) -> int:
        return await self._broadcast(lambda conn: conn.send(text))

    async def broadcast_json(self, message_type: str, payload: dict[str, Any]) -> int:
        return await self.broadcast_text(observer_envelope(message_type, payload))

    async def _broadcast(self, send: Callable[[ServerConnection], Any]) -> int:
        if not self._observers:
            return 0
        results = await asyncio.gather(
            *(self._send_one(conn, send) for conn in tuple(self._observers)),
            return_exceptions=True,
        )
        return sum(1 for result in results if result is True)

    async def _send_one(self, connection: ServerConnection, send: Callable[[ServerConnection], Any]) -> bool:
        try:
            await asyncio.wait_for(send(connection), timeout=self._send_timeout_s)
            return True
        except (TimeoutError, ConnectionClosed, RuntimeError) as exc:
            LOGGER.info("Evicting unresponsive observer: %s", exc)
            self._observers.discard(connection)
            with contextlib.suppress(Exception):
                await connection.close()
            return False


class TelemetryRelay:
    """Wires the device link, the observer fan-out and the ASR service together."""

    def __init__(self, config: AppConfig, asr_engine: AsrEngine) -> None:
        self._config = config
        self._asr = asr_engine
        self._hub = ObserverHub(max_clients=config.relay.max_observer_clients)
        self._stats = ConnectionStats()
        self._latency = LatencyTracker()
        self._device: ServerConnection | None = None
        self._boot: BootInfo | None = None
        self._last_device_frame_at: float | None = None
        self._sessions: dict[int, UtteranceSession] = {}
        self._utterance_counter = 0
        self._stopping = asyncio.Event()
        self._heartbeat_stop = asyncio.Event()
        self._legacy_seq = 0
        self._bound_port: int = config.relay.port

    # -- lifecycle ---------------------------------------------------------- #

    async def serve_forever(self) -> None:
        relay = self._config.relay
        async with serve(
            self._dispatch,
            relay.host,
            relay.port,
            process_request=self._route,
            ping_interval=20,
            ping_timeout=20,
        ) as server:
            self._bound_port = _resolve_bound_port(server, relay.port)
            LOGGER.info(
                "Relay listening on ws://%s:%s  (device=%s observers=%s)",
                relay.host,
                self._bound_port,
                relay.device_path,
                relay.observer_path,
            )
            LOGGER.info("ASR engine: %s", self._asr.description)
            await self._stopping.wait()

    @property
    def bound_port(self) -> int:
        """The port actually bound, which differs from the config when it was 0."""
        return self._bound_port

    def request_stop(self) -> None:
        self._stopping.set()

    def _route(self, connection: ServerConnection, request: Any) -> Any:
        """Accept ``/device`` and ``/ws``; refuse everything else.

        Splitting the two roles onto different paths is what lets a browser
        connect without being mistaken for the ESP32 (and vice versa) — the
        single-endpoint original could not do that.
        """
        path = request.path.split("?", 1)[0]
        relay = self._config.relay
        if path in (relay.device_path, relay.observer_path):
            return None
        LOGGER.info("Rejecting unknown path %s", path)
        return connection.respond(404, f"expected {relay.device_path} or {relay.observer_path}\n")

    async def _dispatch(self, connection: ServerConnection) -> None:
        path = connection.request.path.split("?", 1)[0]
        if path == self._config.relay.device_path:
            await self._handle_device(connection)
        else:
            await self._handle_observer(connection)

    # -- device link -------------------------------------------------------- #

    async def _handle_device(self, connection: ServerConnection) -> None:
        if self._device is not None and self._device is not connection:
            LOGGER.warning("Second device connected; replacing the previous one")
            with contextlib.suppress(Exception):
                await self._device.close()
        self._device = connection
        self._stats.device_connects += 1
        self._last_device_frame_at = asyncio.get_running_loop().time()
        LOGGER.info("Device connected")
        try:
            async for message in connection:
                await self._consume_device_message(message)
        except ConnectionClosed:
            LOGGER.info("Device disconnected")
        finally:
            self._stats.device_disconnects += 1
            if self._device is connection:
                self._device = None
            await self._announce_status(connected=False, reason="closed")

    async def _consume_device_message(self, message: str | bytes) -> None:
        self._last_device_frame_at = asyncio.get_running_loop().time()
        if isinstance(message, bytes):
            await self._consume_opus(message)
            return
        await self._consume_device_text(message)

    async def _consume_device_text(self, text: str) -> None:
        frame, rejection = self.parse_or_translate(text)
        if rejection is not None:
            await self._announce_rejection(rejection, text)
        if frame is None:
            return
        self._stats.frames_forwarded += 1
        if (boot := frame.boot()) is not None:
            self._boot = boot
            LOGGER.info(
                "Device booted: fw=%s class_order=%s arena=%d B",
                boot.firmware,
                "/".join(boot.class_order),
                boot.arena_bytes,
            )
            await self._announce_status(connected=True, reason=None)
        if (decision := frame.decision()) is not None and decision.accepted:
            self._utterance_counter = (self._utterance_counter + 1) & 0xFFFF
            self._latency.mark_commit(self._utterance_counter)
        await self._hub.broadcast_json("telemetry", json.loads(frame.raw))

    def parse_or_translate(self, text: str) -> tuple[DeviceFrame | None, ProtocolError | None]:
        """Validate a device frame, falling back to the legacy string grammar.

        Returns ``(frame, rejection)``. Exactly one is non-``None``: either the
        text became a valid frame, or it is reported as a rejection so the
        dashboard can show that the device sent something untrustworthy rather
        than silently rendering stale state.
        """
        try:
            return parse_device_frame(text), None
        except ProtocolError as exc:
            if self._config.legacy_text_fallback:
                translated = legacy.translate_legacy_text(text, next_seq=self._next_legacy_seq)
                if translated is not None:
                    return translated, None
            return None, exc

    async def _announce_rejection(self, exc: ProtocolError, text: str) -> None:
        self._stats.record_rejection(exc.code)
        LOGGER.warning("Rejected device frame [%s]: %s | %r", exc.code, exc.message, text[:200])
        await self._hub.broadcast_json(
            "relay_notice",
            {"level": "error", "code": exc.code, "message": exc.message},
        )

    # -- Opus uplink -------------------------------------------------------- #

    async def _consume_opus(self, payload: bytes) -> None:
        try:
            kind, flags, utterance_id, _seq, opus = parse_opus_frame(payload)
        except ProtocolError as exc:
            self._stats.record_rejection(exc.code)
            LOGGER.warning("Rejected Opus frame [%s]: %s", exc.code, exc.message)
            return
        self._stats.frames_forwarded += 1
        self._stats.opus_bytes_forwarded += len(opus)

        if kind == OPUS_KIND_UTTERANCE_END:
            session = self._sessions.pop(utterance_id, None)
            if session is not None:
                session.finalise_task = asyncio.create_task(
                    self._finalise_utterance(session, timeout_driven=False)
                )
            return

        session = self._sessions.get(utterance_id)
        if session is None:
            session = UtteranceSession(buffer=UtteranceBuffer(utterance_id=utterance_id))
            self._sessions[utterance_id] = session
            if kind == OPUS_KIND_AUDIO:
                # A live frame with no preceding pre-roll block: the device
                # started mid-utterance. Surface it rather than hiding it.
                LOGGER.info("Utterance %d began with live audio, no pre-roll", utterance_id)
        if kind == OPUS_KIND_PREROLL:
            session.buffer.add_preroll(opus, is_first=bool(flags & OPUS_FLAG_FIRST_PREROLL))
        else:
            session.buffer.add_live(opus)
        self._arm_utterance_timeout(session)

    def _arm_utterance_timeout(self, session: UtteranceSession) -> None:
        """Finalise an utterance if the device never sends an explicit end.

        The patched firmware does send ``kind=0x03``, but a demo that silently
        waits forever because a frame was dropped is the worst possible failure
        in front of judges, so the timeout is a hard backstop.
        """
        if session.finalise_task is not None and not session.finalise_task.done():
            session.finalise_task.cancel()
        session.finalise_task = asyncio.create_task(
            self._finalise_utterance(session, timeout_driven=True)
        )

    async def _finalise_utterance(
        self, session: UtteranceSession, *, timeout_driven: bool
    ) -> None:
        utterance_id = session.buffer.utterance_id
        if timeout_driven:
            try:
                await asyncio.sleep(self._config.asr.utterance_timeout_s)
            except asyncio.CancelledError:
                return  # an explicit end arrived first
        try:
            await self._transcribe(session.buffer)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad utterance must not kill the relay
            LOGGER.exception("Utterance %d failed to transcribe", utterance_id)
            await self._hub.broadcast_json(
                "relay_notice",
                {"level": "error", "code": "ASR_FAILED", "message": f"utterance {utterance_id}"},
            )
        finally:
            self._sessions.pop(utterance_id, None)

    async def _transcribe(self, buffer: UtteranceBuffer) -> None:
        buffer.finalise(minimum_preroll_ms=self._config.asr.preroll_ms)
        if not self._asr.available:
            LOGGER.debug("ASR unavailable; skipping transcript for %d", buffer.utterance_id)
            return
        if buffer.duration_ms() < self._config.asr.min_utterance_ms:
            LOGGER.debug("Utterance %d too short to transcribe", buffer.utterance_id)
            return
        pcm = buffer.decode()
        transcript = await run_in_thread(self._asr, pcm)
        first_token_ms = self._latency.mark_first_token(buffer.utterance_id)
        sample = self._latency.settle(buffer.utterance_id)
        await self._hub.broadcast_json(
            "transcript",
            transcript_payload(
                utterance_id=buffer.utterance_id,
                text=transcript.text,
                first_word=transcript.first_word,
                first_word_ms=transcript.first_word_ms,
                preroll_ms=buffer.preroll_duration_ms(),
                pre_roll_complete=buffer.pre_roll_complete,
                asr_ms=first_token_ms,
                confidence=transcript.confidence,
            ),
        )
        if sample is not None:
            await self._hub.broadcast_json(
                "latency", {"utterance_id": sample.utterance_id, "keyword_end_to_first_byte_ms": round(sample.total_ms, 1)}
            )
        if not buffer.pre_roll_complete:
            await self._hub.broadcast_json(
                "relay_notice",
                {
                    "level": "warn",
                    "code": buffer.preroll_issue or "PREROLL_UNKNOWN",
                    "message": f"utterance {buffer.utterance_id} has no trustworthy pre-roll",
                },
            )

    # -- observer link ------------------------------------------------------ #

    async def _handle_observer(self, connection: ServerConnection) -> None:
        if self._hub.is_full:
            await connection.close(HOST_CLOSE_CODE_POLICY, "observer limit reached")
            LOGGER.warning("Refused observer: %d already connected", self._hub.count)
            return
        self._hub.add(connection)
        self._stats.observer_connects += 1
        LOGGER.info("Observer connected (%d total)", self._hub.count)
        try:
            await self._announce_status(connected=self._device is not None, reason=None, only=connection)
            async for message in connection:
                await self._consume_observer_message(message)
        except ConnectionClosed:
            LOGGER.info("Observer disconnected")
        finally:
            self._hub.remove(connection)
            LOGGER.info("Observer left (%d remaining)", self._hub.count)

    async def _consume_observer_message(self, message: str | bytes) -> None:
        if not isinstance(message, str):
            return
        try:
            parsed = json.loads(message)
        except json.JSONDecodeError:
            LOGGER.info("Ignoring non-JSON observer message")
            return
        if not isinstance(parsed, dict) or parsed.get("type") != "command":
            return
        await self._forward_command_to_device(parsed.get("payload") or {})

    async def _forward_command_to_device(self, payload: dict[str, Any]) -> None:
        if self._device is None:
            await self._hub.broadcast_json(
                "relay_notice", {"level": "warn", "code": "DEVICE_OFFLINE", "message": "command dropped"}
            )
            return
        command = json.dumps({"v": 1, **payload}, separators=(",", ":"))
        with contextlib.suppress(ConnectionClosed):
            await self._device.send(command)
        LOGGER.info("Forwarded command to device: %s", command[:120])

    # -- status + heartbeat ------------------------------------------------- #

    async def _announce_status(
        self, *, connected: bool, reason: str | None, only: ServerConnection | None = None
    ) -> None:
        payload = device_status_payload(connected=connected, reason=reason, boot=self._boot)
        text = observer_envelope("device_status", payload)
        if only is not None:
            with contextlib.suppress(ConnectionClosed):
                await only.send(text)
            return
        await self._hub.broadcast_text(text)

    def start_heartbeat(self) -> asyncio.Task[None]:
        return asyncio.create_task(self._heartbeat_loop())

    async def _heartbeat_loop(self) -> None:
        """Ping everyone on a fixed cadence and detect a silently wedged device.

        The original server set ``ping_interval=None`` to stop the AI math from
        tripping a disconnect. That traded a false disconnect for a far worse
        fault: a frozen device looked healthy indefinitely. This loop keeps
        pings but treats silence as a *reported* state instead of a silent one.
        """
        interval = self._config.relay.heartbeat_interval_s
        stale_after = self._config.relay.device_stale_after_s
        while not self._heartbeat_stop.is_set():
            await asyncio.sleep(interval)
            await self._tick_heartbeat(stale_after)

    async def _tick_heartbeat(self, stale_after: float) -> None:
        loop = asyncio.get_running_loop()
        now = loop.time()
        if self._device is not None:
            with contextlib.suppress(ConnectionClosed):
                await self._device.send(
                    json.dumps({"v": 1, "cmd": "ping", "payload": {"t": now}}, separators=(",", ":"))
                )
            idle_for = None if self._last_device_frame_at is None else now - self._last_device_frame_at
            if idle_for is not None and idle_for > stale_after:
                LOGGER.warning("Device silent for %.1fs; reporting stale", idle_for)
                await self._announce_status(connected=False, reason="timeout")
                with contextlib.suppress(Exception):
                    await self._device.close()
        await self._hub.broadcast_json("ping", {"t": now})

    def _next_legacy_seq(self) -> int:
        """Monotonic sequence source for synthesised legacy frames.

        The legacy protocol has no counter of its own, so the relay supplies
        one. That is precisely the gap v1 closed with a real ``seq`` field.
        """
        self._legacy_seq += 1
        return self._legacy_seq

    def status_snapshot(self) -> dict[str, Any]:
        """Diagnostics for the relay's stdout banner and tests."""
        """Diagnostics for the relay's own stdout banner and tests."""
        return {
            "observers": self._hub.count,
            "device_connected": self._device is not None,
            "class_order": list(self._boot.class_order) if self._boot else None,
            "stats": self._stats.as_payload(),
            "latency_p95_ms": self._latency.percentile(0.95),
        }


async def run_relay(config: AppConfig, asr_engine: AsrEngine) -> None:
    """Entry point used by ``python -m edge_voice``."""
    relay = TelemetryRelay(config, asr_engine)
    heartbeat = relay.start_heartbeat()
    try:
        await relay.serve_forever()
    finally:
        relay.request_stop()
        heartbeat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await heartbeat
