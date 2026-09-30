/**
 * Panel 2 — Activation Log.
 *
 * This is the audit trail a judge reads when asking "prove it only fired when
 * you said the word". Every row is one device `decision` frame, with the
 * AI scores, the gate state at the time, and a wall-clock timestamp that lines
 * up with the demo recording.
 *
 * Two deliberate choices:
 *
 *  - **Rejections are kept, not filtered.** A log of only successes cannot
 *    demonstrate a false-positive rate. Rejections are grouped by their stable
 *    machine token so the rejection breakdown is visible at a glance.
 *  - **Rows are read-only.** There is no optimistic update and no client-side
 *    filtering of the underlying data; the table is exactly what the device
 *    sent, in sequence order.
 */

import { useMemo } from 'react';
import type { ActivationRecord, DashboardState } from '../lib/state';
import { formatCount, formatMs, formatProbability, formatText, formatWallClock } from '../lib/format';
import { Badge, EmptyState, Measured, Panel, Stat } from './primitives';

interface ReasonTally {
  readonly reason: string;
  readonly count: number;
}

function tallyReasons(records: readonly ActivationRecord[]): ReasonTally[] {
  const counts = new Map<string, number>();
  for (const record of records) {
    counts.set(record.reason, (counts.get(record.reason) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([reason, count]) => ({ reason, count }))
    .sort((a, b) => b.count - a.count);
}

function VerdictBadge({ record }: { readonly record: ActivationRecord }) {
  return record.accepted ? (
    <Badge tone="good" title={`Accepted: ${record.reason}`}>
      activated
    </Badge>
  ) : (
    <Badge tone="neutral" title={`Rejected: ${record.reason}`}>
      rejected
    </Badge>
  );
}

function ActivationRow({ record }: { readonly record: ActivationRecord }) {
  const throttled = record.reason === 'debounce';
  return (
    <tr className={record.accepted ? 'activation__row activation__row--accepted' : 'activation__row'}>
      <td className="activation__time">
        <Measured value={formatWallClock(record.wallMs)} />
        <span className="activation__uptime">
          t+<Measured value={formatMs(record.deviceUptimeMs)} />
        </span>
      </td>
      <td className="activation__verdict">
        <VerdictBadge record={record} />
      </td>
      <td className="activation__reason">
        <code className="mono">{record.reason}</code>
        {throttled && record.sinceLastMs != null ? (
          <span className="activation__since">
            {Math.round(record.sinceLastMs)} ms since last (debounce {record.debounceMs ?? '—'} ms)
          </span>
        ) : null}
      </td>
      <td className="activation__scores">
        <Measured value={formatProbability(record.keywordProb)} />
        <span className="activation__threshold">vs thr {record.threshold?.toFixed(2) ?? '—'}</span>
      </td>
      <td className="activation__source">
        {record.synthetic ? (
          <Badge tone="warn" title="Parsed from the pre-v1 serial format on the host rather than reported by the device.">
            synthetic
          </Badge>
        ) : (
          <span className="activation__direct" title="Reported directly by the device in a protocol v1 frame.">
            device
          </span>
        )}
      </td>
    </tr>
  );
}

export function ActivationPanel({ state }: { readonly state: DashboardState }) {
  // Newest first: the interesting row is the one that just happened.
  const records = useMemo(() => [...state.activations].reverse(), [state.activations]);
  const accepted = useMemo(() => records.filter((record) => record.accepted), [records]);
  const reasons = useMemo(() => tallyReasons(records), [records]);
  const total = records.length;
  const acceptRate = total > 0 ? (accepted.length / total) * 100 : null;

  return (
    <Panel
      id="activations"
      title="Activation Log"
      subtitle="Every accept/reject verdict the device issued, with the scores that produced it."
      actions={
        <span className="panel__stamp">
          <Measured value={formatCount(total)} /> decisions in buffer
        </span>
      }
    >
      <div className="activation__summary">
        <Stat label="Accepted" value={<Measured value={formatCount(accepted.length)} />} tone="good" />
        <Stat label="Rejected" value={<Measured value={formatCount(total - accepted.length)} />} />
        <Stat
          label="Accept rate"
          value={<Measured value={formatText(acceptRate == null ? null : `${acceptRate.toFixed(1)}%`)} />}
          hint="Accepted decisions divided by all decisions in the current buffer."
        />
        <Stat
          label="Packet capture"
          value={
            <span className="activation__wireshark" title="Capture the uplink with: tshark -i wlan0 -f 'tcp port 8765' -w demo.pcapng">
              Wireshark / tshark
            </span>
          }
          hint="The Opus uplink is real traffic on port 8765, so it can be captured as proof."
        />
      </div>

      {reasons.length > 0 ? (
        <ul className="activation__reasons" aria-label="Decision reasons">
          {reasons.map((entry) => (
            <li key={entry.reason} className="activation__reason-chip">
              <code className="mono">{entry.reason}</code>
              <span className="activation__reason-count">{entry.count}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {records.length === 0 ? (
        <EmptyState message="No decisions yet. The device reports one per analysis window once the gate opens." />
      ) : (
        <div className="activation__scroll">
          <table className="activation__table">
            <caption className="visually-hidden">
              Activation decisions, newest first, with wall-clock time, verdict, reason, keyword confidence, and source.
            </caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Verdict</th>
                <th scope="col">Reason</th>
                <th scope="col">Keyword score</th>
                <th scope="col">Source</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <ActivationRow key={record.id} record={record} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
