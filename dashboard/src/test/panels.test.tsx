/**
 * Component tests.
 *
 * The point of these is to prove the honesty rule survives all the way to the
 * DOM. A reducer can preserve `undefined` correctly and the UI can still print
 * `0`; only a render assertion catches that.
 */

import { describe, expect, it, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { TelemetryPanel } from '../components/TelemetryPanel';
import { ActivationPanel } from '../components/ActivationPanel';
import { ResourcesPanel } from '../components/ResourcesPanel';
import { StatusHeader } from '../components/StatusHeader';
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
  transcript,
  transcriptFrame,
} from './frames';

function build(frames: ReturnType<typeof asTelemetry>[]): DashboardState {
  return frames.reduce(dashboardReducer, INITIAL_STATE);
}

describe('TelemetryPanel', () => {
  beforeEach(() => resetSeq());

  it('prompts for data instead of showing placeholder confidence', () => {
    render(<TelemetryPanel state={INITIAL_STATE} />);
    expect(screen.getByText(/waiting for at least two inference results/i)).toBeInTheDocument();
    expect(screen.getByText(/no utterance recognised yet/i)).toBeInTheDocument();
  });

  it('renders an em dash, not zero, for an unmeasured inference time', () => {
    const state = build([asTelemetry(bootFrame()), asTelemetry(aiResultFrame([0.1, 0.2, 0.7], { inference_ms: undefined }))]);
    render(<TelemetryPanel state={state} />);

    const inference = screen.getByText('Inference time').closest('.stat');
    expect(inference).not.toBeNull();
    // "0.0 ms" would mean the device reported a zero-duration inference.
    expect(within(inference as HTMLElement).getByText('—')).toBeInTheDocument();
    expect(within(inference as HTMLElement).queryByText(/ms/)).toBeNull();
  });

  it('renders the measured inference time when it is present', () => {
    const state = build([asTelemetry(bootFrame()), asTelemetry(aiResultFrame([0.1, 0.2, 0.7], { inference_ms: 41 }))]);
    render(<TelemetryPanel state={state} />);
    const inference = screen.getByText('Inference time').closest('.stat') as HTMLElement;
    expect(within(inference).getByText('41 ms')).toBeInTheDocument();
  });

  it('labels series by the device-declared class order', () => {
    const state = build([
      asTelemetry(bootFrame({ class_order: ['friday', 'negative', 'silence'] })),
      asTelemetry(aiResultFrame([0.87, 0.09, 0.04], { t: 1000 })),
      asTelemetry(aiResultFrame([0.91, 0.06, 0.03], { t: 1600 })),
    ]);
    render(<TelemetryPanel state={state} />);
    const legend = screen.getByText('friday').closest('.trace__legend-item') as HTMLElement;
    // Index 0 is friday under the declared order, so the latest sample's 0.91
    // must appear against `friday` rather than against `silence`.
    expect(within(legend).getByText('91.0%')).toBeInTheDocument();
  });

  it('highlights the first word and states the pre-roll proof', () => {
    const state = build([asTelemetry(bootFrame()), transcriptFrame()]);
    render(<TelemetryPanel state={state} />);

    const mark = screen.getByText('play');
    expect(mark.tagName).toBe('MARK');
    expect(mark).toHaveClass('transcript__first-word');
    expect(screen.getByText(/was captured before the keyword fired/i)).toBeInTheDocument();
    expect(screen.getByText('pre-roll complete')).toBeInTheDocument();
  });

  it('warns when pre-roll is missing rather than implying the word was clean', () => {
    const state = build([
      asTelemetry(bootFrame()),
      transcriptFrame(transcript({ pre_roll_complete: false, preroll_ms: 0, first_word_ms: undefined })),
    ]);
    render(<TelemetryPanel state={state} />);
    expect(screen.getByText('pre-roll missing')).toBeInTheDocument();
    // With no first-word timing there is no proof to state, so no claim is made.
    expect(screen.queryByText(/was captured before the keyword fired/i)).toBeNull();
  });

  it('survives a malformed transcript payload instead of blanking the dashboard', () => {
    const state = build([asTelemetry(bootFrame()), transcriptFrame({ utterance_id: 1, final: true } as never)]);
    render(<TelemetryPanel state={state} />);
    expect(screen.getByText('Latest transcript')).toBeInTheDocument();
  });

  it('surfaces a non-zero dropped-window counter as a problem', () => {
    const state = build([asTelemetry(bootFrame()), asTelemetry(aiResultFrame([0.1, 0.2, 0.7], { dropped: 7 }))]);
    render(<TelemetryPanel state={state} />);
    const stat = screen.getByText('Dropped windows').closest('.stat') as HTMLElement;
    expect(stat).toHaveClass('stat--bad');
    expect(within(stat).getByText('7')).toBeInTheDocument();
  });

  it('badges the pipeline as synthetic when frames were host-adapted', () => {
    const state = build([asTelemetry({ ...gateFrame(), synthetic: true } as never)]);
    render(<TelemetryPanel state={state} />);
    expect(screen.getByText('synthetic source')).toBeInTheDocument();
  });

  it('marks the active pipeline stage', () => {
    const state = build([asTelemetry(stageFrame('gate_open'))]);
    render(<TelemetryPanel state={state} />);
    const active = document.querySelector('.pipeline__step--active');
    expect(active?.textContent).toContain('gate open');
  });
});

