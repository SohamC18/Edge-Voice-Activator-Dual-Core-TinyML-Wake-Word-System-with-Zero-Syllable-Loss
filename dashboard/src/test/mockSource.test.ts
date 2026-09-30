/**
 * Mock-source tests.
 *
 * The mock is only trustworthy if it produces frames the real relay would
 * accept, so these assert the protocol invariants (version, envelope, softmax
 * summing to 1, monotonic sequence) rather than visual details.
 */

import { describe, expect, it, vi } from 'vitest';
import { MockDevice, LEGACY_CLASS_ORDER, V1_CLASS_ORDER } from '../lib/mockSource';
import type { ObserverFrame } from '../types/protocol';
import { PROTOCOL_VERSION } from '../types/protocol';

function collect(options: Parameters<typeof MockDevice.prototype.start>[0] extends never ? never : ConstructorParameters<typeof MockDevice>[0] = {}, ticks = 8) {
  const frames: ObserverFrame[] = [];
  const device = new MockDevice(options);
  // Drive the tick deterministically instead of waiting on a real 600 ms timer.
  const internals = device as unknown as { step: (emit: (frame: ObserverFrame) => void) => void };
  const emit = (frame: ObserverFrame) => frames.push(frame);
  (device as unknown as { emitBoot: (emit: (frame: ObserverFrame) => void) => void }).emitBoot(emit);
  for (let index = 0; index < ticks; index += 1) {
    internals.step(emit);
  }
  return frames;
}

describe('MockDevice', () => {
  it('emits a boot frame declaring the class order', () => {
    const frames = collect({ classOrder: LEGACY_CLASS_ORDER });
    const status = frames.find((frame) => frame.type === 'device_status');
    expect(status).toBeDefined();
    if (status?.type !== 'device_status') throw new Error('expected device_status');
    expect(status.payload.boot?.class_order).toEqual(LEGACY_CLASS_ORDER);
  });

  it('emits only protocol-v1 frames', () => {
    for (const frame of collect()) {
      expect(frame.v).toBe(PROTOCOL_VERSION);
    }
  });

  it('produces probability vectors that sum to 1', () => {
    // The host relay rejects an ai_result whose vector does not sum to 1, so a
    // mock that drifted would make the dashboard look broken for the wrong
    // reason.
    for (const frame of collect({ classOrder: V1_CLASS_ORDER })) {
      if (frame.type !== 'telemetry' || frame.payload.ev !== 'ai_result') continue;
      const sum = frame.payload.p.reduce((acc, value) => acc + value, 0);
      expect(sum).toBeCloseTo(1, 2);
    }
  });

  it('keeps the sequence strictly increasing', () => {
    const seqs = collect()
      .filter((frame) => frame.type === 'telemetry')
      .map((frame) => frame.payload.seq);
    for (let index = 1; index < seqs.length; index += 1) {
      expect(seqs[index]).toBeGreaterThan(seqs[index - 1]);
    }
  });

  it('advances device uptime monotonically', () => {
    const times = collect()
      .filter((frame) => frame.type === 'telemetry')
      .map((frame) => frame.payload.t);
    for (let index = 1; index < times.length; index += 1) {
      expect(times[index]).toBeGreaterThanOrEqual(times[index - 1]);
    }
  });

  it('emits sys and ram frames roughly once per second', () => {
    const frames = collect({}, 10); // 10 * 600ms = 6s
    const sys = frames.filter((frame) => frame.type === 'telemetry' && frame.payload.ev === 'sys');
    const ram = frames.filter((frame) => frame.type === 'telemetry' && frame.payload.ev === 'ram');
    expect(sys.length).toBeGreaterThan(0);
    expect(ram.length).toBeGreaterThan(0);
  });

  it('omits cpu_idle_pct when the build cannot measure it', () => {
    const frames = collect({ reportCpu: false });
    const sys = frames.find((frame) => frame.type === 'telemetry' && frame.payload.ev === 'sys');
    if (sys?.type !== 'telemetry') throw new Error('expected a sys frame');
    expect('cpu_idle_pct' in sys.payload).toBe(false);
  });

  it('includes cpu_idle_pct when the build measures it', () => {
    const frames = collect({ reportCpu: true });
    const sys = frames.find((frame) => frame.type === 'telemetry' && frame.payload.ev === 'sys');
    if (sys?.type !== 'telemetry') throw new Error('expected a sys frame');
    expect(typeof (sys.payload as { cpu_idle_pct?: number }).cpu_idle_pct).toBe('number');
  });

  it('marks legacy frames synthetic and warns the observer', () => {
    const frames = collect({ mode: 'legacy', classOrder: LEGACY_CLASS_ORDER, acceptRate: 1 });
    const telemetryFrames = frames.filter((frame) => frame.type === 'telemetry');
    const synthetic = telemetryFrames.filter((frame) => (frame.payload as { synthetic?: boolean }).synthetic === true);
    expect(synthetic.length).toBeGreaterThan(0);

    const notice = frames.find((frame) => frame.type === 'relay_notice');
    if (notice?.type !== 'relay_notice') throw new Error('expected a relay_notice');
    expect(notice.payload.code).toBe('LEGACY_SOURCE');
  });

  it('withholds transcripts in legacy mode because that path has no real ASR', () => {
    // The legacy serial format carries no audio, so a transcript there would be
    // a fabrication. The UI must show no transcript rather than a fake one.
    const frames = collect({ mode: 'legacy', classOrder: LEGACY_CLASS_ORDER, acceptRate: 1 });
    expect(frames.filter((frame) => frame.type === 'transcript')).toHaveLength(0);
  });

  it('emits a transcript with pre-roll evidence when a keyword is accepted', () => {
    const frames = collect({ acceptRate: 1, seed: 7 }, 40);
    const transcript = frames.find((frame) => frame.type === 'transcript');
    if (transcript?.type !== 'transcript') throw new Error('expected a transcript');
    expect(transcript.payload.pre_roll_complete).toBe(true);
    expect(transcript.payload.preroll_ms).toBe(300);
    expect(transcript.payload.first_word_ms).toBeGreaterThan(0);
  });

  it('pairs every transcript with a latency sample', () => {
    const frames = collect({ acceptRate: 1, seed: 11 }, 40);
    const transcripts = frames.filter((frame) => frame.type === 'transcript');
    const latency = frames.filter((frame) => frame.type === 'latency');
    expect(transcripts.length).toBeGreaterThan(0);
    expect(latency.length).toBe(transcripts.length);
  });

  it('never reports a negative or zero-length utterance id', () => {
    for (const frame of collect({ acceptRate: 1, seed: 3 }, 30)) {
      if (frame.type === 'transcript') expect(frame.payload.utterance_id).toBeGreaterThan(0);
    }
  });

  it('stops cleanly', () => {
    const clearInterval = vi.spyOn(globalThis, 'clearInterval');
    const device = new MockDevice();
    device.start(() => {});
    device.stop();
    expect(clearInterval).toHaveBeenCalled();
    clearInterval.mockRestore();
  });
});
