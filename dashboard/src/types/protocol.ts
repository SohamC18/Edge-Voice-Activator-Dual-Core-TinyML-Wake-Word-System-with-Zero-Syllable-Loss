/**
 * TypeScript mirror of `docs/protocol.md` (telemetry protocol v1).
 *
 * This file is a *mirror*, not the source of truth — `server/src/edge_voice/protocol.py`
 * is. If the two disagree, the Python module is right (see §7 of the spec).
 *
 * A deliberate modelling decision: nearly every measured field is optional.
 * Protocol design rule 3 says a claim the device cannot measure is not sent.
 * Making those fields optional here is what forces the UI to render
 * "unavailable" instead of silently coercing `undefined` to `0`, which is the
 * difference between an honest instrument and a decorative one.
 */

export const PROTOCOL_VERSION = 1 as const;

export const HOST_PORT = 8765;
export const DEVICE_PATH = '/device';
export const OBSERVER_PATH = '/ws';

/** Application-level heartbeat cadence and the staleness deadline. */
export const PING_INTERVAL_MS = 2000;
export const STALE_AFTER_MS = 5000;

export const RAM_BUDGET_BYTES = 256 * 1024;

// --------------------------------------------------------------------------- //
// Device -> host frames
// --------------------------------------------------------------------------- //

/**
 * Permitted class names.
 *
 * `negative` is the documented alias for `unknown`: `docs/legacy/kws_model_progress.md`
 * names index 1 "negative" while the pre-patch firmware printed it as "Unk".
 * The host relay accepts both, and the UI must too, otherwise a device
 * declaring the documented order would fail to render a series.
 */
export type ClassName = 'silence' | 'unknown' | 'friday' | 'negative';

/** The device declares its own index->name mapping in the boot frame. */
export type ClassOrder = readonly [ClassName, ClassName, ClassName];

export const DEFAULT_CLASS_ORDER: ClassOrder = ['silence', 'unknown', 'friday'];

/**
 * Pipeline stages in execution order. Used to decide whether the pipeline badge
 * should look like it is advancing or stuck.
 */
export const PIPELINE_STAGES = [
  'idle',
  'gate_open',
  'stage1_ai',
  'stage2_verify',
  'commit',
  'streaming',
] as const;

export type PipelineStage = (typeof PIPELINE_STAGES)[number];

/** Stable machine tokens, so rejections can be grouped rather than parsed. */
export const DECISION_REASONS = [
  'friday>=thr',
  'friday<thr',
  'debounce',
  'gate_closed',
  'ai_busy',
] as const;

export type DecisionReason = (typeof DECISION_REASONS)[number] | string;

export interface QuantizationParams {
  readonly input_scale: number;
  readonly input_zero_point: number;
  readonly output_scale: number;
  readonly output_zero_point: number;
}

export interface RamSegments {
  readonly tensor_arena?: number;
  readonly mel_db?: number;
  readonly pcm_ring?: number;
  readonly preroll_ring?: number;
  readonly opus_encoder?: number;
  readonly rtos_stacks?: number;
  readonly other?: number;
}

interface DeviceFrameBase {
  readonly v: typeof PROTOCOL_VERSION;
  readonly seq: number;
  /** ms since boot, device monotonic clock. */
  readonly t: number;
  /** ms since Unix epoch, device wall clock. */
  readonly wall?: number;
}

export interface BootFrame extends DeviceFrameBase {
  readonly ev: 'boot';
  readonly fw: string;
  readonly chip: string;
  readonly arena_bytes: number;
  readonly class_order: ClassOrder;
  readonly quant: QuantizationParams;
  readonly free_heap?: number;
  readonly flash_used?: number;
  readonly sdk?: string;
}

export interface StageFrame extends DeviceFrameBase {
  readonly ev: 'stage';
  readonly stage: PipelineStage | string;
  readonly detail?: string;
}

export interface GateFrame extends DeviceFrameBase {
  readonly ev: 'gate';
  readonly rms: number;
  readonly threshold: number;
  readonly open: boolean;
  readonly window_ms: number;
}

