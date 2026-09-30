/**
 * Reducer tests.
 *
 * These focus on the invariants that keep the dashboard honest, because those
 * are the ones that fail silently and are hardest to catch visually:
 *
 *  - a missing measurement stays missing,
 *  - class indices are mapped through the device-declared order,
 *  - cumulative counters are not reset to zero by a later frame,
 *  - history windows are bounded so a long demo cannot exhaust browser memory.
 */

import { describe, expect, it, beforeEach } from 'vitest';
import { INITIAL_STATE, dashboardReducer } from '../lib/state';
import type { DashboardState } from '../lib/state';
import {
  aiResultFrame,
  asTelemetry,
  bootFrame,
  decisionFrame,
  gateFrame,
  latencyFrame,
  ramFrame,
  resetSeq,
  stageFrame,
  status,
  sysFrame,
  transcriptFrame,
} from './frames';

function applyAll(state: DashboardState, frames: ReturnType<typeof asTelemetry>[]): DashboardState {
  return frames.reduce(dashboardReducer, state);
}

describe('dashboardReducer', () => {
  beforeEach(() => resetSeq());

  it('starts with no telemetry so a zero cannot be mistaken for a measurement', () => {
    expect(INITIAL_STATE.hasTelemetry).toBe(false);
    expect(INITIAL_STATE.resources.cpuIdlePct).toBeUndefined();
    expect(INITIAL_STATE.resources.ramSegments).toEqual([]);
  });

  it('adopts the class order declared by the device boot frame', () => {
    const state = dashboardReducer(INITIAL_STATE, asTelemetry(bootFrame({ class_order: ['friday', 'negative', 'silence'] })));
    expect(state.classOrder).toEqual(['friday', 'negative', 'silence']);
  });

  it('maps probability indices to names using the declared class order', () => {
    let state = dashboardReducer(INITIAL_STATE, asTelemetry(bootFrame({ class_order: ['friday', 'negative', 'silence'] })));
    state = dashboardReducer(state, asTelemetry(aiResultFrame([0.87, 0.09, 0.04])));

    const sample = state.confidence.at(-1);
    // Index 0 is friday under the documented order, so 0.87 must land on
    // `friday` -- not on `silence` as the pre-patch firmware assumed.
    expect(sample?.byName.friday).toBeCloseTo(0.87);
    expect(sample?.byName.negative).toBeCloseTo(0.09);
    expect(sample?.byName.silence).toBeCloseTo(0.04);
  });

  it('keeps cpu_idle_pct undefined when the frame omits it', () => {
    const state = dashboardReducer(INITIAL_STATE, asTelemetry(sysFrame({ cpu_idle_pct: undefined })));
    expect(state.resources.cpuIdlePct).toBeUndefined();
    expect('cpuIdlePct' in state.resources).toBe(true);
  });

  it('does not reset a previously measured value to undefined on a later frame', () => {
    let state = dashboardReducer(INITIAL_STATE, asTelemetry(sysFrame({ cpu_idle_pct: 88.1, rssi: -55 })));
    state = dashboardReducer(state, asTelemetry(sysFrame({ cpu_idle_pct: undefined, rssi: undefined })));

    // A partial frame must not erase what we already know; it means "unchanged".
    expect(state.resources.cpuIdlePct).toBe(88.1);
    expect(state.resources.rssi).toBe(-55);
  });

  it('omits absent RAM segments rather than rendering zero-width bars', () => {
    const state = dashboardReducer(INITIAL_STATE, asTelemetry(ramFrame({ segments: { tensor_arena: 30_720, mel_db: 16_160 } })));
    expect(state.resources.ramSegments.map((segment) => segment.key)).toEqual(['tensor_arena', 'mel_db']);
    expect(state.resources.ramTotalBytes).toBe(46_880);
  });

  it('records decisions newest-last and preserves the rejection reason', () => {
    const state = applyAll(INITIAL_STATE, [
      asTelemetry(decisionFrame({ accept: true, reason: 'friday>=thr' })),
      asTelemetry(decisionFrame({ accept: false, reason: 'debounce', since_last_ms: 900, debounce_ms: 2000 })),
    ]);
    expect(state.activations).toHaveLength(2);
    expect(state.activations.at(-1)?.accepted).toBe(false);
    expect(state.activations.at(-1)?.reason).toBe('debounce');
    expect(state.activations.at(-1)?.sinceLastMs).toBe(900);
  });

  it('flags synthetic frames so parsed serial output is never presented as measured', () => {
    const frame = { ...decisionFrame(), synthetic: true } as never;
    const state = dashboardReducer(INITIAL_STATE, asTelemetry(frame));
    expect(state.sawSyntheticFrame).toBe(true);
    expect(state.activations.at(-0)?.synthetic).toBe(true);
  });

  it('keeps a sticky synthetic flag once any synthetic frame is seen', () => {
    let state = dashboardReducer(INITIAL_STATE, asTelemetry({ ...gateFrame(), synthetic: true } as never));
    state = dashboardReducer(state, asTelemetry(gateFrame()));
    expect(state.sawSyntheticFrame).toBe(true);
  });

  it('adopts the replayed boot frame from device_status so a late joiner can label classes', () => {
    const boot = bootFrame({ class_order: ['friday', 'negative', 'silence'] });
    const state = dashboardReducer(INITIAL_STATE, status(true, null, boot));
    expect(state.classOrder).toEqual(['friday', 'negative', 'silence']);
    expect(state.deviceConnected).toBe(true);
  });

  it('surfaces a device disconnect without discarding accumulated history', () => {
    let state = dashboardReducer(INITIAL_STATE, asTelemetry(aiResultFrame([0.9, 0.05, 0.05])));
    const before = state.confidence.length;
    state = dashboardReducer(state, status(false, 'timeout'));
    expect(state.deviceConnected).toBe(false);
    expect(state.confidence).toHaveLength(before);
  });

  it('upserts latency rather than duplicating a re-sent sample', () => {
    let state = dashboardReducer(INITIAL_STATE, latencyFrame(7, 612));
    state = dashboardReducer(state, latencyFrame(7, 640));
    expect(state.latency).toHaveLength(1);
    expect(state.latency[0]?.keywordToFirstByteMs).toBe(640);
  });

  it('bounds the confidence window so a long demo cannot exhaust browser memory', () => {
    const many = Array.from({ length: 500 }, (_, index) =>
      asTelemetry(aiResultFrame([0.1, 0.2, 0.7], { t: 1000 + index * 100 })),
    );
    const state = applyAll(INITIAL_STATE, many);
    expect(state.confidence.length).toBeLessThanOrEqual(180);
    // The newest sample must survive trimming, not the oldest.
    expect(state.confidence.at(-1)?.t).toBe(1000 + 499 * 100);
  });

  it('keeps the highest sequence seen even if frames arrive out of order', () => {
    let state = dashboardReducer(INITIAL_STATE, asTelemetry(gateFrame({ seq: 500 })));
    state = dashboardReducer(state, asTelemetry(gateFrame({ seq: 100 })));
    expect(state.lastSequence).toBe(500);
  });

  it('records transcripts in arrival order', () => {
    let state = dashboardReducer(INITIAL_STATE, transcriptFrame());
    state = dashboardReducer(state, asTelemetry(stageFrame('streaming')));
    expect(state.transcripts).toHaveLength(1);
    expect(state.transcripts[0]?.text).toBe('play some jazz');
  });

  it('ignores relay pings without polluting state', () => {
    const before = INITIAL_STATE;
    const state = dashboardReducer(before, { v: 1, type: 'ping', payload: { t: 1 } });
    expect(state).toBe(before);
  });
});
