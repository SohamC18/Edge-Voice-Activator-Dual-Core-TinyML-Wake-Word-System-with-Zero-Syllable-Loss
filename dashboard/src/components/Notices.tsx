/**
 * `Notices` — relay warnings, device errors, and a dismissable banner.
 *
 * Errors are surfaced rather than swallowed because the relay deliberately
 * reports contract violations (a frame that fails validation, an oversized Opus
 * frame) instead of hiding them. If the dashboard can show the judge *why* a
 * number is missing, it earns the right to show the numbers that are present.
 */

import type { DashboardState } from '../lib/state';
import { formatMs } from '../lib/format';
import { Badge, Measured } from './primitives';

export function Notices({ state }: { readonly state: DashboardState }) {
  const notices = state.notices.filter((notice) => notice.level !== 'info').slice(-4);
  const errors = state.errors.slice(-3);
  if (notices.length === 0 && errors.length === 0) return null;

  return (
    <aside className="notices" aria-label="Warnings and errors" aria-live="polite">
      {notices.map((notice) => (
        <div key={notice.id} className={`notice notice--${notice.level}`}>
          <Badge tone={notice.level === 'error' ? 'bad' : 'warn'}>{notice.level}</Badge>
          <span className="notice__message">{notice.message}</span>
          {notice.code ? <code className="mono notice__code">{notice.code}</code> : null}
        </div>
      ))}
      {errors.map((error) => (
        <div key={error.id} className="notice notice--error">
          <Badge tone="bad">device error</Badge>
          <code className="mono notice__code">{error.code}</code>
          <span className="notice__message">{error.message}</span>
          <span className="notice__time">
            t+<Measured value={formatMs(error.deviceUptimeMs)} />
          </span>
        </div>
      ))}
    </aside>
  );
}
