/**
 * Scenario templates.
 *
 * Each template is a worked example: the problem it sets up, the mechanism the
 * backend actually uses to resolve it, the canonical events to watch, and a
 * staged demonstration that *holds the console on the problem* before it runs
 * the fix. That staging is the point -- a judge needs to see the failure happen
 * and then see it dealt with, not a run that races past both.
 *
 * The scenario name, layout, and size come from `GET /scenarios`, so a template
 * can never describe a scenario the backend does not have. The mechanisms listed
 * here are the ones the coordination and safety subsystems really implement.
 * Nothing in this file simulates any of them; it only explains, links to the
 * run, and asks the backend to reach the next stage.
 */

import type { ScenarioListResponse } from "../api/types";
import type { DemoStage, DemoState } from "../state/useDemo";

export interface ScenarioTemplate {
  name: string;
  title: string;
  problem: string;
  resolution: string;
  events: string[];
  /** What to do on the map once the run starts. */
  watch: string;
  /** Set the problem up, hold it, then run the fix. */
  demo: DemoStage[];
}

const SETTLE: DemoStage["steps"] = [{ kind: "load" }, { kind: "advance" }];

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
    demo: [
      {
        title: "1. The floor is empty",
        caption:
          "Ten robots, six registered tasks, nobody has been given anything yet. Every unit is IDLE because a task with no owner does not negotiate on its own.",
        watch: "The whole fleet parked. The TASKS dock lists six PENDING tasks with no owner.",
        steps: SETTLE,
      },
      {
        title: "2. Bids are collected",
        caption:
          "Negotiation starts for the first task. Eligible robots bid, and each bid carries its own distance, battery, and workload cost. The winner is the cheapest bid, not the first and not the nearest.",
        watch: "Open the NEGOTIATION tab: one row per bidding robot, TOTAL cost sorted, cost split into distance / battery / load.",
        steps: [{ kind: "dispatch" }],
      },
      {
        title: "3. The work is running",
        caption:
          "The winners plan routes and drive. Progress, remaining cells, and remaining time all come from the committed trajectory, so the meters are the simulation's own numbers.",
        watch: "MOVING climbs in the action mix. Select a unit: the inspector shows its route, progress, and destination.",
        steps: [{ kind: "wait", for: "moving", maxTicks: 200 }, { kind: "pause" }],
      },
      {
        title: "4. Tasks complete",
        caption:
          "Each unit reports TASK_COMPLETED on arrival. The run is finished when every task is, and the console then says so instead of looking frozen.",
        watch: "COMPLETED climbs in the status bar. When the run ends, RESTART RUN appears.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "allTasksDone", maxTicks: 6000 }],
      },
    ],
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
    demo: [
      {
        title: "1. Routes that must cross",
        caption:
          "Same floor, no faults. The difference is geometry: several planned routes share the same crossing cell, so two robots are going to want it at the same moment.",
        watch: "Turn on ROUTES. Several dashed paths converge on one cell.",
        steps: SETTLE,
      },
      {
        title: "2. The collision risk — held here",
        caption:
          "The safety layer detects the pair and holds it. This is the problem, paused on purpose so it can be pointed at: a blocked robot does not advance at all, so neither machine moves through the other.",
        watch:
          "Two robots face each other and stop. CONFLICT rises in the status bar, the cells are hatched, and both units read BLOCKED.",
        steps: [{ kind: "wait", for: "conflict", maxTicks: 1500 }],
      },
      {
        title: "3. The way clears",
        caption:
          "One robot is held, the other is released and goes through. The held unit resumes once the cell is free, and the conflict is closed rather than left counting forever.",
        watch:
          "The held unit returns to MOVING, the hatch disappears, and the CONFLICT counter falls back to zero.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "noConflict", maxTicks: 3000 }],
      },
    ],
  },
  {
    name: "deadlock",
    title: "Head-on in a one-cell throat",
    problem:
      "The floor is a two-cell cross-aisle pinched down to a single cell on the east arm. Two robots travelling in opposite directions along that aisle want the only cell there is, so neither can pass and both are stuck: the smallest possible wait cycle.",
    resolution:
      "The blocked set is detected as a cycle in the wait graph. One robot is chosen and given a real planned route out of the way to the nearest cell with room for two, it drives there, and only then is its task route replanned from its new position. The other robot is released immediately and goes through. The retreat is retried on later safety passes if the first route cannot be planned, so a reported deadlock cannot sit there unresolved.",
    events: ["CONFLICT_DETECTED", "DEADLOCK_DETECTED", "RECOVERY_STARTED", "ROUTE_REPLANNED", "TASK_COMPLETED"],
    watch:
      "Watch DEADLOCK in the status bar, then the unit inspector: the yielding robot changes from BLOCKED to REPLANNING, backs out of the aisle, and the pair separates. If a robot were only labelled resolved while standing still, the deadlock counter would rise and never fall.",
    demo: [
      {
        title: "1. A passage with no passing place",
        caption:
          "The east arm narrows to a single cell. Two robots sent to opposite ends of that aisle have to meet in it, and neither can pass the other. Nothing has gone wrong yet.",
        watch: "The single-cell gap between the two-lane aisles, with robots converging on it from both sides.",
        steps: SETTLE,
      },
      {
        title: "2. The deadlock — held here",
        caption:
          "Both robots are blocked on each other. The wait graph has closed a cycle, so this is a deadlock rather than a plain conflict. The console is paused deliberately so it can be pointed at: neither machine moves while it is held.",
        watch:
          "Two robots nose to nose in the gap, both BLOCKED. DEADLOCK is non-zero, and the inspector names the robot each is waiting for.",
        steps: [{ kind: "wait", for: "deadlock", maxTicks: 3000 }],
      },
      {
        title: "3. Recovery: one of them backs off",
        caption:
          "This is the fix, and it is a real move rather than a label. One robot is given a planned route to the nearest cell with room for two; it drives out of the gap, and only once it has arrived is its task route replanned from there.",
        watch:
          "The chosen unit changes from BLOCKED to REPLANNING, leaves the aisle, and the other robot goes through the gap it vacated.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "retreated", maxTicks: 3000 }],
      },
      {
        title: "4. The cycle is gone",
        caption:
          "The standoff is over and the run continues. DEADLOCK returns to zero because the wait graph no longer has a cycle, and the tasks behind it get done.",
        watch: "DEADLOCK back to 0, CONFLICT back to 0, and both units MOVING again.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "noConflict", maxTicks: 4000 }],
      },
    ],
  },
  {
    name: "battery",
    title: "A robot runs flat and its work is taken over",
    problem:
      "Two robots start below the low-battery threshold. That alone is not interesting: a low unit simply bids higher and loses the contested tasks. The real case is a robot that has already accepted a job and then goes flat part-way through it — now a task is owned by a machine that cannot finish it.",
    resolution:
      "A robot under the threshold is reported as DEGRADED and BATTERY_LOW is published with its remaining range. That event is a reassignment trigger: the coordinator asks the rest of the fleet for replacement bids and hands the task to the cheapest valid one, so the job is migrated rather than left stranded.",
    events: ["BATTERY_LOW", "TASK_REASSIGNED", "NEGOTIATION_STARTED", "BID_SUBMITTED", "TASK_ASSIGNED"],
    watch:
      "BATTERY BANDS in FLEET TELEMETRY is the backend's own count. Select the drained unit to see its charge and its DEGRADED state, then watch the owner of its task change in the TASKS tab.",
    demo: [
      {
        title: "1. Two units start low",
        caption:
          "The floor loads with two robots already under the low threshold. They are not disqualified — a low battery raises the cost they submit rather than excluding them — so this is a cost signal, not an outage.",
        watch: "BATTERY BANDS shows a non-zero CRITICAL count, and the two low units are tinted.",
        steps: SETTLE,
      },
      {
        title: "2. A robot takes a job and goes flat",
        caption:
          "A working robot's charge is dropped below the threshold while it is carrying a task. This is the problem, and it is the one that matters: the task still names this robot as its owner, and the robot can no longer finish the route. Held here so it can be pointed at.",
        watch:
          "The drained unit is tinted and low, and its task in the TASKS tab still lists it as the owner. Nobody else has taken it yet.",
        steps: [
          { kind: "dispatch" },
          { kind: "wait", for: "moving", maxTicks: 400 },
          { kind: "drainWorkingRobot" },
          { kind: "wait", for: "batteryLow", maxTicks: 400 },
        ],
      },
      {
        title: "3. The work is migrated to another robot",
        caption:
          "BATTERY_LOW is a reassignment trigger. The coordinator collects replacement bids from the rest of the fleet and hands the task to the cheapest valid one, so the job moves to a robot that can actually complete it.",
        watch:
          "TASK_REASSIGNED appears in the log, the drained unit releases its route, and a different robot picks the task up and starts moving.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "reassigned", maxTicks: 3000 }],
      },
      {
        title: "4. The new owner finishes the job",
        caption:
          "The replacement plans its own route from where it stands and completes the task. The drained unit stays on the floor but is no longer blocking the work.",
        watch: "The new owner MOVING with its own route, and the task reaching COMPLETED.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "allTasksDone", maxTicks: 8000 }],
      },
    ],
  },
  {
    name: "failure",
    title: "A robot fails and its work is taken over",
    problem:
      "The layout is normal, but a robot can be made to fail at any moment. Its task then has no owner and the floor is blocked by a machine that is never coming back.",
    resolution:
      "The failed unit is reported with its failure code and stops moving. The task is offered again, bids are collected from the remaining fleet, and a new owner takes it over — the old unit's incomplete work is migrated, not abandoned.",
    events: ["ROBOT_FAILED", "TASK_REASSIGNED", "NEGOTIATION_STARTED", "BID_SUBMITTED", "TASK_ASSIGNED"],
    watch:
      "Its body dims and glitches, and the event log shows the reassignment from the previous owner to the new one.",
    demo: [
      {
        title: "1. A unit is working",
        caption:
          "A healthy run first, so there is something to lose. One robot is mid-route with a task of its own.",
        watch: "A MOVING unit with a route drawn to its destination.",
        steps: [{ kind: "load" }, { kind: "wait", for: "moving", maxTicks: 600 }],
      },
      {
        title: "2. It fails — held here",
        caption:
          "That unit's actuator fails. It is now dark and static on the map, still physically occupying its cells, and its task has an owner who cannot move it. This is the problem, paused so it can be pointed at.",
        watch: "The failed unit is dim and not moving. Its task still lists it as the owner, and FAILED is up.",
        steps: [{ kind: "failWorkingRobot" }],
      },
      {
        title: "3. The work is migrated",
        caption:
          "The failed robot leaves the running. Its task is offered again to the rest of the fleet, bids come in, and a new owner takes it over. The incomplete work is migrated, not abandoned.",
        watch:
          "The log shows TASK_REASSIGNED with the previous and the new robot, and the owner in the TASKS tab changes.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "reassigned", maxTicks: 3000 }],
      },
      {
        title: "4. The new owner finishes it",
        caption:
          "The replacement plans its own route from where it is standing and completes the task. The fleet carries on with one fewer usable unit, which is the realistic outcome.",
        watch: "The new owner MOVING, and the task reaching COMPLETED.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "allTasksDone", maxTicks: 8000 }],
      },
    ],
  },
  {
    name: "communication",
    title: "A robot goes silent",
    problem:
      "A high-traffic floor where one unit stops answering. It is still physically present and still blocking its corridor, but the coordinator cannot hear from it.",
    resolution:
      "The link is reported as lost with the last contact time and timeout. Its work is migrated to a reachable robot, and the silent unit is drawn dimmed with a broken antenna so it is visibly still on the floor.",
    events: ["COMMUNICATION_LOST", "TASK_REASSIGNED", "RECOVERY_STARTED"],
    watch: "LOST LINK rises, the body greys out, and the fleet keeps working around it.",
    demo: [
      {
        title: "1. A busy floor",
        caption:
          "High-traffic layout with several routes crossing. Plenty of work in flight, and one unit is about to go quiet.",
        watch: "Several MOVING units and crossing routes.",
        steps: [{ kind: "load" }, { kind: "wait", for: "moving", maxTicks: 600 }],
      },
      {
        title: "2. Its link drops — held here",
        caption:
          "The unit stops answering. The difference from a failure is that the machine is still there: still on the floor, still occupying its cells, but no longer reachable. That is why the fleet must work around it rather than through it.",
        watch: "The unit is greyed out, LOST LINK is non-zero, and it has not moved.",
        steps: [{ kind: "dropLinkOnWorkingRobot" }],
      },
      {
        title: "3. Its work moves to someone reachable",
        caption:
          "The silent unit's task is migrated to a robot that can still be commanded, and the rest of the fleet keeps routing around the machine that is on the floor but unreachable.",
        watch: "TASK_REASSIGNED in the log, and a new owner moving on that task.",
        steps: [{ kind: "resume" }, { kind: "wait", for: "reassigned", maxTicks: 3000 }],
      },
    ],
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
      "The action mix in FLEET TELEMETRY shows the fleet split between MOVING, WAITING, and REPLANNING — that ratio is the point of the template.",
    demo: [
      {
        title: "1. Congested by geometry",
        caption:
          "Pillars break the floor into channels, so many routes share the same cells. The pressure comes from the layout, not from a fault.",
        watch: "ROUTES on: a bundle of paths crossing between the pillars.",
        steps: SETTLE,
      },
      {
        title: "2. Conflicts become the norm — held here",
        caption:
          "Several pairs collide at once. The console pauses on the peak so the pattern is visible: this is congestion, not one unlucky pair.",
        watch: "Multiple hatched cells, CONFLICT high, and the action mix split across MOVING, WAITING, and REPLANNING.",
        steps: [{ kind: "wait", for: "conflict", maxTicks: 3000 }],
      },
      {
        title: "3. The fleet works through it",
        caption:
          "Robots yield pairwise and routes are replanned around blocked corridors. Throughput drops, but the floor keeps moving and no machine drives through another.",
        watch: "The conflict count falls while MOVING stays above zero, and REPLANNING shows routes being redrawn.",
        steps: [{ kind: "resume" }, { kind: "advance" }],
      },
    ],
  },
];

