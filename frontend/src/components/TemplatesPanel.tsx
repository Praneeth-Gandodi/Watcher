/**
 * Scenario templates.
 *
 * Each template is a worked example: the problem it sets up, the mechanism the
 * backend actually uses to resolve it, and the canonical events to watch for
 * while it runs. The scenario name, layout, and size come from
 * `GET /scenarios`, so a template can never describe a scenario the backend
 * does not have.
 *
 * The mechanisms listed here are the ones the coordination and safety
 * subsystems really implement -- bid-driven allocation, right-of-way yielding,
 * deadlock recovery, replanning, and work migration. Nothing in this file
 * simulates any of them; it only explains and links to the run.
 */

import type { ScenarioListResponse } from "../api/types";

export interface ScenarioTemplate {
  name: string;
  title: string;
  problem: string;
  resolution: string;
  events: string[];
  /** What to do on the map once the run starts. */
  watch: string;
}

export const TEMPLATES: ScenarioTemplate[] = [
  {
    name: "normal",
    title: "Steady operation",
    problem:
      "Six spread tasks on a 40x25 warehouse floor with ten mixed-size robots. No faults, no congestion: this is the baseline the other templates depart from.",
    resolution:
      "Every task runs the full pipeline. Each candidate robot submits a bid with its own distance, battery, and workload cost, the cheapest valid bid wins, and the winner plans a route around the obstacle grid.",
    events: ["TASK_CREATED", "NEGOTIATION_STARTED", "BID_SUBMITTED", "TASK_ASSIGNED", "ROUTE_PLANNED", "TASK_COMPLETED"],
    watch:
      "Turn on ROUTES and LABELS. Robots park until they win a bid, then follow their planned path and report TASK_COMPLETED at the crate.",
  },
  {
    name: "crossing",
    title: "Right of way at a crossing",
    problem:
      "A four-way intersection where routes overlap. Two robots want the same cell in the same instant, so the safety layer raises a collision risk instead of letting them overlap.",
    resolution:
      "The conflicting robot is held and the other proceeds; the blocked one is reported as BLOCKED with the partner named in conflict_with, and once the way clears it resumes. No route is discarded unless the hold goes on too long.",
    events: ["CONFLICT_DETECTED", "ROUTE_REPLANNED", "ROUTE_PLANNED"],
    watch:
      "Turn on CONFLICTS. The hatched cells mark the pair the backend named, and the inspector shows which robot the other is yielding to and for how long.",
  },
  {
    name: "deadlock",
    title: "Mutual blocking in a ring",
    problem:
      "A one-cell ring corridor. Robots travelling in opposite directions can each end up waiting on the other, and neither can move even though both paths are individually valid.",
    resolution:
      "The blocked set is detected, one robot is chosen to yield and backs off to a free cell, and the other continues. The yield is reported as right-of-way state, and a route is replanned when the yield changes the reachable set.",
    events: ["DEADLOCK_DETECTED", "RECOVERY_STARTED", "ROUTE_REPLANNED"],
    watch:
      "Watch the DEADLOCK counter in the status bar and the FLEET TELEMETRY panel, which lists the units the backend currently reports as deadlocked.",
  },
  {
    name: "battery",
    title: "Low battery",
    problem:
      "Two robots start below the low-battery threshold, so taking a long task would strand them mid-route.",
    resolution:
      "A robot under the threshold is reported as DEGRADED and its BATTERY_LOW event carries the estimated remaining range. The allocation cost it submits is higher, so cheaper bids from healthier robots win the contested tasks.",
    events: ["BATTERY_LOW", "BID_SUBMITTED", "TASK_ASSIGNED"],
    watch:
      "Watch BATTERY BANDS in FLEET TELEMETRY: the critical and low counts are the backend's own, not a browser estimate.",
  },
  {
    name: "failure",
    title: "A robot fails and its work is taken over",
    problem:
      "The layout is normal, but a robot can be made to fail at any moment. Its task then has no owner and the floor is blocked by a machine that is never coming back.",
    resolution:
      "The failed unit is reported with its failure code and stops moving. The task is offered again, bids are collected from the remaining fleet, and a new owner takes it over -- the old unit's incomplete work is migrated, not abandoned.",
    events: ["ROBOT_FAILED", "TASK_REASSIGNED", "NEGOTIATION_STARTED", "BID_SUBMITTED", "TASK_ASSIGNED"],
    watch:
      "Select a robot and press INJECT FAILURE. Its body glitches and dims, and the event log shows the reassignment from the previous owner to the new one.",
  },
  {
    name: "communication",
    title: "A robot goes silent",
    problem:
      "A high-traffic floor where one unit stops answering. It is still physically present and still blocking its corridor, but the coordinator cannot hear from it.",
    resolution:
      "The link is reported as lost with the last contact time and timeout. Its work is migrated to a reachable robot, and the silent unit is drawn dimmed with a broken antenna so it is visibly still on the floor.",
    events: ["COMMUNICATION_LOST", "TASK_REASSIGNED", "RECOVERY_STARTED"],
    watch:
      "Select a robot and press DROP LINK. The LOST LINK counter rises, the body greys out, and the fleet keeps working around it.",
  },
  {
    name: "high-traffic",
    title: "Many routes, one floor",
    problem:
      "An open floor broken up by pillars, so many planned routes cross the same cells at once and conflicts are frequent rather than exceptional.",
    resolution:
      "Conflicts are raised per pair, robots yield to each other, and routes are replanned when a corridor is blocked for longer than the safety layer tolerates. The same mechanisms as the crossing template, at a higher rate.",
    events: ["CONFLICT_DETECTED", "ROUTE_REPLANNED", "ROUTE_PLANNED", "TASK_COMPLETED"],
    watch:
      "The action mix in FLEET TELEMETRY shows the fleet split between MOVING, WAITING, and REPLANNING -- that ratio is the whole point of the template.",
  },
];

