import { useCallback, useEffect, useReducer, useRef } from "react";
import { AGENTS, TOPOLOGY, findTopologyEdge, type AgentId } from "../types/agent";
import type {
  ChatItem,
  EdgeRuntime,
  NodeRuntime,
  RunnerState,
  Scenario,
  ScenarioEvent,
  Session,
} from "../types/scenario";

const DEFAULT_DELAY = 500;

/* ------------------------------------------------------------------ */
/* Initial state                                                       */
/* ------------------------------------------------------------------ */

function initialNodes(): Record<AgentId, NodeRuntime> {
  const out = {} as Record<AgentId, NodeRuntime>;
  for (const id of Object.keys(AGENTS) as AgentId[]) out[id] = { state: "idle" };
  return out;
}

function initialEdges(): Record<string, EdgeRuntime> {
  const out: Record<string, EdgeRuntime> = {};
  for (const e of TOPOLOGY) out[e.id] = { state: "idle", direction: "forward" };
  return out;
}

export function createInitialRunnerState(): RunnerState {
  return {
    status: "idle",
    currentStep: 0,
    stepTitle: "",
    nodes: initialNodes(),
    edges: initialEdges(),
    messages: [],
    sessions: {},
    sessionStack: [],
  };
}

/* ------------------------------------------------------------------ */
/* Reducer                                                             */
/* ------------------------------------------------------------------ */

type Action =
  | { type: "reset" }
  | { type: "start"; userText: string }
  | { type: "apply"; event: ScenarioEvent }
  | { type: "append_message"; item: ChatItem };

let idCounter = 0;
const nextId = () => `m${++idCounter}`;

function setEdge(
  edges: Record<string, EdgeRuntime>,
  from: AgentId,
  to: AgentId,
  state: EdgeRuntime["state"],
): Record<string, EdgeRuntime> {
  const found = findTopologyEdge(from, to);
  if (!found) return edges;
  return {
    ...edges,
    [found.edge.id]: { state, direction: found.direction },
  };
}

/**
 * Appends a chat item to the innermost open session, or to the top-level
 * transcript when no session is open.
 */
function pushItem(state: RunnerState, item: ChatItem): RunnerState {
  const top = state.sessionStack[state.sessionStack.length - 1];
  if (!top) return { ...state, messages: [...state.messages, item] };
  const session = state.sessions[top];
  return {
    ...state,
    sessions: {
      ...state.sessions,
      [top]: { ...session, items: [...session.items, item] },
    },
  };
}

function applyEvent(state: RunnerState, ev: ScenarioEvent): RunnerState {
  switch (ev.type) {
    case "step":
      return { ...state, currentStep: ev.step, stepTitle: ev.title };

    case "agent_start": {
      const nodes = {
        ...state.nodes,
        [ev.agent]: {
          state: "active",
          status: ev.status,
          workflow: ev.workflow ?? state.nodes[ev.agent].workflow,
        } satisfies NodeRuntime,
      };
      let edges = state.edges;
      if (ev.from) {
        edges = setEdge(edges, ev.from, ev.agent, "active");
        // A caller that is still idle (e.g. the Chairman) becomes active too.
        if (nodes[ev.from].state === "idle") {
          nodes[ev.from] = { state: "active", status: "Requesting" };
        }
      }
      return { ...state, nodes, edges };
    }

    case "agent_complete": {
      const nodes = {
        ...state.nodes,
        [ev.agent]: {
          state: "completed",
          workflow: state.nodes[ev.agent].workflow,
        } satisfies NodeRuntime,
      };
      let edges = state.edges;
      if (ev.from) edges = setEdge(edges, ev.from, ev.agent, "completed");
      return { ...state, nodes, edges };
    }

    case "agent_status":
      return {
        ...state,
        nodes: {
          ...state.nodes,
          [ev.agent]: {
            ...state.nodes[ev.agent],
            status: ev.status,
            emphasized: ev.emphasized ?? false,
          },
        },
      };

    case "edge":
      return { ...state, edges: setEdge(state.edges, ev.from, ev.to, ev.state) };

    case "message":
      return pushItem(state, {
        id: nextId(),
        kind: "assistant",
        sender: ev.sender,
        title: ev.title,
        content: ev.content,
      });

    case "activity":
      return pushItem(state, {
        id: nextId(),
        kind: "activity",
        communication: ev.communication,
        from: ev.from,
        to: ev.to,
        title: ev.title,
        content: ev.content,
      });

    case "comparison":
      return pushItem(state, { id: nextId(), kind: "comparison", data: ev.data });

    case "session_open": {
      const sessionId = `session-${nextId()}`;
      const parentId = state.sessionStack[state.sessionStack.length - 1];
      const session: Session = {
        id: sessionId,
        communication: ev.communication,
        from: ev.from,
        to: ev.to,
        parentId,
        status: "open",
        title: ev.title,
        request: ev.content,
        items: [],
      };
      // The placeholder lands in the parent transcript; the session itself
      // becomes the routing target for whatever follows.
      const withCard = pushItem(state, { id: nextId(), kind: "session", sessionId });
      return {
        ...withCard,
        sessions: { ...withCard.sessions, [sessionId]: session },
        sessionStack: [...withCard.sessionStack, sessionId],
      };
    }

    case "session_close": {
      const top = state.sessionStack[state.sessionStack.length - 1];
      if (!top) return state;
      const session = state.sessions[top];
      return {
        ...state,
        sessions: {
          ...state.sessions,
          [top]: {
            ...session,
            status: "closed",
            resultTitle: ev.resultTitle,
            result: ev.result,
          },
        },
        sessionStack: state.sessionStack.slice(0, -1),
      };
    }

    case "scenario_complete": {
      // Every node that took part in the scenario ends in the completed state.
      const nodes = {} as Record<AgentId, NodeRuntime>;
      for (const id of Object.keys(state.nodes) as AgentId[]) {
        const n = state.nodes[id];
        nodes[id] = n.state === "idle" ? n : { state: "completed", workflow: n.workflow };
      }
      const edges: Record<string, EdgeRuntime> = {};
      for (const [id, e] of Object.entries(state.edges)) {
        edges[id] = e.state === "active" ? { ...e, state: "completed" } : e;
      }
      return { ...state, status: "completed", nodes, edges };
    }

    default:
      return state;
  }
}

