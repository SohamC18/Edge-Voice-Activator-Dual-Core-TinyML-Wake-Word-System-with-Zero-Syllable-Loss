/**
 * The status header: identity, liveness, and the source switch.
 *
 * The source switch is not a developer toy — it is what lets the demo be shown
 * without hardware while still being honest about what is on screen. The
 * "legacy serial" option routes through the host's legacy adapter so the
 * `synthetic` badge and the adapted-rendering path can be demonstrated to a
 * judge rather than merely described.
 */

import type { DashboardState, LinkState } from '../lib/state';
import type { SourceMode } from '../lib/useDashboard';
import { formatBytes, formatCount, formatMs } from '../lib/format';
import { Badge, LinkPill, Measured } from './primitives';

const SOURCES: ReadonlyArray<{ readonly value: SourceMode; readonly label: string; readonly title: string }> = [
  { value: 'live', label: 'ESP32 (live)', title: 'Connect to the host relay observer endpoint at /ws' },
  { value: 'mock', label: 'Mock device', title: 'Protocol-v1 simulator, for development and visual validation' },
  { value: 'legacy-mock', label: 'Legacy serial', title: 'Pre-v1 serial format adapted by the host; frames are marked synthetic' },
];

export function StatusHeader({
  state,
  link,
  linkDetail,
  source,
  onSourceChange,
  relayRttMs,
}: {
  readonly state: DashboardState;
  readonly link: LinkState;
  readonly linkDetail: string | null;
  readonly source: SourceMode;
  readonly onSourceChange: (mode: SourceMode) => void;
  readonly relayRttMs: number | null;
}) {
  const boot = state.boot;
  const deviceTone = state.deviceConnected ? 'good' : 'bad';

  return (
    <header className="topbar">
      <div className="topbar__identity">
        <h1 className="topbar__title">
          Edge Voice Activator
          <span className="topbar__subtitle">SIH 2026 · always-on keyword spotting with on-device ASR</span>
        </h1>
        <div className="topbar__pills">
          <LinkPill link={link} detail={linkDetail} />
          <Badge tone={deviceTone}>
            device {state.deviceConnected ? 'connected' : state.deviceReason ?? 'disconnected'}
          </Badge>
          {source !== 'live' ? (
            <Badge tone="warn" title="No hardware is attached to this view.">
              simulated source
            </Badge>
          ) : null}
          {state.sawSyntheticFrame ? (
            <Badge tone="warn" title="At least one frame was adapted from the pre-v1 serial format on the host.">
              contains synthetic frames
            </Badge>
          ) : null}
        </div>
      </div>

      <dl className="topbar__facts">
        <div>
          <dt>Firmware</dt>
          <dd>
            {boot ? <code className="mono">{boot.fw}</code> : <span className="measured measured--missing" title="no boot frame received">—</span>}
          </dd>
        </div>
        <div>
          <dt>Device</dt>
          <dd>{boot?.chip ?? '—'}</dd>
        </div>
        <div>
          <dt>Tensor arena</dt>
          <dd>
            <Measured value={formatBytes(boot?.arena_bytes)} />
          </dd>
        </div>
        <div>
          <dt>Free heap</dt>
          <dd>
            <Measured value={formatBytes(state.resources.freeHeap)} />
          </dd>
        </div>
        <div>
          <dt>Frames</dt>
          <dd>
            <Measured value={formatCount(state.hasTelemetry ? state.lastSequence : null)} />
          </dd>
        </div>
        <div>
          <dt>Relay RTT</dt>
          <dd>
            <Measured value={formatMs(relayRttMs)} />
          </dd>
        </div>
      </dl>

      <div className="topbar__source" role="group" aria-label="Telemetry source">
        {SOURCES.map((option) => (
          <button
            key={option.value}
            type="button"
            className={source === option.value ? 'source source--active' : 'source'}
            aria-pressed={source === option.value}
            title={option.title}
            onClick={() => onSourceChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </header>
  );
}
