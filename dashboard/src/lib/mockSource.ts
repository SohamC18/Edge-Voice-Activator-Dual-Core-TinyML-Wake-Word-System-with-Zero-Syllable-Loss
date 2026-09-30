/**
 * Mock device source.
 *
 * This exists so the dashboard can be developed, visually validated, and
 * demoed without an ESP32 on the bench. It emits *real protocol frames* rather
 * than fake UI state, which means the mock exercises exactly the same reducer,
 * the same class-order mapping, and the same "absent means unavailable" rules as
 * the live path. If a panel renders correctly here, it renders correctly against
 * hardware.
 *
 * Two modes:
 *
 *  - `telemetry` — a compliant device speaking protocol v1.
 *  - `legacy`    — the pre-protocol serial format, adapted by
 *                  `edge_voice.legacy` on the host. Frames are marked
 *                  `synthetic: true` and the UI badges them, because a judge
 *                  must never mistake parsed serial output for device-reported
 *                  measurement.
 */

import type { BootFrame, ClassOrder, ObserverFrame } from '../types/protocol';
import { PROTOCOL_VERSION, RAM_BUDGET_BYTES } from '../types/protocol';

/**
 * The class order documented in `docs/legacy/kws_model_progress.md`, which
 * disagrees with the pre-patch firmware. Used by the legacy mock so the
 * alias-handling path is exercised for real.
 */
export const LEGACY_CLASS_ORDER: ClassOrder = ['friday', 'negative', 'silence'];
export const V1_CLASS_ORDER: ClassOrder = ['silence', 'unknown', 'friday'];

export type MockMode = 'telemetry' | 'legacy';

export interface MockOptions {
  readonly mode?: MockMode;
  readonly classOrder?: ClassOrder;
  /** Simulated probability that an open gate produces an accepted keyword. */
  readonly acceptRate?: number;
  /** Omit `cpu_idle_pct` to exercise the "unavailable" rendering path. */
  readonly reportCpu?: boolean;
  readonly seed?: number;
}

const GATE_WINDOW_MS = 600;

/** Deterministic PRNG so visual-regression screenshots are stable. */
function makeRandom(seed: number): () => number {
  let state = seed >>> 0 || 1;
  return () => {
    // xorshift32
    state ^= state << 13;
    state >>>= 0;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    return state / 0xffffffff;
  };
}

function envelope(ev: string, seq: number, t: number, wall: number, extra: Record<string, unknown>): string {
  return JSON.stringify({ v: PROTOCOL_VERSION, ev, seq, t, wall, ...extra });
}

/**
 * Produces observer frames on a timer, mimicking a device going through the
 * gate -> inference -> decision -> commit -> streaming pipeline.
 */
export class MockDevice {
  private readonly random: () => number;
  private readonly mode: MockMode;
  private readonly classOrder: ClassOrder;
  private readonly acceptRate: number;
  private readonly reportCpu: boolean;

  private timer: number | null = null;
  private seq = 0;
  private t = 0;
  private startedAt = Date.now();
  private utteranceId = 0;
  private lastAcceptedUptime = -10_000;
  private phase: 'idle' | 'speaking' = 'idle';
  private phaseEndsAt = 0;

  constructor(options: MockOptions = {}) {
    this.mode = options.mode ?? 'telemetry';
    this.classOrder = options.classOrder ?? V1_CLASS_ORDER;
    this.acceptRate = options.acceptRate ?? 0.45;
    this.reportCpu = options.reportCpu ?? true;
    this.random = makeRandom(options.seed ?? 0x5eed1234);
  }

  start(emit: (frame: ObserverFrame) => void): () => void {
    this.emitBoot(emit);
    // Emit immediately so the first paint is populated, then on a 600 ms
    // cadence that matches the device's real analysis window.
    const tick = () => {
      this.step(emit);
    };
    tick();
    this.timer = globalThis.setInterval(tick, GATE_WINDOW_MS) as unknown as number;
    return () => this.stop();
  }

  stop(): void {
    if (this.timer !== null) {
      globalThis.clearInterval(this.timer);
      this.timer = null;
    }
  }

  private nextSeq(): number {
    this.seq += 1;
    return this.seq;
  }

  private wall(): number {
    // Device wall clock tracks elapsed time since the mock started.
    return this.startedAt + this.t;
  }