export interface TemplatesPanelProps {
  scenarios: ScenarioListResponse | null;
  active: string;
  busy: boolean;
  demo: DemoState;
  onLoad: (name: string) => void;
  onStartDemo: (name: string, stages: DemoStage[]) => void;
}

export function TemplatesPanel(props: TemplatesPanelProps) {
  const { demo } = props;

  return (
    <div className="panel-body templates">
      <p className="muted templates-intro">
        Each template loads a real scenario and shows how the backend resolves it. Nothing here is
        simulated in the browser: the text names the mechanism, the buttons load the scenario, and
        the events listed are the canonical ones to watch in the log.
      </p>

      <p className="muted templates-intro">
        <strong>DEMO</strong> walks a template one stage at a time and <strong>pauses on each
        problem</strong> so it can be explained before the fix runs. Use it to present: the stage
        bar shows where you are, and the caption says what to point at.
      </p>

      {demo.status !== "idle" && demo.stage ? (
        <div className="demo-bar">
          <div className="demo-bar-head">
            <span className="demo-bar-stage">{demo.stage.title}</span>
            <span className="demo-bar-count">
              stage {demo.stageIndex + 1} of {demo.stageCount}
            </span>
            <span className={`demo-bar-status demo-bar-status--${demo.status}`}>
              {demo.status === "running"
                ? (demo.waiting ?? "running…")
                : demo.status === "held"
                  ? "HELD — explain this, then continue"
                  : demo.status === "done"
                    ? "done"
                    : demo.status}
            </span>
          </div>
          <p className="demo-caption">{demo.stage.caption}</p>
          <p className="demo-watch">
            <span>POINT AT</span> {demo.stage.watch}
          </p>
          {demo.error ? <p className="demo-error">{demo.error}</p> : null}
          <div className="demo-controls">
            <button
              className="button button--chip"
              type="button"
              onClick={() => void demo.previous()}
              disabled={demo.status === "running" || demo.stageIndex === 0}
            >
              PREV
            </button>
            <button
              className="button button--primary"
              type="button"
              onClick={() => void demo.next()}
              disabled={demo.status === "running" || demo.status === "done"}
            >
              {demo.stageIndex + 1 >= demo.stageCount ? "FINISH" : "NEXT STAGE"}
            </button>
            <button className="button" type="button" onClick={demo.stop} disabled={demo.status === "running"}>
              STOP
            </button>
          </div>
          <div className="demo-track">
            {Array.from({ length: demo.stageCount }, (_, index) => (
              <span
                key={index}
                className={
                  index < demo.stageIndex
                    ? "demo-tick demo-tick--done"
                    : index === demo.stageIndex
                      ? "demo-tick demo-tick--on"
                      : "demo-tick"
                }
              />
            ))}
          </div>
        </div>
      ) : null}

      <div className="template-grid">
        {TEMPLATES.map((template) => {
          const spec = props.scenarios?.scenarios.find(
            (scenario) => scenario.name === template.name,
          );
          const isActive = props.active === template.name;
          return (
            <article
              key={template.name}
              className={isActive ? "template template--on" : "template"}
            >
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

              <ol className="template-stages">
                {template.demo.map((stage) => (
                  <li key={stage.title} className="template-stage">
                    <span className="template-stage-title">{stage.title}</span>
                    <span className="template-stage-caption">{stage.caption}</span>
                  </li>
                ))}
              </ol>

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
                <span className="button-row">
                  <button
                    className="button button--primary"
                    type="button"
                    disabled={props.busy}
                    onClick={() => props.onStartDemo(template.name, template.demo)}
                  >
                    DEMO
                  </button>
                  <button
                    className="button"
                    type="button"
                    disabled={props.busy || isActive}
                    onClick={() => props.onLoad(template.name)}
                  >
                    LOAD
                  </button>
                </span>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
