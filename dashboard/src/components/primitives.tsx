/**
 * Small shared presentational primitives.
 *
 * `Measured` is the important one. Every number in this dashboard that comes
 * from the device goes through it, so "the firmware did not report this" always
 * renders identically — as an em dash with an explanatory tooltip — instead of
 * being coerced to 0 somewhere upstream.
 */

import type { ReactNode } from 'react';
import { UNAVAILABLE } from '../lib/format';
import type { Formatted } from '../lib/format';
import type { LinkState } from '../lib/state';

export function Panel({
  title,
  subtitle,
  actions,
  children,
  id,
}: {
  readonly title: string;
  readonly subtitle?: string;
  readonly actions?: ReactNode;
  readonly children: ReactNode;
  readonly id?: string;
}) {
  return (
    <section className="panel" aria-labelledby={id ? `${id}-heading` : undefined} id={id}>
      <header className="panel__header">
        <div>
          <h2 className="panel__title" id={id ? `${id}-heading` : undefined}>
            {title}
          </h2>
          {subtitle ? <p className="panel__subtitle">{subtitle}</p> : null}
        </div>
        {actions ? <div className="panel__actions">{actions}</div> : null}
      </header>
      <div className="panel__body">{children}</div>
    </section>
  );
}

/** Renders a measurement, or an explicit em dash when it is absent. */
export function Measured({
  value,
  className,
  suffix,
}: {
  readonly value: Formatted;
  readonly className?: string;
  readonly suffix?: string;
}) {
  if (value.missingReason) {
    return (
      <span className="measured measured--missing" title={value.missingReason} aria-label="unavailable">
        {value.text}
      </span>
    );
  }
  return (
    <span className={className ? `measured ${className}` : 'measured'}>
      {value.text}
      {suffix ? <span className="measured__suffix">{suffix}</span> : null}
    </span>
  );
}

export function Stat({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  readonly label: string;
  readonly value: ReactNode;
  readonly hint?: string;
  readonly tone?: 'neutral' | 'good' | 'warn' | 'bad';
}) {
  return (
    <div className={`stat stat--${tone}`} title={hint}>
      <span className="stat__label">{label}</span>
      <span className="stat__value">{value}</span>
    </div>
  );
}

const LINK_LABEL: Record<LinkState, string> = {
  idle: 'Idle',
  connecting: 'Connecting',
  live: 'Live',
  stale: 'Stale',
  closed: 'Disconnected',
  error: 'Error',
};

export function LinkPill({ link, detail }: { readonly link: LinkState; readonly detail: string | null }) {
  const tone =
    link === 'live' ? 'good' : link === 'connecting' ? 'warn' : link === 'idle' ? 'neutral' : 'bad';
  return (
    <span className={`pill pill--${tone}`} role="status" aria-live="polite">
      <span className="pill__dot" aria-hidden="true" />
      {LINK_LABEL[link]}
      {detail ? <span className="pill__detail"> · {detail}</span> : null}
    </span>
  );
}

export function Badge({
  children,
  tone = 'neutral',
  title,
}: {
  readonly children: ReactNode;
  readonly tone?: 'neutral' | 'good' | 'warn' | 'bad' | 'info';
  readonly title?: string;
}) {
  return (
    <span className={`badge badge--${tone}`} title={title}>
      {children}
    </span>
  );
}

export function EmptyState({ message }: { readonly message: string }) {
  return (
    <p className="empty" role="status">
      {message}
    </p>
  );
}

export { UNAVAILABLE };
