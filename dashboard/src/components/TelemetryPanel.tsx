/**
 * Panel 1 — Live Telemetry & Transcript.
 *
 * The job of this panel is to answer one question visually: *is the model
 * actually hearing the word, and is the system capturing audio from before the
 * word started?*
 *
 * Three coordinated views:
 *  - a confidence trace per named class (not per index — see protocol rule 2),
 *  - the current transcript with the first word highlighted,
 *  - the RMS energy trace against the gate threshold.
 */

import { useMemo } from 'react';
import type { ConfidenceSample, DashboardState, GateSample } from '../lib/state';
import { PIPELINE_STAGES } from '../types/protocol';
import type { TranscriptPayload } from '../types/protocol';
import { formatCount, formatMs, formatProbability, formatText, niceMax, splitTranscript } from '../lib/format';
import { Badge, EmptyState, Measured, Panel, Stat, UNAVAILABLE } from './primitives';

const SERIES_COLORS: Record<string, string> = {
  friday: 'var(--accent-friday)',
  silence: 'var(--accent-silence)',
  unknown: 'var(--accent-unknown)',
  negative: 'var(--accent-unknown)',
};

function seriesColor(name: string): string {
  return SERIES_COLORS[name] ?? 'var(--accent-generic)';
}

function stageIndex(stage: string): number {
  const index = PIPELINE_STAGES.indexOf(stage as (typeof PIPELINE_STAGES)[number]);
  return index >= 0 ? index : -1;
}

/** The horizontal pipeline indicator. */
function PipelineBadge({ stage, detail, synthetic }: { readonly stage: string; readonly detail: string | null; readonly synthetic: boolean }) {
  const activeIndex = stageIndex(stage);
  return (
    <div className="pipeline">
      <ol className="pipeline__steps">
        {PIPELINE_STAGES.map((step, index) => {
          const state = index < activeIndex ? 'done' : index === activeIndex ? 'active' : 'pending';
          return (
            <li key={step} className={`pipeline__step pipeline__step--${state}`}>
              <span className="pipeline__marker" aria-hidden="true" />
              <span className="pipeline__label">{step.replace(/_/g, ' ')}</span>
            </li>
          );
        })}
      </ol>
      <div className="pipeline__meta">
        {detail ? <span className="pipeline__detail">{detail}</span> : null}
        {synthetic ? (
          <Badge tone="warn" title="Frame was adapted from the pre-v1 serial format on the host. Values are parsed, not device-reported.">
            synthetic source
          </Badge>
        ) : null}
      </div>
    </div>
  );
}