describe('ActivationPanel', () => {
  beforeEach(() => resetSeq());

  it('shows an empty state before any decisions', () => {
    render(<ActivationPanel state={INITIAL_STATE} />);
    expect(screen.getByText(/no decisions yet/i)).toBeInTheDocument();
  });

  it('lists accepted decisions with a wall-clock timestamp', () => {
    const state = build([asTelemetry(decisionFrame({ accept: true, reason: 'friday>=thr' }))]);
    render(<ActivationPanel state={state} />);
    expect(screen.getByText('activated')).toBeInTheDocument();
    // The reason token appears in both the tally and the row, so scope to the row.
    const row = screen.getByText('activated').closest('tr') as HTMLElement;
    expect(within(row).getByText('friday>=thr')).toBeInTheDocument();
    // A judge must be able to correlate a row with the demo recording.
    const time = within(row).getByText(/\d{2}:\d{2}:\d{2}\.\d{3}/);
    expect(time).toBeInTheDocument();
  });

  it('keeps rejected decisions so a false-positive rate can be shown', () => {
    const state = build([
      asTelemetry(decisionFrame({ accept: true })),
      asTelemetry(decisionFrame({ accept: false, reason: 'friday<thr' })),
    ]);
    render(<ActivationPanel state={state} />);
    expect(screen.getByText('rejected')).toBeInTheDocument();
    expect(screen.getByText('Accepted')).toBeInTheDocument();
    expect(within(screen.getByText('Accepted').closest('.stat') as HTMLElement).getByText('1')).toBeInTheDocument();
  });

  it('groups decisions by reason token', () => {
    const state = build([
      asTelemetry(decisionFrame({ accept: false, reason: 'gate_closed' })),
      asTelemetry(decisionFrame({ accept: false, reason: 'gate_closed' })),
      asTelemetry(decisionFrame({ accept: true, reason: 'friday>=thr' })),
    ]);
    render(<ActivationPanel state={state} />);
    const tally = screen.getByLabelText('Decision reasons');
    const chip = within(tally).getByText('gate_closed').closest('.activation__reason-chip') as HTMLElement;
    expect(within(chip).getByText('2')).toBeInTheDocument();
  });

  it('shows debounce detail for a throttled rejection', () => {
    const state = build([asTelemetry(decisionFrame({ accept: false, reason: 'debounce', since_last_ms: 900, debounce_ms: 2000 }))]);
    render(<ActivationPanel state={state} />);
    expect(screen.getByText(/900 ms since last \(debounce 2000 ms\)/)).toBeInTheDocument();
  });

  it('marks host-adapted rows as synthetic instead of device-reported', () => {
    const state = build([asTelemetry({ ...decisionFrame(), synthetic: true } as never)]);
    render(<ActivationPanel state={state} />);
    expect(screen.getByText('synthetic')).toBeInTheDocument();
  });

  it('offers a real packet-capture command for proof', () => {
    render(<ActivationPanel state={INITIAL_STATE} />);
    const capture = screen.getByText('Wireshark / tshark');
    expect(capture).toHaveAttribute('title', expect.stringContaining('tshark'));
  });
});

