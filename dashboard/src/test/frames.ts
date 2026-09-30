/**
 * Frame builders.
 *
 * Tests construct telemetry through these rather than inline object literals so
 * that a change to the protocol types breaks the builders (loudly) instead of
 * silently letting a test send a frame the real relay would reject.
 */

import type {
  AiResultFrame,
  BootFrame,
  ClassOrder,
  DecisionFrame,
  GateFrame,
  ObserverFrame,
  RamFrame,
  StageFrame,
  SysFrame,
  TranscriptPayload,
} from '../types/protocol';
import { PROTOCOL_VERSION } from '../types/protocol';

let seq = 0;
export function resetSeq(): void {
  seq = 0;
}
function next(): number {
  seq += 1;
  return seq;
}

export function bootFrame(overrides: Partial<BootFrame> = {}): BootFrame {
  return {
    v: PROTOCOL_VERSION,
    ev: 'boot',
    seq: next(),
    t: 812,
    wall: 1_756_000_000_000,
    fw: 'edge-kws/1.0.0',
    chip: 'ESP32-D0WDQ6',
    arena_bytes: 30_720,
    class_order: ['silence', 'unknown', 'friday'] as ClassOrder,
    quant: { input_scale: 0.0078431377, input_zero_point: -128, output_scale: 0.00390625, output_zero_point: -128 },
    free_heap: 142_336,
    ...overrides,
  };
}

export function stageFrame(stage: string, detail?: string, t = 1000): StageFrame {
  return { v: PROTOCOL_VERSION, ev: 'stage', seq: next(), t, wall: 1_756_000_000_000, stage, ...(detail ? { detail } : {}) };
}

export function gateFrame(overrides: Partial<GateFrame> = {}): GateFrame {
  return {
    v: PROTOCOL_VERSION,
    ev: 'gate',
    seq: next(),
    t: 9_000,
    wall: 1_756_000_000_000,
    rms: 812.4,
    threshold: 300,
    open: true,
    window_ms: 600,
    ...overrides,
  };
}

export function aiResultFrame(p: [number, number, number], overrides: Partial<AiResultFrame> = {}): AiResultFrame {
  return { v: PROTOCOL_VERSION, ev: 'ai_result', seq: next(), t: 9_100, wall: 1_756_000_000_000, p, ...overrides };
}

export function decisionFrame(overrides: Partial<DecisionFrame> = {}): DecisionFrame {
  return {
    v: PROTOCOL_VERSION,
    ev: 'decision',
    seq: next(),
    t: 9_101,
    wall: 1_756_000_000_000,
    accept: true,
    reason: 'friday>=thr',
    keyword_prob: 0.87,
    threshold: 0.5,
    since_last_ms: 4_180,
    debounce_ms: 2_000,
    ...overrides,
  };
}

export function sysFrame(overrides: Partial<SysFrame> = {}): SysFrame {
  return {
    v: PROTOCOL_VERSION,
    ev: 'sys',
    seq: next(),
    t: 9_200,
    wall: 1_756_000_000_000,
    free_heap: 142_336,
    min_free_heap: 118_272,
    largest_free: 118_272,
    cpu_idle_pct: 92.4,
    uptime_ms: 91_800,
    rssi: -58,
    audio_dropped_chunks: 0,
    serial_overruns: 0,
    ...overrides,
  };
}

export function ramFrame(overrides: Partial<RamFrame> = {}): RamFrame {
  return {
    v: PROTOCOL_VERSION,
    ev: 'ram',
    seq: next(),
    t: 9_300,
    wall: 1_756_000_000_000,
    budget_bytes: 262_144,
    segments: {
      tensor_arena: 30_720,
      mel_db: 16_160,
      pcm_ring: 32_000,
      preroll_ring: 24_000,
      opus_encoder: 12_288,
      rtos_stacks: 24_576,
      other: 20_000,
    },
    ...overrides,
  };
}

export function transcript(overrides: Partial<TranscriptPayload> = {}): TranscriptPayload {
  return {
    utterance_id: 7,
    text: 'play some jazz',
    first_word: 'play',
    first_word_ms: 180,
    preroll_ms: 300,
    pre_roll_complete: true,
    asr_ms: 310,
    confidence: 0.94,
    final: true,
    ...overrides,
  };
}

export function telemetry(frame: Parameters<typeof asTelemetry>[0]): ObserverFrame {
  return asTelemetry(frame);
}

export function asTelemetry(frame: BootFrame | StageFrame | GateFrame | AiResultFrame | DecisionFrame | SysFrame | RamFrame) {
  return { v: PROTOCOL_VERSION, type: 'telemetry', payload: frame } as ObserverFrame;
}

export function status(connected: boolean, reason: string | null = null, boot?: BootFrame) {
  return { v: PROTOCOL_VERSION, type: 'device_status', payload: { connected, reason, ...(boot ? { boot } : {}) } } as ObserverFrame;
}

export function transcriptFrame(payload: TranscriptPayload = transcript()): ObserverFrame {
  return { v: PROTOCOL_VERSION, type: 'transcript', payload };
}

export function latencyFrame(utteranceId: number, ms: number): ObserverFrame {
  return { v: PROTOCOL_VERSION, type: 'latency', payload: { utterance_id: utteranceId, keyword_end_to_first_byte_ms: ms } };
}
