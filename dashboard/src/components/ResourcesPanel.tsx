/**
 * Panel 3 — Resources.
 *
 * The claim this panel has to defend is "it fits in 256 KiB of RAM". A single
 * number cannot show that, so the budget is rendered as a stacked bar of the
 * named static pools, with the residual free heap shown as an explicit
 * `other` segment rather than being quietly folded into the total.
 *
 * CPU and latency follow the same honesty rule: `cpu_idle_pct` requires FreeRTOS
 * run-time statistics, and if the firmware build did not enable them the bar is
 * replaced by an "unavailable" note. A plausible-looking CPU bar that the device
 * never measured is worse than no bar.
 */

import { useMemo } from 'react';
import type { DashboardState, LatencySample, RamSegment } from '../lib/state';
import { RAM_BUDGET_BYTES } from '../types/protocol';
import { formatBytes, formatCount, formatMs, formatPercent, formatText } from '../lib/format';
import { Badge, EmptyState, Measured, Panel, Stat } from './primitives';

const SEGMENT_COLORS: Record<string, string> = {
  tensor_arena: 'var(--seg-arena)',
  mel_db: 'var(--seg-mel)',
  pcm_ring: 'var(--seg-pcm)',
  preroll_ring: 'var(--seg-preroll)',
  opus_encoder: 'var(--seg-opus)',
  rtos_stacks: 'var(--seg-rtos)',
  other: 'var(--seg-other)',
};

function percent(part: number, whole: number): number {
  if (whole <= 0) return 0;
  return (part / whole) * 100;
}

