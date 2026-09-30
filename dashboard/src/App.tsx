/**
 * Dashboard shell.
 *
 * Layout intent: the three panels map to the three questions a judge asks, in
 * the order they ask them.
 *
 *   1. Is it hearing me right now?      -> Live Telemetry & Transcript
 *   2. Does it only fire on the word?   -> Activation Log
 *   3. Can it actually run on this MCU? -> Hardware Resources
 *
 * On a projector (wide screens) the telemetry panel takes the full width of the
 * left column with the other two stacked beneath it; below 1100px everything
 * collapses to a single readable column.
 */

import { useDashboard } from './lib/useDashboard';
import { StatusHeader } from './components/StatusHeader';
import { TelemetryPanel } from './components/TelemetryPanel';
import { ActivationPanel } from './components/ActivationPanel';
import { ResourcesPanel } from './components/ResourcesPanel';
import { Notices } from './components/Notices';

export default function App() {
  const { state, source, setSource, relayRttMs } = useDashboard();

  return (
    <div className="app">
      <a className="skip-link" href="#telemetry">
        Skip to live telemetry
      </a>
      <StatusHeader
        state={state}
        link={state.link}
        linkDetail={state.linkDetail}
        source={source}
        onSourceChange={setSource}
        relayRttMs={relayRttMs}
      />
      <Notices state={state} />
      <main className="app__grid">
        <div className="app__column app__column--primary">
          <TelemetryPanel state={state} />
        </div>
        <div className="app__column app__column--secondary">
          <ActivationPanel state={state} />
          <ResourcesPanel state={state} />
        </div>
      </main>
      <footer className="app__footer">
        <p>
          Telemetry protocol v1 · observers fan out from the single ESP32 link · no audio is written to disk ·
          absent measurements render as <code className="mono">—</code>
        </p>
      </footer>
    </div>
  );
}