  private emitBoot(emit: (frame: ObserverFrame) => void): void {
    // Typed as BootFrame rather than left to inference: `ev` would widen to
    // `string`, which is not assignable to the discriminated union the observer
    // frame expects.
    const boot: BootFrame = {
      v: PROTOCOL_VERSION,
      ev: 'boot',
      seq: this.nextSeq(),
      t: this.t,
      wall: this.wall(),
      fw: this.mode === 'legacy' ? 'main_kscnn/0.9.0-legacy' : 'edge-kws/1.0.0',
      chip: 'ESP32-D0WDQ6',
      arena_bytes: 30_720,
      class_order: this.classOrder,
      quant: {
        input_scale: 0.0078431377,
        input_zero_point: -128,
        output_scale: 0.00390625,
        output_zero_point: -128,
      },
      free_heap: 142_336,
      flash_used: 1_088_128,
      sdk: 'v4.4.7',
    };
    emit({ v: PROTOCOL_VERSION, type: 'device_status', payload: { connected: true, reason: null, boot } });
    emit({ v: PROTOCOL_VERSION, type: 'telemetry', payload: boot });

    emit({
      v: PROTOCOL_VERSION,
      type: 'relay_notice',
      payload:
        this.mode === 'legacy'
          ? {
              level: 'warning',
              message: 'Device is speaking the pre-v1 serial format; frames are adapted and marked synthetic.',
              code: 'LEGACY_SOURCE',
            }
          : { level: 'info', message: 'Mock device attached (development mode).', code: 'MOCK_SOURCE' },
    });
  }

