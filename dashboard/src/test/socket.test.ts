/**
 * Socket tests.
 *
 * A dashboard that gives up on a dropped frame looks broken mid-demo, so the
 * reconnect and stale-detection behaviour is tested directly with injected
 * timers and a fake socket. No real network, no real waiting.
 */

import { describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';
import { TelemetrySocket } from '../lib/socket';
import { STALE_AFTER_MS } from '../types/protocol';

/** Minimal WebSocket stand-in; only the surface the client actually uses. */
class FakeSocket {
  static instances: FakeSocket[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;
  closed = false;
  readonly url: string;

  // Field assigned in the body rather than a `private readonly url` parameter
  // property: `erasableSyntaxOnly` forbids emit-only syntax.
  constructor(url: string) {
    this.url = url;
    FakeSocket.instances.push(this);
  }

  close(): void {
    this.closed = true;
  }

  /* test helpers */
  accept(): void {
    this.onopen?.();
  }
  deliver(data: unknown): void {
    this.onmessage?.({ data } as MessageEvent);
  }
  drop(reason = 'relay restarted', code = 1006): void {
    this.onclose?.({ reason, code } as CloseEvent);
  }
}

/** Controllable timer queue so backoff can be asserted without waiting. */
function fakeTimers() {
  let now = 0;
  let nextHandle = 1;
  const timeouts = new Map<number, { at: number; handler: () => void }>();
  const intervals = new Map<number, { every: number; next: number; handler: () => void }>();

  return {
    setTimeoutFn: (handler: () => void, ms: number) => {
      const handle = nextHandle++;
      timeouts.set(handle, { at: now + ms, handler });
      return handle;
    },
    clearTimeoutFn: (handle: number) => {
      timeouts.delete(handle);
    },
    setIntervalFn: (handler: () => void, ms: number) => {
      const handle = nextHandle++;
      intervals.set(handle, { every: ms, next: now + ms, handler });
      return handle;
    },
    clearIntervalFn: (handle: number) => {
      intervals.delete(handle);
    },
    /** Advance virtual time, firing anything due. */
    advance(ms: number) {
      now += ms;
      for (const [handle, entry] of [...timeouts]) {
        if (entry.at <= now) {
          timeouts.delete(handle);
          entry.handler();
        }
      }
      for (const entry of [...intervals.values()]) {
        while (entry.next <= now) {
          entry.next += entry.every;
          entry.handler();
        }
      }
    },
    get pendingTimeouts() {
      return timeouts.size;
    },
    get pendingIntervals() {
      return intervals.size;
    },
  };
}

type StateSpy = Mock<(state: string, detail: string | null) => void>;
type FrameSpy = Mock<(raw: string) => void>;

function setup(overrides: Partial<Parameters<typeof buildClient>[0]> = {}) {
  return buildClient(overrides);
}

function buildClient({
  onFrame = vi.fn(),
  onStateChange = vi.fn(),
  onParseError = vi.fn(),
  url = 'ws://host:8765/ws',
  timers = fakeTimers(),
}: {
  onFrame?: FrameSpy;
  onStateChange?: StateSpy;
  onParseError?: Mock<(message: string) => void>;
  url?: string;
  timers?: ReturnType<typeof fakeTimers>;
} = {}) {
  FakeSocket.instances = [];
  const client = new TelemetrySocket({
    url,
    onFrame,
    onStateChange,
    onParseError,
    socketFactory: (target) => new FakeSocket(target) as unknown as WebSocket,
    setTimeoutFn: timers.setTimeoutFn,
    clearTimeoutFn: timers.clearTimeoutFn,
    setIntervalFn: timers.setIntervalFn,
    clearIntervalFn: timers.clearIntervalFn,
  });
  return { client, timers, onFrame, onStateChange, onParseError };
}

describe('TelemetrySocket', () => {
  it('connects to the supplied observer URL', () => {
    const { client } = setup({ url: 'ws://192.168.137.1:8765/ws' });
    client.connect();
    expect(FakeSocket.instances[0]?.url).toBe('ws://192.168.137.1:8765/ws');
  });

  it('reports connecting then live on open', () => {
    const { client, onStateChange } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();

    const states = onStateChange.mock.calls.map((call) => call[0]);
    expect(states).toContain('connecting');
    expect(states).toContain('live');
  });

  it('forwards text frames to the reducer', () => {
    const { client, onFrame } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();
    FakeSocket.instances[0]?.deliver('{"v":1,"type":"ping","payload":{"t":1}}');

    expect(onFrame).toHaveBeenCalledWith('{"v":1,"type":"ping","payload":{"t":1}}');
  });

  it('counts binary Opus frames as liveness without forwarding them', () => {
    // Protocol rule 4: no audio is retained, so the dashboard must not try to
    // store or decode the uplink.
    const { client, onFrame, onStateChange, timers } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();
    FakeSocket.instances[0]?.deliver(new Uint8Array([1, 0, 0, 0]));

    expect(onFrame).not.toHaveBeenCalled();
    timers.advance(STALE_AFTER_MS);
    // Still live, because the binary frame proved the link is alive.
    expect(onStateChange.mock.calls.map((c) => c[0])).toContain('live');
  });

  it('marks the link stale after the deadline with no frames', () => {
    const { client, onStateChange, timers } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();
    onStateChange.mockClear();

    timers.advance(STALE_AFTER_MS);
    const states = onStateChange.mock.calls.map((call) => call[0]);
    expect(states).toContain('stale');
  });

  it('reconnects after an unexpected close', () => {
    const { client, timers } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();
    FakeSocket.instances[0]?.drop();

    expect(timers.pendingTimeouts).toBe(1);
    timers.advance(600);
    expect(FakeSocket.instances.length).toBe(2);
  });

  it('backs off exponentially across repeated failures', () => {
    const { client, timers } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();

    // Each close schedules a retry; the delay must grow so a dead relay is not
    // hammered during a demo.
    FakeSocket.instances[0]?.drop();
    timers.advance(500);
    expect(FakeSocket.instances.length).toBe(2);
    FakeSocket.instances[1]?.drop();
    timers.advance(999);
    expect(FakeSocket.instances.length).toBe(2); // first backoff not yet elapsed
    timers.advance(2);
    expect(FakeSocket.instances.length).toBe(3);
  });

  it('resets the backoff after a successful reconnect', () => {
    const { client, timers } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();
    FakeSocket.instances[0]?.drop();
    timers.advance(600);
    FakeSocket.instances[1]?.accept(); // success

    FakeSocket.instances[1]?.drop();
    // Backoff reset, so the first-retry delay applies again.
    timers.advance(500);
    expect(FakeSocket.instances.length).toBe(3);
  });

  it('stops retrying and reports closed after close()', () => {
    const { client, onStateChange, timers } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();
    client.close();

    expect(FakeSocket.instances[0]?.closed).toBe(true);
    expect(timers.pendingTimeouts).toBe(0);
    expect(onStateChange.mock.calls.map((c) => c[0])).toContain('closed');

    timers.advance(10_000);
    expect(FakeSocket.instances.length).toBe(1); // no reconnect after close()
  });

  it('clears the stale watchdog on close so it cannot fire after teardown', () => {
    const { client, timers, onStateChange } = setup();
    client.connect();
    FakeSocket.instances[0]?.accept();
    expect(timers.pendingIntervals).toBe(1);

    client.close();
    expect(timers.pendingIntervals).toBe(0);

    onStateChange.mockClear();
    timers.advance(STALE_AFTER_MS * 2);
    expect(onStateChange.mock.calls.map((c) => c[0])).not.toContain('stale');
  });

  it('reports an error and retries when the socket cannot be constructed', () => {
    const onStateChange = vi.fn();
    const timers = fakeTimers();
    FakeSocket.instances = [];
    const client = new TelemetrySocket({
      url: 'ws://host/ws',
      onFrame: vi.fn(),
      onStateChange,
      onParseError: vi.fn(),
      socketFactory: () => {
        throw new Error('blocked by policy');
      },
      setTimeoutFn: timers.setTimeoutFn,
      clearTimeoutFn: timers.clearTimeoutFn,
      setIntervalFn: timers.setIntervalFn,
      clearIntervalFn: timers.clearIntervalFn,
    });

    client.connect();
    const states = onStateChange.mock.calls.map((call) => call[0]);
    expect(states).toContain('error');
    expect(onStateChange.mock.calls.some((call) => call[1] === 'blocked by policy')).toBe(true);
    expect(timers.pendingTimeouts).toBe(1);
  });
});