describe('ResourcesPanel', () => {
  beforeEach(() => resetSeq());

  it('states that CPU is unavailable rather than drawing a bar', () => {
    const state = build([asTelemetry(sysFrame({ cpu_idle_pct: undefined }))]);
    render(<ResourcesPanel state={state} />);
    expect(screen.getByText('unavailable')).toBeInTheDocument();
    expect(screen.queryByRole('img', { name: /CPU .* percent busy/i })).toBeNull();
  });

  it('draws a CPU bar when the device actually measures it', () => {
    const state = build([asTelemetry(sysFrame({ cpu_idle_pct: 92.4 }))]);
    render(<ResourcesPanel state={state} />);
    expect(screen.getByRole('img', { name: /7\.6 percent busy, 92\.4 percent idle/i })).toBeInTheDocument();
  });

  it('renders the RAM ledger as one segment per reported pool', () => {
    const state = build([asTelemetry(ramFrame())]);
    render(<ResourcesPanel state={state} />);
    const bar = screen.getByRole('img', { name: /static RAM .* budget/i });
    expect(bar).toBeInTheDocument();
    expect(document.querySelectorAll('.ram__segment')).toHaveLength(7);
  });

  it('does not invent a segment for a pool the device did not report', () => {
    const state = build([asTelemetry(ramFrame({ segments: { tensor_arena: 30_720 } }))]);
    render(<ResourcesPanel state={state} />);
    expect(document.querySelectorAll('.ram__segment')).toHaveLength(1);
  });

  it('warns when static allocations exceed the declared budget', () => {
    const state = build([asTelemetry(ramFrame({ segments: { tensor_arena: 300_000 }, budget_bytes: 262_144 }))]);
    render(<ResourcesPanel state={state} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/exceed the declared/i);
  });

  it('prompts for a memory ledger instead of drawing an empty bar', () => {
    render(<ResourcesPanel state={INITIAL_STATE} />);
    expect(screen.getByText(/no memory ledger received/i)).toBeInTheDocument();
  });

  it('shows mean, best, and worst latency only once samples exist', () => {
    const state = build([latencyFrame(1, 500), latencyFrame(2, 900), latencyFrame(3, 700)]);
    render(<ResourcesPanel state={state} />);
    const mean = screen.getByText('Mean').closest('.stat') as HTMLElement;
    expect(within(mean).getByText('700 ms')).toBeInTheDocument();
    expect(screen.getByText('Best').closest('.stat')).toHaveTextContent('500 ms');
    expect(screen.getByText('Worst').closest('.stat')).toHaveTextContent('900 ms');
  });

  it('prompts for latency samples rather than showing a fake zero', () => {
    render(<ResourcesPanel state={INITIAL_STATE} />);
    expect(screen.getByText(/no latency samples yet/i)).toBeInTheDocument();
  });

  it('renders an em dash for an unreported RSSI', () => {
    const state = build([asTelemetry(sysFrame({ rssi: undefined }))]);
    render(<ResourcesPanel state={state} />);
    const rssi = screen.getByText('Wi-Fi RSSI').closest('.stat') as HTMLElement;
    expect(within(rssi).getByText('—')).toBeInTheDocument();
  });
});

describe('StatusHeader', () => {
  beforeEach(() => resetSeq());

  const noop = () => {};

  it('shows placeholders before a boot frame arrives', () => {
    render(
      <StatusHeader
        state={INITIAL_STATE}
        link="idle"
        linkDetail={null}
        source="mock"
        onSourceChange={noop}
        relayRttMs={null}
      />,
    );
    expect(screen.getByText('Idle')).toBeInTheDocument();
    expect(screen.getAllByText('—').length).toBeGreaterThan(0);
  });

  it('shows firmware identity once the device has booted', () => {
    const state = build([asTelemetry(bootFrame())]);
    render(
      <StatusHeader state={state} link="live" linkDetail={null} source="live" onSourceChange={noop} relayRttMs={12.4} />,
    );
    expect(screen.getByText('edge-kws/1.0.0')).toBeInTheDocument();
    expect(screen.getByText('ESP32-D0WDQ6')).toBeInTheDocument();
    expect(screen.getByText('30.0 KiB')).toBeInTheDocument();
  });

  it('marks a simulated source so a judge is never misled', () => {
    render(
      <StatusHeader state={INITIAL_STATE} link="live" linkDetail={null} source="mock" onSourceChange={noop} relayRttMs={null} />,
    );
    expect(screen.getByText('simulated source')).toBeInTheDocument();
  });

  it('does not claim a simulated source when connected to hardware', () => {
    render(
      <StatusHeader state={build([asTelemetry(bootFrame())])} link="live" linkDetail={null} source="live" onSourceChange={noop} relayRttMs={null} />,
    );
    expect(screen.queryByText('simulated source')).toBeNull();
  });

  it('reports a disconnected device with its reason', () => {
    const state = dashboardReducer(INITIAL_STATE, status(false, 'timeout'));
    render(<StatusHeader state={state} link="stale" linkDetail="timeout" source="live" onSourceChange={noop} relayRttMs={null} />);
    expect(screen.getByText(/device timeout/i)).toBeInTheDocument();
  });

  it('exposes the source switch as pressed buttons for assistive tech', () => {
    render(
      <StatusHeader state={INITIAL_STATE} link="idle" linkDetail={null} source="mock" onSourceChange={noop} relayRttMs={null} />,
    );
    expect(screen.getByRole('button', { name: 'Mock device' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: 'ESP32 (live)' })).toHaveAttribute('aria-pressed', 'false');
  });
});
