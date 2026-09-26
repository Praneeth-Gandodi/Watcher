/**
 * A staged demonstration for one scenario.
 *
 * The point of a template is that a judge can watch a problem happen and then
 * watch the backend deal with it. That needs the run to be *held* at the
 * interesting moment instead of racing past, so each template is a list of
 * stages and the console runs them one at a time, pausing on arrival.
 *
 * Every stage is expressed in backend terms. The console drives the clock and
 * reads the same read models it always does; nothing here simulates a conflict,
 * a deadlock, or an assignment. A stage waits on a condition the backend
 * actually reported (`deadlock`, `reassigned`, `conflict`), which is why the
 * captions can be trusted to match what happened.
 */

import { useCallback, useRef, useState } from "react";
import * as api from "../api/backend";
import type { TelemetryResponse } from "../api/types";

/** Conditions the runner waits for, all read from backend state. */
export type WaitFor =
  | "moving"
  | "conflict"
  | "deadlock"
  | "reassigned"
  | "batteryLow"
  | "linkLost"
  | "retreated"
  | "noConflict"
  | "allTasksDone";

export type StageStep =
  | { kind: "load" }
  | { kind: "dispatch" }
  | { kind: "failWorkingRobot" }
  | { kind: "dropLinkOnWorkingRobot" }
  | { kind: "drainWorkingRobot" }
  | { kind: "pause" }
  | { kind: "resume" }
  | { kind: "advance" }
  | { kind: "wait"; for: WaitFor; maxTicks: number };

export interface DemoStage {
  /** Short label shown on the stepper. */
  title: string;
  /** What a presenter should say while this stage is on screen. */
  caption: string;
  /** What to point at on the map. */
  watch: string;
  steps: StageStep[];
}

export interface DemoTransport {
  /** Pause the clock through the console's own transport, not just the API. */
  pause: () => Promise<void>;
  resume: () => Promise<void>;
  /** Pull the read models so a held stage shows the state the pause produced. */
  refresh: () => Promise<void>;
}

export type DemoStatus = "idle" | "running" | "held" | "done" | "error";

export interface DemoState {
  status: DemoStatus;
  stageIndex: number;
  stage: DemoStage | null;
  stageCount: number;
  error: string | null;
  /** Progress text while a stage is waiting for its condition. */
  waiting: string | null;
  start: (
    name: string,
    stages: DemoStage[],
    transport: DemoTransport,
  ) => Promise<void>;
  next: () => Promise<void>;
  previous: () => Promise<void>;
  stop: () => void;
}

/** Robots currently doing work, so a fault lands on a robot that matters. */
function workingRobotId(telemetry: TelemetryResponse | null): string | null {
  if (!telemetry) return null;
  const active = telemetry.robots.find(
    (robot) => robot.task_id !== null && robot.status !== "failed",
  );
  return active?.robot_id ?? telemetry.robots[0]?.robot_id ?? null;
}

function blockedIds(telemetry: TelemetryResponse | null): string[] {
  return (telemetry?.robots ?? [])
    .filter((robot) => robot.action === "BLOCKED" || robot.conflict_with.length > 0)
    .map((robot) => robot.robot_id);
}

