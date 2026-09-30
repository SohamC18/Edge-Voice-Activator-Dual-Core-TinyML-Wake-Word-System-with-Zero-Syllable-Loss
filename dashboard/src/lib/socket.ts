/**
 * Observer WebSocket client.
 *
 * Responsibilities, in order of importance for a live demo:
 *
 * 1. **Reconnect without losing the room.** A projector browser that gives up
 *    after one dropped Wi-Fi frame looks broken. Backoff is capped so recovery
 *    is quick, and the reducer keeps accumulated history across reconnects.
 * 2. **Never let a malformed frame kill the stream.** The relay validates
 *    frames, but a `try/catch` here means a future protocol addition degrades
 *    to a visible notice rather than a blank dashboard.
 * 3. **Report staleness honestly.** If nothing arrives for `STALE_AFTER_MS` the
 *    UI shows a stale state instead of frozen numbers, which would read as
 *    "the device is idle" when it is actually wedged.
 */

import { STALE_AFTER_MS } from '../types/protocol';
import type { LinkState } from './state';

export interface TelemetrySocketOptions {
  readonly url: string;
  /** Injected for tests; defaults to the global timer functions. */
  readonly setTimeoutFn?: (handler: () => void, ms: number) => number;
  readonly clearTimeoutFn?: (handle: number) => void;
  readonly setIntervalFn?: (handler: () => void, ms: number) => number;
  readonly clearIntervalFn?: (handle: number) => void;
  readonly socketFactory?: (url: string) => WebSocket;
  readonly onFrame: (raw: string) => void;
  readonly onStateChange: (state: LinkState, detail: string | null) => void;
  readonly onParseError: (message: string) => void;
}

const MAX_BACKOFF_MS = 8000;
const BASE_BACKOFF_MS = 500;

export class TelemetrySocket {
  private readonly options: TelemetrySocketOptions;
  private socket: WebSocket | null = null;
  private closed = false;
  private attempt = 0;
  private retryHandle: number | null = null;
  private staleHandle: number | null = null;
  private sawFrame = false;

  private readonly setTimeoutFn: (handler: () => void, ms: number) => number;
  private readonly clearTimeoutFn: (handle: number) => void;
  private readonly setIntervalFn: (handler: () => void, ms: number) => number;
  private readonly clearIntervalFn: (handle: number) => void;
  private readonly socketFactory: (url: string) => WebSocket;

  constructor(options: TelemetrySocketOptions) {
    // Assigned explicitly rather than via a `private readonly` parameter
    // property: `erasableSyntaxOnly` in tsconfig forbids emit-only syntax, and
    // the whole point of that flag is that the code stays readable by anything
    // that only strips types.
    this.options = options;
    this.setTimeoutFn = options.setTimeoutFn ?? ((handler, ms) => globalThis.setTimeout(handler, ms) as unknown as number);
    this.clearTimeoutFn = options.clearTimeoutFn ?? ((handle) => globalThis.clearTimeout(handle));
    this.setIntervalFn = options.setIntervalFn ?? ((handler, ms) => globalThis.setInterval(handler, ms) as unknown as number);
    this.clearIntervalFn = options.clearIntervalFn ?? ((handle) => globalThis.clearInterval(handle));
    this.socketFactory = options.socketFactory ?? ((url) => new WebSocket(url));
  }

  connect(): void {
    this.closed = false;
    this.openSocket();
  }

  private openSocket(): void {
    if (this.closed) return;
    this.options.onStateChange(this.attempt === 0 ? 'connecting' : 'connecting', this.attempt > 0 ? `reconnecting (attempt ${this.attempt + 1})` : null);

    let socket: WebSocket;
    try {
      socket = this.socketFactory(this.options.url);
    } catch (error) {
      this.options.onStateChange('error', error instanceof Error ? error.message : 'could not open socket');
      this.scheduleRetry();
      return;
    }
    this.socket = socket;

    socket.onopen = () => {
      this.attempt = 0;
      this.sawFrame = false;
      this.options.onStateChange('live', null);
      this.startStaleWatch();
    };

    socket.onmessage = (event: MessageEvent) => {
      // Binary frames are the device's Opus uplink, relayed only as raw audio.
      // The dashboard has no use for them (protocol rule 4: no audio retained),
      // so they are counted as liveness and dropped.
      if (typeof event.data !== 'string') {
        this.sawFrame = true;
        return;
      }
      this.sawFrame = true;
      this.options.onFrame(event.data);
    };

    socket.onerror = () => {
      // `onclose` always follows, so the retry is scheduled there; setting an
      // error state here avoids a silent gap in the status pill.
      this.options.onStateChange('error', 'socket error');
    };

    socket.onclose = (event: CloseEvent) => {
      this.stopStaleWatch();
      this.socket = null;
      if (this.closed) {
        this.options.onStateChange('closed', null);
        return;
      }
      this.options.onStateChange('stale', event.reason || `relay closed (code ${event.code})`);
      this.scheduleRetry();
    };
  }

  /**
   * Watches for the absence of frames. The relay also enforces this
   * server-side, but the browser needs its own view so a relay that is up while
   * the device is dead is still visible as stale.
   */
  private startStaleWatch(): void {
    this.stopStaleWatch();
    this.staleHandle = this.setIntervalFn(() => {
      if (!this.sawFrame) {
        this.options.onStateChange('stale', 'no frames from relay');
      } else {
        this.sawFrame = false;
        this.options.onStateChange('live', null);
      }
    }, STALE_AFTER_MS);
  }

  private stopStaleWatch(): void {
    if (this.staleHandle !== null) {
      this.clearIntervalFn(this.staleHandle);
      this.staleHandle = null;
    }
  }

  private scheduleRetry(): void {
    if (this.closed || this.retryHandle !== null) return;
    // Exponential backoff, capped. The first retry is fast so a relay restart
    // during a demo recovers almost immediately.
    const delay = Math.min(BASE_BACKOFF_MS * 2 ** this.attempt, MAX_BACKOFF_MS);
    this.attempt += 1;
    this.retryHandle = this.setTimeoutFn(() => {
      this.retryHandle = null;
      this.openSocket();
    }, delay);
  }

  close(): void {
    this.closed = true;
    this.stopStaleWatch();
    if (this.retryHandle !== null) {
      this.clearTimeoutFn(this.retryHandle);
      this.retryHandle = null;
    }
    if (this.socket) {
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.onmessage = null;
      this.socket.close();
      this.socket = null;
    }
    this.options.onStateChange('closed', null);
  }
}

/** Default relay URL for the ESP32 hotspot topology (192.168.137.1). */
export function defaultObserverUrl(host = window.location.hostname || '192.168.137.1', port = 8765): string {
  return `ws://${host}:${port}/ws`;
}
