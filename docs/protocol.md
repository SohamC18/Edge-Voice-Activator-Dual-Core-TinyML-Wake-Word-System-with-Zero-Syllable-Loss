# Edge Voice Activator — Telemetry Protocol v1

Status: **frozen for the SIH 2026 demo.** Any change is a major version bump.

Two WebSocket endpoints on the host relay (`ws://<host>:8765`):

| Endpoint | Direction | Cardinality | Purpose |
| --- | --- | --- | --- |
| `/device` | ESP32 → host | exactly 1 active | Telemetry + Opus uplink |
| `/ws` | browser → host | 0..N | Fan-out to any number of observers |

The host relay is a **fan-out**: the single ESP32 link is broadcast to every
connected browser, so a projector and a judge's laptop can watch simultaneously.
This is the fix for the original `server.py`, which was print-only and
single-client.

---

## 1. Design rules

1. **Every frame is a complete record.** No streaming deltas, no patch semantics.
   A late-joining browser can render the full current state from one frame.
2. **The device never names a class index.** It sends three probabilities in
   wire order and separately declares `class_order` in its `boot` frame. The
   dashboard renders names, not indices. This makes the
   `kws_model_progress.md` vs `main.ino` class-order contradiction a runtime
   configuration value rather than a hardcoded guess — see
   [ADR-0001](adr/0001-telemetry-protocol.md).
3. **A claim the device cannot measure is not sent.** No estimated CPU, no
   estimated arena. If a counter is unavailable it is omitted, and the dashboard
   renders "unavailable" instead of a fabricated number.
4. **No audio is retained.** The relay never writes PCM or Opus to disk. The
   Opus ring buffer lives only in device RAM and only spans the pre-roll window.
5. **Every counter is monotonic and named.** `seq` is global and strictly
   increasing; `dropped` counters are cumulative, so silence is impossible to
   confuse with "no data".

---

## 2. Device → Host frames

Text frames carrying newline-free compact JSON. Common envelope:

```jsonc
{
  "v": 1,              // schema version, integer
  "ev": "ai_result",   // event discriminator
  "seq": 412,          // global, strictly increasing, never reused
  "t": 91234,          // milliseconds since boot (device monotonic clock)
  "wall": 1756000000123 // device wall clock, ms since Unix epoch
}
```

### 2.1 `boot` — sent once, immediately after model load

```jsonc
{
  "v": 1, "ev": "boot", "seq": 1, "t": 812, "wall": 1756000000000,
  "fw": "edge-kws/1.0.0",
  "chip": "ESP32-D0WDQ6",
  "arena_bytes": 30720,
  "class_order": ["silence", "unknown", "friday"],
  "quant": { "input_scale": 0.0078431377, "input_zero_point": -128,
             "output_scale": 0.00390625, "output_zero_point": -128 },
  "free_heap": 142336, "flash_used": 1088128, "sdk": "v4.4.7"
}
```

`class_order[i]` is the human name of output index `i`. Permitted values:
`silence`, `unknown`, `friday`. The dashboard renders the probabilities by
these names and refuses to draw a "Friday" series if `friday` is absent.

### 2.2 `stage` — drives the pipeline state badge

```jsonc
{ "v":1, "ev":"stage", "seq":402, "t":90311, "wall":…,
  "stage": "stage1_ai",
  "detail": "dscnn inference 4040x1 int8" }
```

Allowed `stage` values, in pipeline order:

`idle` → `gate_open` → `stage1_ai` → `stage2_verify` → `commit` → `streaming`

### 2.3 `gate` — one per 600 ms analysis window

```jsonc
{ "v":1, "ev":"gate", "seq":403, "t":90600, "wall":…,
  "rms": 812.4, "threshold": 300.0, "open": true, "window_ms": 600 }
```

`rms` is the true measured RMS of the window, not a boolean. The dashboard
renders a real energy trace against the threshold line.

### 2.4 `ai_result` — one per completed inference

```jsonc
{ "v":1, "ev":"ai_result", "seq":404, "t":91050, "wall":…,
  "p": [0.02, 0.11, 0.87],
  "inference_ms": 41, "arena_bytes": 30720, "dropped": 0 }
```

`p` is in `class_order` order. `dropped` is the cumulative count of inference
windows discarded because the AI core was still busy — non-zero means the
"Stage-1" figure is under-reporting, and the dashboard surfaces it.

### 2.5 `decision` — the accept/reject verdict

```jsonc
{ "v":1, "ev":"decision", "seq":405, "t":91051, "wall":…,
  "accept": true, "reason": "friday>=0.50",
  "keyword_prob": 0.87, "threshold": 0.5,
  "since_last_ms": 4180, "debounce_ms": 2000 }
```

`reason` is a stable machine token, not prose, so the dashboard can group
rejections: `friday>=thr`, `friday<thr`, `debounce`, `gate_closed`, `ai_busy`.

### 2.6 `sys` — periodic health, default every 1000 ms