/** Multi-series confidence trace, one polyline per class, newest at the right. */
function ConfidenceTrace({ samples, classOrder }: { readonly samples: readonly ConfidenceSample[]; readonly classOrder: readonly string[] }) {
  const geometry = useMemo(() => {
    if (samples.length < 2) return null;
    const width = 100;
    const height = 100;
    const step = width / (samples.length - 1);
    return classOrder.map((name) => ({
      name,
      points: samples
        .map((sample, index) => {
          const value = sample.byName[name];
          if (value == null || !Number.isFinite(value)) return null;
          const x = index * step;
          const y = height - value * height;
          return `${x.toFixed(2)},${y.toFixed(2)}`;
        })
        .filter((point): point is string => point !== null)
        .join(' '),
    }));
  }, [samples, classOrder]);

  const latest = samples.at(-1);

  return (
    <div className="trace">
      <svg className="trace__svg" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Keyword confidence over time">
        <line className="trace__grid" x1="0" y1="0" x2="100" y2="0" />
        <line className="trace__grid" x1="0" y1="50" x2="100" y2="50" />
        <line className="trace__grid" x1="0" y1="100" x2="100" y2="100" />
        {/* 50% decision threshold */}
        <line className="trace__threshold" x1="0" y1="50" x2="100" y2="50" />
        {geometry?.map((series) => (
          <polyline
            key={series.name}
            className="trace__line"
            points={series.points}
            style={{ stroke: seriesColor(series.name) }}
            data-series={series.name}
          />
        ))}
      </svg>
      <ul className="trace__legend">
        {classOrder.map((name) => (
          <li key={name} className="trace__legend-item">
            <span className="trace__swatch" style={{ background: seriesColor(name) }} aria-hidden="true" />
            <span className="trace__legend-name">{name}</span>
            <Measured value={formatProbability(latest?.byName[name])} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** RMS energy against the gate threshold. */
function EnergyTrace({ samples }: { readonly samples: readonly GateSample[] }) {
  const { path, thresholdY } = useMemo(() => {
    if (samples.length < 2) return { path: null, thresholdY: null };
    const threshold = samples[samples.length - 1]?.threshold ?? 0;
    const maxValue = niceMax([...samples.map((s) => s.rms), threshold]);
    const step = 100 / (samples.length - 1);
    const d = samples
      .map((sample, index) => {
        const x = index * step;
        const y = 100 - (sample.rms / maxValue) * 100;
        return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
      })
      .join(' ');
    return { path: d, thresholdY: 100 - (threshold / maxValue) * 100 };
  }, [samples]);

  const latest = samples.at(-1);

  return (
    <div className="energy">
      <svg className="energy__svg" viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Microphone RMS against gate threshold">
        {path ? <path className="energy__area" d={`${path} L100,100 L0,100 Z`} /> : null}
        {path ? <path className="energy__line" d={path} /> : null}
        {thresholdY != null ? <line className="energy__threshold" x1="0" y1={thresholdY} x2="100" y2={thresholdY} /> : null}
      </svg>
      <div className="energy__readout">
        <Stat
          label="RMS"
          value={
            <Measured
              value={formatText(
                latest && Number.isFinite(latest.rms) ? latest.rms.toFixed(0) : null,
                'no energy window received yet',
              )}
            />
          }
        />
        <Stat
          label="Threshold"
          value={latest ? latest.threshold.toFixed(0) : UNAVAILABLE}
        />
        <Stat
          label="Gate"
          value={
            latest ? (
              <Badge tone={latest.open ? 'good' : 'neutral'}>{latest.open ? 'open' : 'closed'}</Badge>
            ) : (
              UNAVAILABLE
            )
          }
        />
      </div>
    </div>
  );
}

function TranscriptCard({ transcript }: { readonly transcript: TranscriptPayload }) {
  const parts = splitTranscript(transcript.text, transcript.first_word);
  return (
    <article className="transcript">
      <header className="transcript__header">
        <span className="transcript__id">utterance #{transcript.utterance_id}</span>
        {transcript.final ? <Badge tone="info">final</Badge> : <Badge tone="warn">partial</Badge>}
        {transcript.pre_roll_complete ? (
          <Badge tone="good" title="Pre-roll audio preceded the keyword, so the first word was not clipped.">
            pre-roll complete
          </Badge>
        ) : (
          <Badge tone="warn" title="No pre-roll frames were captured for this utterance; the first word may be clipped.">
            pre-roll missing
          </Badge>
        )}
      </header>
      <p className="transcript__text">
        {parts.firstWord ? <mark className="transcript__first-word">{parts.firstWord}</mark> : null}
        {parts.rest}
      </p>
      <dl className="transcript__meta">
        <div>
          <dt>First word at</dt>
          <dd>
            <Measured value={formatMs(transcript.first_word_ms)} />
          </dd>
        </div>
        <div>
          <dt>Pre-roll</dt>
          <dd>
            <Measured value={formatMs(transcript.preroll_ms)} />
          </dd>
        </div>
        <div>
          <dt>ASR time</dt>
          <dd>
            <Measured value={formatMs(transcript.asr_ms)} />
          </dd>
        </div>
        <div>
          <dt>Confidence</dt>
          <dd>
            <Measured value={formatProbability(transcript.confidence)} />
          </dd>
        </div>
      </dl>
      {transcript.first_word_ms != null && transcript.preroll_ms != null ? (
        <p className="transcript__proof">
          The word begins <strong>{Math.round(transcript.first_word_ms)} ms</strong> into a{' '}
          <strong>{Math.round(transcript.preroll_ms)} ms</strong> pre-roll window, so it was captured before the
          keyword fired.
        </p>
      ) : null}
    </article>
  );
}

export function TelemetryPanel({ state }: { readonly state: DashboardState }) {
  const latestTranscript = state.transcripts.at(-1);
  const latestConfidence = state.confidence.at(-1);
  const dropped = latestConfidence?.dropped;
  const inferenceMs = latestConfidence?.inferenceMs;

  // The keyword series is looked up by name via the device-declared class
  // order, so this keeps working whichever ordering the model actually uses.
  const keywordIndex = state.classOrder.findIndex((name) => name === 'friday' || name === 'negative');
  const keywordProb = keywordIndex >= 0 ? latestConfidence?.byName[state.classOrder[keywordIndex]] : undefined;

  return (
    <Panel
      id="telemetry"
      title="Live Telemetry & Transcript"
      subtitle="Named-class confidence, microphone energy, and what the recogniser actually heard."
      actions={
        <span className="panel__stamp">
          frame sequence <Measured value={formatCount(state.hasTelemetry ? state.lastSequence : null)} />
        </span>
      }
    >
      <PipelineBadge stage={state.stage} detail={state.stageDetail} synthetic={state.sawSyntheticFrame} />

      <div className="telemetry__stats">
        <Stat
          label="Keyword confidence"
          value={<Measured value={formatProbability(keywordProb)} />}
          tone="good"
        />
        <Stat label="Inference time" value={<Measured value={formatMs(inferenceMs)} />} />
        <Stat
          label="Dropped windows"
          value={<Measured value={formatCount(dropped)} />}
          tone={dropped != null && dropped > 0 ? 'bad' : 'neutral'}
          hint="Analysis windows discarded because the AI core was still busy. Non-zero means Stage-1 is under-reporting."
        />
        <Stat
          label="Class order"
          value={<code className="mono">{state.classOrder.join(' · ')}</code>}
          hint="Declared by the device in its boot frame, not assumed by the dashboard."
        />
      </div>

      <section className="telemetry__section" aria-labelledby="confidence-heading">
        <h3 id="confidence-heading" className="telemetry__section-title">
          Class confidence
        </h3>
        {state.confidence.length >= 2 ? (
          <ConfidenceTrace samples={state.confidence} classOrder={state.classOrder} />
        ) : (
          <EmptyState message="Waiting for at least two inference results." />
        )}
      </section>

      <section className="telemetry__section" aria-labelledby="energy-heading">
        <h3 id="energy-heading" className="telemetry__section-title">
          Microphone energy vs gate
        </h3>
        {state.gate.length >= 2 ? (
          <EnergyTrace samples={state.gate} />
        ) : (
          <EmptyState message="Waiting for energy windows." />
        )}
      </section>

      <section className="telemetry__section" aria-labelledby="transcript-heading">
        <h3 id="transcript-heading" className="telemetry__section-title">
          Latest transcript
        </h3>
        {latestTranscript ? (
          <TranscriptCard transcript={latestTranscript} />
        ) : (
          <EmptyState message="No utterance recognised yet. Say the keyword to produce one." />
        )}
      </section>
    </Panel>
  );
}
