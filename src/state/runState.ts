/**
 * Agent run state: the reducer that turns lifecycle-derived events
 * (src/types/scenario.ts ScenarioEvent) into nodes / edges / transcript /
 * sessions, and the ordered queue that applies them as they arrive. Used by
 * the platform UIs (per chat tab) and the Demo Monitor. Nothing here is
 * scheduled or invented: every change is an event the backend emitted.
 */

import { AGENTS, TOPOLOGY, findTopologyEdge, type AgentId } from "../types/agent";
import {
  type ChatItem,
  type EdgeRuntime,
  type NodeRuntime,
  type RunnerState,
  type ScenarioEvent,
  type Session,
} from "../types/scenario";

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
  /** Fresh state with the user's request as the first transcript item (observer views). */
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
 * Records the Workflow Tool an agent started on the innermost open session
 * it is answering, so the session header/replay show what actually ran.
 */
function withSessionWorkflow(state: RunnerState, agent: AgentId, workflow?: string): RunnerState {
  if (!workflow) return state;
  const top = state.sessionStack[state.sessionStack.length - 1];
  if (!top || state.sessions[top].to !== agent) return state;
  return {
    ...state,
    sessions: { ...state.sessions, [top]: { ...state.sessions[top], workflow } },
  };
}

/** Rewrites the item list of the innermost open session, or of the top-level transcript. */
function mapContainer(state: RunnerState, fn: (items: ChatItem[]) => ChatItem[]): RunnerState {
  const top = state.sessionStack[state.sessionStack.length - 1];
  if (!top) return { ...state, messages: fn(state.messages) };
  const session = state.sessions[top];
  return { ...state, sessions: { ...state.sessions, [top]: { ...session, items: fn(session.items) } } };
}

/**
 * Appends a chat item to the innermost open session, or to the top-level
 * transcript when no session is open.
 */
function pushItem(state: RunnerState, item: ChatItem): RunnerState {
  return mapContainer(state, (items) => [...items, item]);
}

type Draft = Extract<ChatItem, { kind: "assistant" }>;

/** Index of the sender's open streaming draft in `items`, or -1. Only the last item can be one. */
function draftIndex(items: ChatItem[], sender: AgentId): number {
  const last = items[items.length - 1];
  return last && last.kind === "assistant" && last.streaming && last.sender === sender ? items.length - 1 : -1;
}

function applyEvent(state: RunnerState, ev: ScenarioEvent): RunnerState {
  switch (ev.type) {
    case "step":
      return { ...state, currentStep: ev.step, stepTitle: ev.title };

    case "agent_start": {
      // A fresh start shows only the Workflow Tool this start declares; a live
      // agent gets its workflow later, when the backend reports tool_start.
      const nodes = {
        ...state.nodes,
        [ev.agent]: { state: "active", status: ev.status, workflow: ev.workflow } satisfies NodeRuntime,
      };
      let edges = state.edges;
      if (ev.from) {
        edges = setEdge(edges, ev.from, ev.agent, "active");
        // A caller that is still idle (e.g. the Chairman) becomes active too.
        if (nodes[ev.from].state === "idle") {
          nodes[ev.from] = { state: "active", status: "요청 중" };
        }
      }
      return withSessionWorkflow({ ...state, nodes, edges }, ev.agent, ev.workflow);
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

    case "agent_error":
      return {
        ...state,
        nodes: {
          ...state.nodes,
          [ev.agent]: { state: "error", workflow: state.nodes[ev.agent].workflow },
        },
      };

    case "scenario_error": {
      const failed = applyEvent(state, { type: "agent_error", agent: ev.agent });
      const withMessage = pushItem(failed, {
        id: nextId(),
        kind: "assistant",
        sender: ev.agent,
        title: "오류",
        content: [
          { kind: "badge", text: "실행 중단", tone: "high" },
          { kind: "text", text: ev.message },
        ],
      });
      // Nothing will close the sessions the failed run left open: close them so
      // the next request is not routed into a stale window.
      const sessions = { ...withMessage.sessions };
      for (const id of withMessage.sessionStack) {
        sessions[id] = { ...sessions[id], status: "closed", resultTitle: "중단" };
      }
      return { ...withMessage, sessions, sessionStack: [], status: "error", error: ev.message };
    }

    case "agent_status":
      return withSessionWorkflow(
        {
          ...state,
          nodes: {
            ...state.nodes,
            [ev.agent]: {
              ...state.nodes[ev.agent],
              status: ev.status,
              emphasized: ev.emphasized ?? false,
              workflow: ev.workflow ?? state.nodes[ev.agent].workflow,
            },
          },
        },
        ev.agent,
        ev.workflow,
      );

    case "edge":
      return { ...state, edges: setEdge(state.edges, ev.from, ev.to, ev.state) };

    case "message":
      return mapContainer(state, (items) => {
        const i = ev.final ? draftIndex(items, ev.sender) : -1;
        const item: ChatItem = { id: nextId(), kind: "assistant", sender: ev.sender, title: ev.title, content: ev.content };
        // The final answer replaces the draft that was growing in its place.
        return i >= 0 ? [...items.slice(0, i), { ...item, id: items[i].id }] : [...items, item];
      });

    case "message_delta":
      return mapContainer(state, (items) => {
        const i = draftIndex(items, ev.sender);
        if (i >= 0) {
          const draft = items[i] as Draft;
          const text = typeof draft.content === "string" ? draft.content : "";
          return [...items.slice(0, i), { ...draft, content: text + ev.text }];
        }
        const draft: Draft = { id: nextId(), kind: "assistant", sender: ev.sender, title: ev.title, content: ev.text, streaming: true };
        return [...items, draft];
      });

    case "message_commit":
      return mapContainer(state, (items) => {
        const i = draftIndex(items, ev.sender);
        return i >= 0 ? [...items.slice(0, i), { ...(items[i] as Draft), streaming: false }] : items;
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

export type RunnerAction = Action;

export function reducer(state: RunnerState, action: Action): RunnerState {
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
/* Ordered application of arriving events                              */
/* ------------------------------------------------------------------ */

const sleep = (ms: number) => new Promise<void>((resolve) => window.setTimeout(resolve, ms));

/**
 * Applies events in arrival order. An event's `delay` holds back only the
 * *next* one (e.g. keep a result readable before its session collapses);
 * state itself changes the moment an event arrives. `isCurrent` lets a
 * reset/restart drop whatever is still queued.
 */
export function createEventQueue(apply: (event: ScenarioEvent) => void, isCurrent: () => boolean) {
  const pending: ScenarioEvent[] = [];
  let draining = false;
  let drained: Promise<void> = Promise.resolve();
  const drain = async () => {
    draining = true;
    try {
      while (pending.length > 0 && isCurrent()) {
        const next = pending.shift()!;
        apply(next);
        if (next.delay) await sleep(next.delay);
      }
    } finally {
      draining = false;
    }
  };
  return {
    push(event: ScenarioEvent) {
      pending.push(event);
      if (!draining) drained = drain();
    },
    /** Resolves once everything queued so far has been applied. */
    settled: () => drained,
  };
}