/** Stacked bar of static RAM against the 256 KiB budget. */
function RamLedger({ segments, total, budget }: { readonly segments: readonly RamSegment[]; readonly total?: number; readonly budget?: number }) {
  const effectiveBudget = budget ?? RAM_BUDGET_BYTES;
  const used = total ?? 0;
  const overBudget = used > effectiveBudget;

  if (segments.length === 0) {
    return <EmptyState message="No memory ledger received. The device sends a `ram` frame once per second." />;
  }

  return (
    <div className="ram">
      <div className="ram__bar-row">
        <div
          className="ram__bar"
          role="img"
          aria-label={
            `Static RAM ${used} bytes of ${effectiveBudget} budget across ` +
            segments.map((segment) => `${segment.label} ${segment.bytes}`).join(', ')
          }
        >
          {segments.map((segment) => (
            <div
              key={segment.key}
              className="ram__segment"
              data-segment={segment.key}
              style={{ width: `${percent(segment.bytes, effectiveBudget)}%`, background: SEGMENT_COLORS[segment.key] ?? 'var(--seg-other)' }}
              title={`${segment.label}: ${formatBytes(segment.bytes).text}`}
            />
          ))}
        </div>
        <div className="ram__totals">
          <span className={overBudget ? 'ram__total ram__total--over' : 'ram__total'}>
            <Measured value={formatBytes(used)} /> of <Measured value={formatBytes(effectiveBudget)} />
          </span>
          <span className="ram__pct">
            <Measured value={formatPercent(percent(used, effectiveBudget))} /> used
          </span>
        </div>
      </div>
      {overBudget ? (
        <p className="ram__warning" role="alert">
          <Badge tone="bad">over budget</Badge> Static allocations exceed the declared {formatBytes(effectiveBudget).text} budget
          by {formatBytes(used - effectiveBudget).text}.
        </p>
      ) : null}
      <ul className="ram__legend">
        {segments.map((segment) => (
          <li key={segment.key} className="ram__legend-item">
            <span className="ram__swatch" style={{ background: SEGMENT_COLORS[segment.key] ?? 'var(--seg-other)' }} aria-hidden="true" />
            <span className="ram__legend-label">{segment.label}</span>
            <span className="ram__legend-value">{formatBytes(segment.bytes).text}</span>
            <span className="ram__legend-pct">{percent(segment.bytes, effectiveBudget).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** CPU idle bar, or an explicit "unavailable" when the build cannot measure it. */
function CpuMeter({ idlePct }: { readonly idlePct?: number }) {
  if (idlePct == null) {
    return (
      <p className="meter meter--unavailable" title="cpu_idle_pct is omitted unless the firmware enables configGENERATE_RUN_TIME_STATS.">
        <Badge tone="neutral">unavailable</Badge> FreeRTOS run-time statistics are not enabled in this build, so CPU
        utilisation is not reported.
      </p>
    );
  }
  const busy = 100 - idlePct;
  const tone = busy > 50 ? 'bad' : busy > 25 ? 'warn' : 'good';
  return (
    <div className="meter">
      <div className="meter__bar" role="img" aria-label={`CPU ${busy.toFixed(1)} percent busy, ${idlePct.toFixed(1)} percent idle`}>
        <div className={`meter__fill meter__fill--${tone}`} style={{ width: `${busy}%` }} />
      </div>
      <div className="meter__readout">
        <Stat label="Busy" value={<Measured value={formatPercent(busy)} />} tone={tone} />
        <Stat label="Idle" value={<Measured value={formatPercent(idlePct)} />} />
      </div>
    </div>
  );
}

/** Keyword-to-first-byte latency, with a running mean. */
function LatencyTable({ samples }: { readonly samples: readonly LatencySample[] }) {
  const stats = useMemo(() => {
    if (samples.length === 0) return null;
    const values = samples.map((sample) => sample.keywordToFirstByteMs);
    const mean = values.reduce((acc, value) => acc + value, 0) / values.length;
    return { mean, min: Math.min(...values), max: Math.max(...values) };
  }, [samples]);

  if (!stats) {
    return <EmptyState message="No latency samples yet. A sample is recorded per recognised utterance." />;
  }

  return (
    <div className="latency">
      <div className="latency__stats">
        <Stat label="Mean" value={<Measured value={formatMs(stats.mean)} />} />
        <Stat label="Best" value={<Measured value={formatMs(stats.min)} />} tone="good" />
        <Stat label="Worst" value={<Measured value={formatMs(stats.max)} />} />
      </div>
      <ul className="latency__rows">
        {samples
          .slice(-6)
          .reverse()
          .map((sample) => (
            <li key={sample.utteranceId} className="latency__row">
              <span className="latency__id">#{sample.utteranceId}</span>
              <span className="latency__bar-track" aria-hidden="true">
                <span
                  className="latency__bar-fill"
                  // Scale against the worst observed sample so the bar is
                  // comparable within the session without inventing a target.
                  style={{ width: `${Math.min(100, (sample.keywordToFirstByteMs / Math.max(1, stats.max)) * 100)}%` }}
                />
              </span>
              <span className="latency__value">
                <Measured value={formatMs(sample.keywordToFirstByteMs)} />
              </span>
              {sample.asrMs != null ? <span className="latency__asr">asr {formatMs(sample.asrMs).text}</span> : null}
            </li>
          ))}
      </ul>
    </div>
  );
}

export function ResourcesPanel({ state }: { readonly state: DashboardState }) {
  const { resources } = state;
  const heapPressure =
    resources.minFreeHeap != null && resources.largestFreeBlock != null
      ? (resources.largestFreeBlock / resources.minFreeHeap) * 100
      : null;

  return (
    <Panel
      id="resources"
      title="Hardware Resources"
      subtitle="Measured memory, CPU, and end-to-end latency. Anything the firmware does not report stays blank."
    >
      <section className="resources__section" aria-labelledby="ram-heading">
        <h3 id="ram-heading" className="resources__section-title">
          RAM ledger <span className="resources__budget">budget {formatBytes(resources.budgetBytes ?? RAM_BUDGET_BYTES).text}</span>
        </h3>
        <RamLedger segments={resources.ramSegments} total={resources.ramTotalBytes} budget={resources.budgetBytes} />
      </section>

      <section className="resources__section" aria-labelledby="cpu-heading">
        <h3 id="cpu-heading" className="resources__section-title">
          CPU
        </h3>
        <CpuMeter idlePct={resources.cpuIdlePct} />
      </section>

      <section className="resources__section" aria-labelledby="heap-heading">
        <h3 id="heap-heading" className="resources__section-title">
          Heap &amp; link
        </h3>
        <div className="resources__grid">
          <Stat label="Free heap" value={<Measured value={formatBytes(resources.freeHeap)} />} />
          <Stat label="Minimum free heap" value={<Measured value={formatBytes(resources.minFreeHeap)} />} />
          <Stat label="Largest block" value={<Measured value={formatBytes(resources.largestFreeBlock)} />} />
          <Stat
            label="Fragmentation"
            value={
              <Measured
                value={formatText(heapPressure == null ? null : `${(100 - heapPressure).toFixed(1)}%`)}
              />
            }
            tone={heapPressure != null && heapPressure < 50 ? 'warn' : 'neutral'}
            hint="100% minus (largest free block / minimum free heap). High values mean the heap is fragmenting."
          />
          <Stat label="Uptime" value={<Measured value={formatMs(resources.uptimeMs)} />} />
          <Stat label="Wi-Fi RSSI" value={<Measured value={formatText(resources.rssi == null ? null : `${resources.rssi} dBm`)} />} />
          <Stat
            label="Audio chunks dropped"
            value={<Measured value={formatCount(resources.audioDroppedChunks)} />}
            tone={resources.audioDroppedChunks != null && resources.audioDroppedChunks > 0 ? 'bad' : 'neutral'}
          />
          <Stat
            label="Serial overruns"
            value={<Measured value={formatCount(resources.serialOverruns)} />}
            tone={resources.serialOverruns != null && resources.serialOverruns > 0 ? 'bad' : 'neutral'}
          />
        </div>
      </section>

      <section className="resources__section" aria-labelledby="latency-heading">
        <h3 id="latency-heading" className="resources__section-title">
          Keyword-to-first-byte latency
        </h3>
        <LatencyTable samples={state.latency} />
      </section>
    </Panel>
  );
}