  private step(emit: (frame: ObserverFrame) => void): void {
    this.t += GATE_WINDOW_MS;

    // --- speech / silence phase machine -----------------------------------
    if (this.t >= this.phaseEndsAt) {
      this.phase = this.phase === 'idle' ? 'speaking' : 'idle';
      this.phaseEndsAt = this.t + (this.phase === 'speaking' ? 1800 + this.random() * 2400 : 1500 + this.random() * 3000);
    }
    const speaking = this.phase === 'speaking';

    // --- stage ------------------------------------------------------------
    const stage = speaking ? 'stage1_ai' : 'idle';
    if (this.mode === 'legacy') {
      // Legacy firmware has no stage frames at all; the host cannot invent one.
    } else {
      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: JSON.parse(
          envelope('stage', this.nextSeq(), this.t, this.wall(), {
            stage,
            detail: speaking ? 'dscnn inference 4040x1 int8' : 'rms below threshold',
          }),
        ) as never,
      });
    }

    // --- gate -------------------------------------------------------------
    const threshold = 300;
    const rms = speaking ? 420 + this.random() * 900 : this.random() * 160;
    const open = rms > threshold;
    if (this.mode === 'legacy') {
      // Legacy path: the raw serial line, adapted host-side. The relay's
      // legacy adapter is what turns this into a gate frame; the mock
      // approximates that by emitting a synthetic gate.
      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: {
          ...(JSON.parse(envelope('gate', this.nextSeq(), this.t, this.wall(), { rms: Math.round(rms), threshold, open, window_ms: GATE_WINDOW_MS })) as object),
          synthetic: true,
        } as never,
      });
    } else {
      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: JSON.parse(
          envelope('gate', this.nextSeq(), this.t, this.wall(), { rms: Math.round(rms), threshold, open, window_ms: GATE_WINDOW_MS }),
        ) as never,
      });
    }

    // --- inference --------------------------------------------------------
    const keywordIndex = this.classOrder.indexOf('friday' as never);
    const keywordName = keywordIndex >= 0 ? this.classOrder[keywordIndex] : this.classOrder[2];
    const p = this.classOrder.map((name) => {
      if (name === 'silence') return speaking ? 0.02 + this.random() * 0.08 : 0.86 + this.random() * 0.13;
      if (name === keywordName) return speaking ? 0.55 + this.random() * 0.44 : 0.01 + this.random() * 0.04;
      return 0.01 + this.random() * 0.06;
    });
    // Softmax must sum to 1; the host validator enforces this, so normalise.
    const sum = p.reduce((acc, value) => acc + value, 0);
    const normalised = p.map((value) => Number((value / sum).toFixed(4)));

    if (this.mode === 'legacy') {
      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: {
          ...(JSON.parse(
            envelope('ai_result', this.nextSeq(), this.t, this.wall(), {
              p: normalised,
              inference_ms: 38 + Math.round(this.random() * 12),
              arena_bytes: 30_720,
            }),
          ) as object),
          synthetic: true,
        } as never,
      });
    } else {
      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: JSON.parse(
          envelope('ai_result', this.nextSeq(), this.t, this.wall(), {
            p: normalised,
            inference_ms: 38 + Math.round(this.random() * 12),
            arena_bytes: 30_720,
            dropped: 0,
          }),
        ) as never,
      });
    }

    // --- decision ---------------------------------------------------------
    const keywordProb = normalised[keywordIndex >= 0 ? keywordIndex : 2];
    const keywordThreshold = 0.5;
    const sinceLast = this.t - this.lastAcceptedUptime;
    const debounce = 2000;

    let accepted = false;
    let reason: string;
    if (!open) {
      reason = 'gate_closed';
    } else if (keywordProb < keywordThreshold) {
      reason = 'friday<thr';
    } else if (sinceLast < debounce) {
      reason = 'debounce';
    } else {
      accepted = this.random() < this.acceptRate;
      reason = accepted ? 'friday>=thr' : 'friday<thr';
    }

    if (accepted) this.lastAcceptedUptime = this.t;

    if (this.mode === 'legacy') {
      const pct = (value: number) => `${Math.round(value * 100)}%`;
      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: {
          v: PROTOCOL_VERSION,
          ev: 'decision',
          seq: this.nextSeq(),
          t: this.t,
          wall: this.wall(),
          accept: accepted,
          reason,
          keyword_prob: keywordProb,
          threshold: keywordThreshold,
          synthetic: true,
          legacy_line: `[AI] Sil ${pct(normalised[0])} | Unk ${pct(normalised[1])} | Fri ${pct(normalised[2])}`,
        } as never,
      });
    } else {
      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: JSON.parse(
          envelope('decision', this.nextSeq(), this.t, this.wall(), {
            accept: accepted,
            reason,
            keyword_prob: keywordProb,
            threshold: keywordThreshold,
            since_last_ms: Math.max(0, sinceLast),
            debounce_ms: debounce,
          }),
        ) as never,
      });
    }

    // --- commit -> transcript + latency -----------------------------------
    if (accepted) {
      this.utteranceId = (this.utteranceId + 1) % 65536;
      const firstWord = this.random() < 0.5 ? 'play' : 'stop';
      const asrMs = 240 + Math.round(this.random() * 220);
      const firstWordMs = 150 + Math.round(this.random() * 90);

      emit({
        v: PROTOCOL_VERSION,
        type: 'telemetry',
        payload: JSON.parse(envelope('stage', this.nextSeq(), this.t, this.wall(), { stage: 'streaming', detail: 'opus uplink open' })) as never,
      });

      if (this.mode !== 'legacy') {
        emit({
          v: PROTOCOL_VERSION,
          type: 'transcript',
          payload: {
            utterance_id: this.utteranceId,
            text: `${firstWord} some jazz`,
            first_word: firstWord,
            first_word_ms: firstWordMs,
            preroll_ms: 300,
            pre_roll_complete: true,
            asr_ms: asrMs,
            confidence: 0.88 + this.random() * 0.11,
            final: true,
          },
        });
        emit({
          v: PROTOCOL_VERSION,
          type: 'latency',
          payload: { utterance_id: this.utteranceId, keyword_end_to_first_byte_ms: asrMs + firstWordMs + 60 + Math.round(this.random() * 120) },
        });
      }
    }

    // --- periodic health (1 s) -------------------------------------------
    if (this.t % 1000 === 0) {
      const sys: Record<string, unknown> = {
        free_heap: 142_000 - Math.round(this.random() * 6_000),
        min_free_heap: 118_272,
        largest_free: 118_272,
        uptime_ms: this.t,
        rssi: -52 - Math.round(this.random() * 12),
        audio_dropped_chunks: 0,
        serial_overruns: 0,
      };
      if (this.reportCpu) {
        sys.cpu_idle_pct = Number((86 + this.random() * 12).toFixed(1));
      }
      const ram = {
        budget_bytes: RAM_BUDGET_BYTES,
        segments: {
          tensor_arena: 30_720,
          mel_db: 16_160,
          pcm_ring: 32_000,
          preroll_ring: 24_000,
          opus_encoder: 12_288,
          rtos_stacks: 24_576,
          other: 20_000 + Math.round(this.random() * 4_000),
        },
      };
      if (this.mode !== 'legacy') {
        emit({ v: PROTOCOL_VERSION, type: 'telemetry', payload: JSON.parse(envelope('sys', this.nextSeq(), this.t, this.wall(), sys)) as never });
        emit({ v: PROTOCOL_VERSION, type: 'telemetry', payload: JSON.parse(envelope('ram', this.nextSeq(), this.t, this.wall(), ram)) as never });
      }
    }
  }
}

/** Convenience: a `device_status` frame marking the device as gone. */
export function mockDisconnect(reason = 'timeout'): ObserverFrame {
  return { v: PROTOCOL_VERSION, type: 'device_status', payload: { connected: false, reason } };
}
