/**
 * Formatter tests.
 *
 * The central assertion throughout: an absent value formats as the em dash
 * with a reason, never as `0` or an empty string. If this regresses, every
 * unmeasured metric in the UI silently becomes a fabricated zero.
 */

import { describe, expect, it } from 'vitest';
import {
  UNAVAILABLE,
  formatBytes,
  formatCount,
  formatDuration,
  formatMs,
  formatPercent,
  formatProbability,
  formatRssi,
  formatText,
  formatWallClock,
  niceMax,
  splitTranscript,
} from '../lib/format';

describe('absent-value handling', () => {
  it.each([
    ['formatPercent', formatPercent],
    ['formatProbability', formatProbability],
    ['formatCount', formatCount],
    ['formatMs', formatMs],
    ['formatBytes', formatBytes],
    ['formatRssi', formatRssi],
    ['formatDuration', formatDuration],
    ['formatWallClock', formatWallClock],
  ] as const)('%s returns the em dash plus a reason for null', (_name, fn) => {
    const result = fn(null);
    expect(result.text).toBe(UNAVAILABLE);
    expect(result.missingReason).toBeTruthy();
  });

  it('treats undefined the same as null', () => {
    expect(formatMs(undefined).text).toBe(UNAVAILABLE);
    expect(formatBytes(undefined).missingReason).toBeTruthy();
  });

  it('rejects NaN rather than printing NaN on screen', () => {
    expect(formatPercent(Number.NaN).text).toBe(UNAVAILABLE);
    expect(formatMs(Number.NaN).missingReason).toBeTruthy();
  });

  it('marks zero as a real measurement, not a missing one', () => {
    // The distinction that matters: a reported 0 is data; an absent value is not.
    const zero = formatCount(0);
    expect(zero.text).toBe('0');
    expect(zero.missingReason).toBeUndefined();
  });
});

describe('formatText', () => {
  it('passes through a present value', () => {
    expect(formatText('512').text).toBe('512');
    expect(formatText('512').missingReason).toBeUndefined();
  });

  it('marks null as missing', () => {
    expect(formatText(null).text).toBe(UNAVAILABLE);
  });

  it('marks a non-finite number as missing', () => {
    expect(formatText(Number.POSITIVE_INFINITY).text).toBe(UNAVAILABLE);
  });
});

describe('formatBytes', () => {
  it('uses bytes below 1 KiB', () => {
    expect(formatBytes(512).text).toBe('512 B');
  });

  it('uses KiB above 1 KiB', () => {
    expect(formatBytes(30_720).text).toBe('30.0 KiB');
  });

  it('uses MiB above 1 MiB', () => {
    expect(formatBytes(3 * 1024 * 1024).text).toBe('3.00 MiB');
  });
});

describe('formatMs', () => {
  it('keeps sub-10 ms values to one decimal', () => {
    expect(formatMs(4.25).text).toBe('4.3 ms');
  });

  it('switches to seconds past 1000 ms', () => {
    expect(formatMs(1_500).text).toBe('1.50 s');
  });
});

describe('formatWallClock', () => {
  it('formats an epoch stamp as time of day with milliseconds', () => {
    const result = formatWallClock(Date.UTC(2026, 0, 1, 14, 32, 7, 480));
    expect(result.text).toMatch(/^\d{2}:\d{2}:\d{2}\.\d{3}$/);
  });

  it('reports an invalid stamp as unavailable instead of Invalid Date', () => {
    expect(formatWallClock(Number.NaN).text).toBe(UNAVAILABLE);
  });
});

describe('splitTranscript', () => {
  it('highlights the reported first word', () => {
    const parts = splitTranscript('play some jazz', 'play');
    expect(parts.firstWord).toBe('play');
    expect(parts.rest).toBe(' some jazz');
  });

  it('matches the first word case-insensitively', () => {
    const parts = splitTranscript('Play some jazz', 'play');
    expect(parts.firstWord).toBe('Play');
    expect(parts.rest).toBe(' some jazz');
  });

  it('falls back to the first token when no first word is reported', () => {
    const parts = splitTranscript('play some jazz');
    expect(parts.firstWord).toBe('play');
    expect(parts.rest).toBe(' some jazz');
  });

  it('handles a single-word transcript', () => {
    const parts = splitTranscript('stop', 'stop');
    expect(parts.firstWord).toBe('stop');
    expect(parts.rest).toBe('');
  });

  it('returns empty parts for empty text', () => {
    expect(splitTranscript('   ')).toEqual({ prefix: '', firstWord: '', rest: '' });
  });

  it('falls back to the token split when the reported word is not in the text', () => {
    // Guards against highlighting nothing while still implying a highlight.
    const parts = splitTranscript('play some jazz', 'banana');
    expect(parts.firstWord).toBe('play');
  });
});

describe('niceMax', () => {
  it('never returns zero for a flat series, so charts cannot divide by zero', () => {
    expect(niceMax([0, 0, 0])).toBeGreaterThan(0);
  });

  it('leaves headroom above the largest sample', () => {
    expect(niceMax([10, 20, 30])).toBeGreaterThan(30);
  });

  it('ignores non-finite samples', () => {
    expect(niceMax([10, Number.NaN, Number.POSITIVE_INFINITY])).toBeGreaterThan(10);
  });
});
