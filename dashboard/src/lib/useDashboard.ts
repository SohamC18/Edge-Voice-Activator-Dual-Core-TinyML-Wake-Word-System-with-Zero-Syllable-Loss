/**
 * `useDashboard` — binds a telemetry transport to the reducer.
 *
 * The two transports (live WebSocket, mock device) are interchangeable behind
 * this hook, which is why the panels have no idea whether they are looking at
 * real hardware. In a demo the judge can be shown the mock on the laptop while
 * the projector shows the ESP32, with identical rendering.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { ObserverFrame } from '../types/protocol';
import { isObserverFrame } from '../types/protocol';
import { INITIAL_STATE, dashboardReducer } from './state';
import type { DashboardState, LinkState } from './state';
import { TelemetrySocket, defaultObserverUrl } from './socket';
import { MockDevice, LEGACY_CLASS_ORDER, V1_CLASS_ORDER } from './mockSource';
import type { MockOptions } from './mockSource';

export type SourceMode = 'live' | 'mock' | 'legacy-mock';

export interface UseDashboardOptions {
  readonly mode?: SourceMode;
  readonly url?: string;
  readonly mock?: MockOptions;
}

export interface UseDashboardResult {
  readonly state: DashboardState;
  readonly source: SourceMode;
  readonly setSource: (mode: SourceMode) => void;
  /** Round-trip time to the relay, measured from the application-level ping. */
  readonly relayRttMs: number | null;
}

export function useDashboard(options: UseDashboardOptions = {}): UseDashboardResult {
  const { mode = 'mock', url = defaultObserverUrl(), mock } = options;
  const [state, dispatch] = useReducer(dashboardReducer, INITIAL_STATE);
  const [source, setSource] = useState<SourceMode>(mode);
  const [relayRttMs, setRelayRttMs] = useState<number | null>(null);
  const pendingPings = useRef(new Map<number, number>());

  const onFrame = useCallback((raw: string) => {
    let parsed: ObserverFrame;
    try {
      parsed = JSON.parse(raw) as ObserverFrame;
    } catch {
      return; // a malformed frame must not tear down the stream
    }
    if (isObserverFrame(parsed, 'ping')) {
      const sent = parsed.payload.t;
      const started = pendingPings.current.get(sent);
      if (started !== undefined) {
        pendingPings.current.delete(sent);
        setRelayRttMs(performance.now() - started);
      }
      return;
    }
    dispatch(parsed);
  }, []);

  const onStateChange = useCallback((link: LinkState, detail: string | null) => {
    dispatch({ v: 1, type: 'device_status', payload: { connected: link === 'live', reason: detail } });
  }, []);

  const onParseError = useCallback((message: string) => {
    dispatch({ v: 1, type: 'relay_notice', payload: { level: 'warning', message } });
  }, []);

  // Reset accumulated history when switching sources, so a judge never sees the
  // mock's history immediately after connecting to real hardware.
  useEffect(() => {
    dispatch({ v: 1, type: 'device_status', payload: { connected: false, reason: null } });
    if (source === 'live') {
      const socket = new TelemetrySocket({ url, onFrame, onStateChange, onParseError });
      socket.connect();
      return () => socket.close();
    }

    const device = new MockDevice({
      ...mock,
      mode: source === 'legacy-mock' ? 'legacy' : 'telemetry',
      classOrder: mock?.classOrder ?? (source === 'legacy-mock' ? LEGACY_CLASS_ORDER : V1_CLASS_ORDER),
    });
    const stop = device.start((frame) => dispatch(frame));
    return stop;
  }, [source, url, onFrame, onStateChange, onParseError, mock]);

  return useMemo(() => ({ state, source, setSource, relayRttMs }), [state, source, relayRttMs]);
}