```jsonc
{ "v":1, "ev":"sys", "seq":406, "t":91800, "wall":…,
  "free_heap": 142336, "min_free_heap": 118272, "largest_free": 118272,
  "cpu_idle_pct": 92.4, "uptime_ms": 91800, "rssi": -58,
  "audio_dropped_chunks": 0, "serial_overruns": 0 }
```

`cpu_idle_pct` requires FreeRTOS run-time stats; if not enabled in the build it
is **omitted** and the dashboard shows "unavailable". See ADR-0004.

### 2.7 `ram` — the measured memory ledger, default every 1000 ms

```jsonc
{ "v":1, "ev":"ram", "seq":407, "t":91900, "wall":…,
  "budget_bytes": 262144,
  "segments": { "tensor_arena": 30720, "mel_db": 16160, "pcm_ring": 32000,
                "preroll_ring": 24000, "opus_encoder": 12288,
                "rtos_stacks": 24576, "other": 0 } }
```

The sum of `segments` is what the stacked bar renders. `other` is the residual
`free_heap` not attributed to a named static pool — it is derived, not guessed.
`budget_bytes` is 256 KiB.

### 2.8 Opus uplink (binary frame, not JSON)

Sent only after a `commit` decision, and only for the duration of the command.
Header is 8 bytes, little-endian:

| Offset | Size | Field | Notes |
| --- | --- | --- | --- |
| 0 | 1 | `kind` | `0x01` = audio frame, `0x02` = pre-roll, `0x03` = end-of-utterance |
| 1 | 1 | `flags` | bit0 = marks the **first** frame of the pre-roll block |
| 2 | 2 | `utterance_id` | wraps at 65535 |
| 4 | 4 | `seq` | per-utterance frame counter |

Payload is a 20 ms Opus frame (16 kHz, mono, VOIP).

`kind = 0x02` frames carry the **pre-roll block**: the 300 ms of audio that
preceded the energy-gate trip, captured in the device ring buffer. The ASR
service decodes pre-roll and live frames into one continuous buffer *before*
running recognition. That is the mechanism that prevents a clipped first word,
and `first_word_ms` in the resulting transcript is the number that proves it.

### 2.9 `error`

```jsonc
{ "v":1, "ev":"error", "seq":408, "t":92000, "wall":…,
  "code": "AI_TFLM_ALLOC", "message": "arena 30720 too small" }
```

---

## 3. Host → Device frames

```jsonc
{ "v":1, "cmd":"config",   "payload": { "rms_threshold": 300, "keyword_threshold": 0.5, "debounce_ms": 2000, "preroll_ms": 300 } }
{ "v":1, "cmd":"arm" }      // begin evaluating
{ "v":1, "cmd":"disarm" }   // stop evaluating, keep streaming
{ "v":1, "cmd":"reset_counters" }
{ "v":1, "cmd":"ping", "payload": { "t": 1756000000123 } }
```

The device answers a `ping` with `{"ev":"pong","payload":{"t":<echo>}}` so the
relay can compute round-trip time without relying on TCP keepalive.

---

## 4. Host → Observer frames

```jsonc
{ "v":1, "type":"device_status", "payload": { "connected": true, "reason": null, "boot": {…} } }
{ "v":1, "type":"telemetry",     "payload": { …device frame, verbatim… } }
{ "v":1, "type":"transcript",    "payload": { "utterance_id": 7, "text": "play some jazz",
                                              "first_word": "play", "first_word_ms": 180,
                                              "preroll_ms": 300, "pre_roll_complete": true,
                                              "asr_ms": 310, "confidence": 0.94, "final": true } }
{ "v":1, "type":"latency",       "payload": { "utterance_id": 7, "keyword_end_to_first_byte_ms": 612 } }
{ "v":1, "type":"ping",          "payload": { "t": 1756000000123 } }
```

`device_status` is sent immediately on observer connect and again on every
change. `payload.boot` is the last `boot` frame received, so a browser joining
mid-demo can label the classes and show the memory budget without waiting for
the device to reboot.

---

## 5. Heartbeat and liveness

The original `server.py` set `ping_interval=None`, which meant a wedged ESP32
looked connected forever. v1 replaces that with an explicit application-level
heartbeat:

* Host → device `ping` every **2000 ms**; device replies `pong`.
* Host → observer `ping` every **2000 ms**.
* No device frame of any kind for **5000 ms** ⇒ emit
  `device_status {"connected": false, "reason": "timeout"}` and set the
  dashboard to a stale state. Telemetry is never silently frozen.

---

## 6. Binary Opus framing note

A raw Opus frame carries no length, so frames are delimited by the WebSocket
message boundary, not by a length field. `MAX_PACKET_SIZE` on the device is
1000 bytes; the relay must reject any binary frame longer than
`OPUS_MAX_FRAME_BYTES` (1024) and raise `error.code = "OPUS_FRAME_TOO_LARGE"`
rather than buffering it.

---

## 7. Conformance

`server/src/edge_voice/protocol.py` is the single source of truth for this
document. `tests/test_protocol.py` asserts every frame shape in this spec; if
you change the spec and the tests disagree, the tests are the bug.