export function useDemo(): DemoState {
  const [status, setStatus] = useState<DemoStatus>("idle");
  const [stageIndex, setStageIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [waiting, setWaiting] = useState<string | null>(null);
  const [stages, setStages] = useState<DemoStage[]>([]);
  const [name, setName] = useState<string>("");
  const transportRef = useRef<DemoTransport | null>(null);
  const tokenRef = useRef(0);
  const stageRef = useRef(0);

  const telemetry = async (): Promise<TelemetryResponse> => api.getTelemetry();

  /** Events published since the stage began, used for the one-shot conditions. */
  const eventsSince = async (cursor: number): Promise<Set<string>> => {
    const types = new Set<string>();
    for (const event of await api.getEvents(cursor)) types.add(event.event_type);
    return types;
  };

  const satisfied = async (
    condition: WaitFor,
    seen: Set<string>,
    snapshot: TelemetryResponse,
    blockedBefore: string[],
  ): Promise<boolean> => {
    switch (condition) {
      case "moving":
        return snapshot.robots.some((robot) => robot.action === "MOVING");
      case "conflict":
        return snapshot.open_conflict_pairs.length > 0;
      case "deadlock":
        return snapshot.deadlocked_robot_ids.length > 0 || seen.has("DEADLOCK_DETECTED");
      case "reassigned":
        return seen.has("TASK_REASSIGNED");
      case "batteryLow":
        return seen.has("BATTERY_LOW");
      case "linkLost":
        return seen.has("COMMUNICATION_LOST");
      case "retreated": {
        // The recovery is real when a robot that was being held has actually
        // left the cell it was stuck on.
        const now = blockedIds(snapshot);
        return (
          blockedBefore.length > 0 &&
          now.length < blockedBefore.length &&
          snapshot.robots.some((robot) => robot.action === "REPLANNING" || robot.action === "MOVING")
        );
      }
      case "noConflict":
        return snapshot.open_conflict_pairs.length === 0 && blockedIds(snapshot).length === 0;
      case "allTasksDone":
        return snapshot.robots.every((robot) => robot.action === "TASK_COMPLETED") ||
          (await api.getTasks()).every(
            (task) => task.status === "completed" || task.status === "cancelled",
          );
    }
  };

  const runStage = useCallback(
    async (index: number): Promise<void> => {
      const token = tokenRef.current;
      const stage = stages[index];
      if (!stage) {
        setStatus("done");
        return;
      }
      setStageIndex(index);
      stageRef.current = index;
      setStatus("running");
      setError(null);
      const transport = transportRef.current;
      // The clock is only held if the console's own transport says so. Pausing
      // the backend directly is not enough: the poll loop is what advances
      // ticks, so the run kept moving between stages and nothing was ever held
      // still for long enough to point at.
      if (!transport) return;

      try {
        for (const step of stage.steps) {
          if (tokenRef.current !== token) return;
          switch (step.kind) {
            case "load": {
              await api.loadScenario({ name });
              await api.dispatchPending();
              break;
            }
            case "dispatch":
              await api.dispatchPending();
              break;
            case "failWorkingRobot": {
              const robotId = workingRobotId(await telemetry());
              if (robotId) {
                await api.failRobot(robotId);
                setWaiting(`failure injected on ${robotId}`);
              }
              break;
            }
            case "dropLinkOnWorkingRobot": {
              const robotId = workingRobotId(await telemetry());
              if (robotId) {
                await api.loseCommunication(robotId);
                setWaiting(`link dropped on ${robotId}`);
              }
              break;
            }
            case "drainWorkingRobot": {
              // The battery case has to be a robot that is *carrying* a task:
              // the coordinator only migrates work that somebody owns, so
              // draining an idle robot would demonstrate nothing.
              const robotId = workingRobotId(await telemetry());
              if (robotId) {
                await api.drainBattery(robotId, 8);
                setWaiting(`battery drained on ${robotId}`);
              }
              break;
            }
            case "pause":
              await transport.pause();
              break;
            case "resume":
              await transport.resume();
              break;
            case "advance":
              await api.advance(20);
              break;
            case "wait": {
              const cursor = (await api.getSnapshot()).last_event_sequence - 400 < 0
                ? 0
                : (await api.getSnapshot()).last_event_sequence - 400;
              const seen = new Set<string>();
              const before = blockedIds(await telemetry());
              setWaiting(`waiting for ${step.for}…`);
              let ticks = 0;
              while (ticks < step.maxTicks) {
                if (tokenRef.current !== token) return;
                const snapshot = await telemetry();
                for (const type of await eventsSince(cursor)) seen.add(type);
                if (await satisfied(step.for, seen, snapshot, before)) {
                  setWaiting(null);
                  break;
                }
                await api.advance(4);
                ticks += 4;
                await transport.refresh();
              }
              setWaiting(null);
              break;
            }
          }
          await transport.refresh();
        }
        // Hold this stage so it can be explained before moving on.
        if (tokenRef.current === token) {
          await transport.pause();
          await transport.refresh();
          setStatus("held");
        }
      } catch (cause) {
        if (tokenRef.current !== token) return;
        setError(cause instanceof Error ? cause.message : String(cause));
        setStatus("error");
      }
    },
    [name, stages],
  );

  const start = useCallback(
    async (scenario: string, script: DemoStage[], transport: DemoTransport) => {
      tokenRef.current += 1;
      setStages(script);
      setName(scenario);
      transportRef.current = transport;
      stageRef.current = 0;
      setWaiting(null);
      await transport.resume();
      await runStage(0);
    },
    [runStage],
  );

  const next = useCallback(async () => {
    const index = stageRef.current + 1;
    if (index >= stages.length) {
      await transportRef.current?.pause();
      setStatus("done");
      return;
    }
    await transportRef.current?.resume();
    await runStage(index);
  }, [runStage, stages.length]);

  const previous = useCallback(async () => {
    const index = Math.max(0, stageRef.current - 1);
    await transportRef.current?.resume();
    await runStage(index);
  }, [runStage]);

  const stop = useCallback(() => {
    tokenRef.current += 1;
    setStatus("idle");
    setWaiting(null);
    setStageIndex(0);
    stageRef.current = 0;
    setStages([]);
    void transportRef.current?.resume();
  }, []);

  const stage = stages[stageIndex] ?? null;

  return {
    status,
    stageIndex,
    stage,
    stageCount: stages.length,
    error,
    waiting,
    start,
    next,
    previous,
    stop,
  };
}