export interface TemplatesPanelProps {
  scenarios: ScenarioListResponse | null;
  active: string;
  busy: boolean;
  onLoad: (name: string) => void;
}

export function TemplatesPanel({ scenarios, active, busy, onLoad }: TemplatesPanelProps) {
  return (
    <div className="panel-body templates">
      <p className="muted templates-intro">
        Each template loads a real scenario and shows how the backend resolves it. Nothing here is
        simulated in the browser: the text names the mechanism, the buttons load the scenario, and
        the events listed are the canonical ones to watch in the log.
      </p>
      <div className="template-grid">
        {TEMPLATES.map((template) => {
          const spec = scenarios?.scenarios.find((scenario) => scenario.name === template.name);
          const isActive = active === template.name;
          return (
            <article key={template.name} className={isActive ? "template template--on" : "template"}>
              <header className="template-head">
                <h3 className="template-title">{template.title}</h3>
                <span className="template-tag">{template.name.toUpperCase()}</span>
              </header>

              <dl className="template-facts">
                <div>
                  <dt>PROBLEM</dt>
                  <dd>{template.problem}</dd>
                </div>
                <div>
                  <dt>HOW IT IS FIXED</dt>
                  <dd>{template.resolution}</dd>
                </div>
                <div>
                  <dt>WATCH</dt>
                  <dd>{template.watch}</dd>
                </div>
              </dl>

              <div className="template-events">
                {template.events.map((event) => (
                  <span key={event} className="event-chip">
                    {event}
                  </span>
                ))}
              </div>

              <div className="template-foot">
                <span className="muted">
                  {spec
                    ? `${spec.layout} layout · ${spec.columns}x${spec.rows} · ${spec.robot_count} robots · ${spec.task_count} tasks`
                    : "loading scenario details"}
                </span>
                <button
                  className={isActive ? "button button--active" : "button button--primary"}
                  type="button"
                  disabled={busy || isActive}
                  onClick={() => onLoad(template.name)}
                >
                  {isActive ? "LOADED" : "LOAD"}
                </button>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
