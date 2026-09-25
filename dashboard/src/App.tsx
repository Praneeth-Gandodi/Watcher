import {
  Activity,
  BatteryCharging,
  Boxes,
  Network,
  Route,
  ShieldCheck,
  Waypoints,
} from "lucide-react";

const subsystems = [
  {
    icon: Network,
    label: "Decision layer",
    description: "Peer bids, negotiation, assignment, and task migration.",
    owner: "Agent 1",
  },
  {
    icon: ShieldCheck,
    label: "Safety layer",
    description: "Routes, right-of-way, collision, deadlock, and recovery.",
    owner: "Agent 2",
  },
  {
    icon: Activity,
    label: "Fleet telemetry",
    description: "Canonical state, events, metrics, and simulation controls.",
    owner: "Agent 3",
  },
  {
    icon: Boxes,
    label: "Demo system",
    description: "Reproducible scenarios, benchmarks, deployment, and evidence.",
    owner: "Agent 4",
  },
] as const;

function App() {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to dashboard content
      </a>

      <header className="topbar">
        <div className="brand-lockup" aria-label="Watcher fleet operations">
          <div className="brand-mark" aria-hidden="true">
            <Waypoints size={22} strokeWidth={1.8} />
          </div>
          <div>
            <p className="eyebrow">Multi-robot operations</p>
            <p className="brand-name">WATCHER</p>
          </div>
        </div>

        <div className="connection-state" role="status">
          <span className="connection-dot" aria-hidden="true" />
          Contracts ready · runtime not connected
        </div>
      </header>

      <main id="main-content" className="dashboard-main">
        <section className="overview-panel" aria-labelledby="overview-title">
          <div className="overview-copy">
            <p className="section-kicker">Foundation checkpoint</p>
            <h1 id="overview-title">Decentralized fleet coordination</h1>
            <p className="overview-description">
              A typed simulation workspace for negotiating task ownership,
              resolving motion conflicts, and recovering from local failures.
            </p>
          </div>

          <div className="readiness-panel" aria-label="Foundation readiness">
            <div>
              <span className="readiness-value">4</span>
              <span className="readiness-label">owned subsystems</span>
            </div>
            <div>
              <span className="readiness-value">500+</span>
              <span className="readiness-label">robot target</span>
            </div>
          </div>
        </section>

        <section className="visualization-panel" aria-labelledby="visualization-title">
          <div className="panel-heading">
            <div>
              <p className="section-kicker">Industrial workspace</p>
              <h2 id="visualization-title">Fleet visualization boundary</h2>
            </div>
            <span className="scaffold-badge">Canvas integration pending</span>
          </div>

          <div className="map-placeholder" role="img" aria-label="Placeholder for the future robot route visualization">
            <div className="map-grid" aria-hidden="true" />
            <div className="route-line route-line-one" aria-hidden="true" />
            <div className="route-line route-line-two" aria-hidden="true" />
            <div className="map-marker marker-one" aria-hidden="true" />
            <div className="map-marker marker-two" aria-hidden="true" />
            <div className="map-marker marker-three" aria-hidden="true" />
            <div className="map-center-label">
              <Route size={18} aria-hidden="true" />
              Live state and routes attach here
            </div>
          </div>
        </section>

        <section className="subsystem-section" aria-labelledby="subsystems-title">
          <div className="section-heading-row">
            <div>
              <p className="section-kicker">Integration map</p>
              <h2 id="subsystems-title">Protected ownership boundaries</h2>
            </div>
            <p>Parallel implementation without shared-state coupling</p>
          </div>

          <div className="subsystem-grid">
            {subsystems.map(({ icon: Icon, label, description, owner }) => (
              <article className="subsystem-card" key={label}>
                <div className="card-icon" aria-hidden="true">
                  <Icon size={20} strokeWidth={1.8} />
                </div>
                <p className="card-owner">{owner}</p>
                <h3>{label}</h3>
                <p>{description}</p>
              </article>
            ))}
          </div>
        </section>

        <aside className="foundation-note" aria-label="Implementation status">
          <BatteryCharging size={20} aria-hidden="true" />
          <p>
            This React shell intentionally shows no fabricated robot telemetry.
            Live state, controls, and fault injection connect through the
            canonical API and event stream in Agent 3&apos;s implementation phase.
          </p>
        </aside>
      </main>
    </div>
  );
}

export default App;