function reducer(state: RunnerState, action: Action): RunnerState {
  switch (action.type) {
    case "reset":
      return createInitialRunnerState();
    case "start":
      return {
        ...createInitialRunnerState(),
        status: "running",
        messages: [{ id: nextId(), kind: "user", content: action.userText }],
      };
    case "append_message":
      return { ...state, messages: [...state.messages, action.item] };
    case "apply":
      return applyEvent(state, action.event);
    default:
      return state;
  }
}

/* ------------------------------------------------------------------ */
/* Hook                                                                */
/* ------------------------------------------------------------------ */

export interface ScenarioRunner extends RunnerState {
  totalSteps: number;
  title: string;
  /** Starts (or restarts) the scenario with the given user message. */
  start: (userText: string) => void;
  /** Stops any in-flight playback and clears graph + chat state. */
  reset: () => void;
  /** Adds a plain chat item without touching graph state (non-scenario replies). */
  appendMessage: (item: ChatItem) => void;
  /**
   * Runs a specialist agent on its own, outside the orchestrated scenario
   * (a user talking to the agent directly). The node goes active with the
   * given Workflow Tool and completes after `durationMs`. No edge is involved.
   */
  runDirect: (agent: AgentId, workflow: string, durationMs: number) => void;
}

/**
 * Replays a scenario's event list on a timer. The hook is intentionally
 * agnostic of the UI: it only produces node/edge runtime state and chat items.
 * Swapping the timer-driven replay for a real agent event stream later only
 * requires dispatching `apply` with live events.
 */
export function useScenarioRunner(scenario: Scenario): ScenarioRunner {
  const [state, dispatch] = useReducer(reducer, undefined, createInitialRunnerState);
  const timerRef = useRef<number | null>(null);
  const runIdRef = useRef(0);

  const clearTimer = () => {
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  };

  const reset = useCallback(() => {
    runIdRef.current += 1;
    clearTimer();
    dispatch({ type: "reset" });
  }, []);

  const start = useCallback(
    (userText: string) => {
      runIdRef.current += 1;
      const runId = runIdRef.current;
      clearTimer();
      dispatch({ type: "start", userText });

      let index = 0;
      const tick = () => {
        if (runId !== runIdRef.current) return;
        if (index >= scenario.events.length) return;
        const ev = scenario.events[index++];
        dispatch({ type: "apply", event: ev });
        timerRef.current = window.setTimeout(tick, ev.delay ?? DEFAULT_DELAY);
      };

      timerRef.current = window.setTimeout(tick, 400);
    },
    [scenario],
  );

  const appendMessage = useCallback((item: ChatItem) => {
    dispatch({ type: "append_message", item });
  }, []);

  // Direct runs use their own timers so they never interfere with scenario playback.
  const directTimers = useRef<Set<number>>(new Set());
  const runDirect = useCallback((agent: AgentId, workflow: string, durationMs: number) => {
    dispatch({
      type: "apply",
      event: { type: "agent_start", agent, workflow, status: "Analyzing..." },
    });
    const id = window.setTimeout(() => {
      directTimers.current.delete(id);
      dispatch({ type: "apply", event: { type: "agent_complete", agent } });
    }, durationMs);
    directTimers.current.add(id);
  }, []);

  useEffect(() => {
    const timers = directTimers.current;
    return () => {
      clearTimer();
      for (const id of timers) window.clearTimeout(id);
      timers.clear();
    };
  }, []);

  return {
    ...state,
    totalSteps: scenario.totalSteps,
    title: scenario.title,
    start,
    reset,
    appendMessage,
    runDirect,
  };
}