export interface AiResultFrame extends DeviceFrameBase {
  readonly ev: 'ai_result';
  /** Softmax output in `class_order` order. */
  readonly p: readonly [number, number, number];
  readonly inference_ms?: number;
  readonly arena_bytes?: number;
  /** Cumulative count of analysis windows dropped because the AI core was busy. */
  readonly dropped?: number;
}

export interface DecisionFrame extends DeviceFrameBase {
  readonly ev: 'decision';
  readonly accept: boolean;
  readonly reason: DecisionReason;
  readonly keyword_prob?: number;
  readonly threshold?: number;
  readonly since_last_ms?: number;
  readonly debounce_ms?: number;
}

export interface SysFrame extends DeviceFrameBase {
  readonly ev: 'sys';
  readonly free_heap?: number;
  readonly min_free_heap?: number;
  readonly largest_free?: number;
  /** Requires FreeRTOS run-time stats; omitted when not enabled. */
  readonly cpu_idle_pct?: number;
  readonly uptime_ms?: number;
  readonly rssi?: number;
  readonly audio_dropped_chunks?: number;
  readonly serial_overruns?: number;
}

export interface RamFrame extends DeviceFrameBase {
  readonly ev: 'ram';
  readonly budget_bytes: number;
  readonly segments: RamSegments;
}

export interface ErrorFrame extends DeviceFrameBase {
  readonly ev: 'error';
  readonly code: string;
  readonly message: string;
}

export interface PongFrame extends DeviceFrameBase {
  readonly ev: 'pong';
  readonly payload?: { readonly t?: number };
}

export type DeviceFrame =
  | BootFrame
  | StageFrame
  | GateFrame
  | AiResultFrame
  | DecisionFrame
  | SysFrame
  | RamFrame
  | ErrorFrame
  | PongFrame;

export type DeviceEvent = DeviceFrame['ev'];

// --------------------------------------------------------------------------- //
// Host -> observer frames
// --------------------------------------------------------------------------- //

export interface DeviceStatusPayload {
  readonly connected: boolean;
  readonly reason: string | null;
  readonly boot?: BootFrame;
}

export interface TranscriptPayload {
  readonly utterance_id: number;
  readonly text: string;
  readonly first_word?: string;
  /**
   * Offset from the start of the pre-roll window to the start of the first
   * word. This is the number that proves the first word was not clipped.
   */
  readonly first_word_ms?: number;
  readonly preroll_ms?: number;
  readonly pre_roll_complete?: boolean;
  readonly asr_ms?: number;
  readonly confidence?: number;
  readonly final: boolean;
}

export interface LatencyPayload {
  readonly utterance_id: number;
  readonly keyword_end_to_first_byte_ms: number;
}

export interface RelayNoticePayload {
  readonly level: 'info' | 'warning' | 'error';
  readonly message: string;
  readonly code?: string;
}

export type ObserverFrame =
  | { readonly v: typeof PROTOCOL_VERSION; readonly type: 'device_status'; readonly payload: DeviceStatusPayload }
  | { readonly v: typeof PROTOCOL_VERSION; readonly type: 'telemetry'; readonly payload: DeviceFrame }
  | { readonly v: typeof PROTOCOL_VERSION; readonly type: 'transcript'; readonly payload: TranscriptPayload }
  | { readonly v: typeof PROTOCOL_VERSION; readonly type: 'latency'; readonly payload: LatencyPayload }
  | { readonly v: typeof PROTOCOL_VERSION; readonly type: 'ping'; readonly payload: { readonly t: number } }
  | { readonly v: typeof PROTOCOL_VERSION; readonly type: 'relay_notice'; readonly payload: RelayNoticePayload };

export type ObserverFrameType = ObserverFrame['type'];

/**
 * Narrows an observer frame to a specific `type`.
 *
 * Without this, TypeScript cannot tell you that `frame.payload` is a
 * `TranscriptPayload` on a transcript frame, and every consumer ends up with an
 * untyped `any` cast.
 */
export function isObserverFrame<K extends ObserverFrameType>(
  frame: ObserverFrame,
  type: K,
): frame is Extract<ObserverFrame, { type: K }> {
  return frame.type === type;
}
