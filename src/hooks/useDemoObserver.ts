import { useEffect, useReducer, useState } from "react";
import { createAgentEventMapper, resolveForFullGraph } from "../live/agentEvents";
import { isAgentId, subscribeAgentEvents, type AgentEvent } from "../live/api";
import { stepFor } from "../monitor/steps";
import type { AgentId } from "../types/agent";
import type { RunnerState } from "../types/scenario";
import { createEventQueue, createInitialRunnerState, reducer } from "../state/runState";

export interface DemoObserver extends RunnerState {
  /** Whether the observer channel is currently open. */
  connected: boolean;
  /** Clears graph / transcript state; the subscription stays. */
  reset: () => void;
}

/**
 * Observes one backend's event channel (SSE) and turns it into the same
 * graph / transcript state the scenario runner produces — nothing is
 * scheduled or invented, every change is a real lifecycle event.
 *
 * The first `agent_start` of a run that was started in the observed system
 * (not relayed from a peer) begins a fresh view: the transcript shows that
 * request, and every event of that run — including the peer's relayed
 * lifecycle — is applied in arrival order. The run's `done` completes its
 * root agent; an `error` stops the view at the agent the backend blamed.
 */
export function useDemoObserver(eventsUrl: string): DemoObserver {
  const [state, dispatch] = useReducer(reducer, undefined, createInitialRunnerState);
  const [connected, setConnected] = useState(false);
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    let rootRun: string | null = null;
    let rootAgent: AgentId | null = null;
    let failed = false;
    let mapEvent = createAgentEventMapper(resolveForFullGraph);
    let queue = createEventQueue((event) => dispatch({ type: "apply", event }), () => true);

    const beginRun = (ev: Extract<AgentEvent, { type: "agent_start" }>) => {
      rootRun = ev.run_id ?? null;
      failed = false;
      const current = rootRun;
      mapEvent = createAgentEventMapper(resolveForFullGraph);
      queue = createEventQueue(
        (event) => dispatch({ type: "apply", event }),
        () => rootRun === current,
      );
      dispatch({ type: "start", userText: ev.request });
    };

    const unsubscribe = subscribeAgentEvents(
      eventsUrl,
      (ev) => {
        const ownRun = !ev.relayed && ev.run_id !== undefined;
        if (ev.type === "agent_start" && ownRun && ev.run_id !== rootRun) {
          // First event of a new run started in this system: a fresh view.
          beginRun(ev);
          const agent = resolveForFullGraph(ev.system, ev.agent);
          rootAgent = isAgentId(agent) ? agent : null;
          if (rootAgent) queue.push({ type: "agent_start", agent: rootAgent, from: "chairman", status: "요청 분석 중..." });
        }
        if (rootRun === null) return; // events of a run that began before we subscribed
        if (ev.relayed === undefined && ev.run_id !== rootRun && ev.trace_id !== rootRun) return;

        const step = stepFor(ev);
        if (step) queue.push(step);
        mapEvent(ev).forEach(queue.push);

        if (ev.type === "error") {
          // The peer's own error names the precise agent; the Group's follow-up error repeats it.
          if (failed) return;
          failed = true;
          const agent = resolveForFullGraph(ev.system, ev.agent);
          queue.push({ type: "scenario_error", agent: isAgentId(agent) ? agent : "group_super", message: ev.message });
        }
        if (ev.type === "done" && ownRun && ev.run_id === rootRun && !failed) {
          if (rootAgent) queue.push({ type: "agent_complete", agent: rootAgent, from: "chairman" });
          queue.push({ type: "scenario_complete" });
        }
      },
      setConnected,
    );
    return unsubscribe;
  }, [eventsUrl, generation]);

  const reset = () => {
    dispatch({ type: "reset" });
    setGeneration((g) => g + 1); // resubscribe so a half-applied run is dropped
  };

  return { ...state, connected, reset };
}
