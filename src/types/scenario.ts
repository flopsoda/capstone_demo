import type { AgentId, AgentState, CommunicationType, EdgeState } from "./agent";

/* ------------------------------------------------------------------ */
/* Message content                                                     */
/* ------------------------------------------------------------------ */

export type BadgeTone = "high" | "medium" | "info" | "ok" | "warn";

/** Lightweight structured content so mock responses render consistently. */
export type MessageBlock =
  | { kind: "text"; text: string }
  | { kind: "label"; text: string }
  | { kind: "kv"; items: { k: string; v: string }[] }
  | { kind: "list"; items: string[]; ordered?: boolean }
  | { kind: "badge"; text: string; tone: BadgeTone };

export type MessageContent = string | MessageBlock[];

/* ------------------------------------------------------------------ */
/* Chat items                                                          */
/* ------------------------------------------------------------------ */

export interface ComparisonSide {
  agent: AgentId;
  stance: string;
  tone: "warn" | "ok";
  items: { k: string; v: string }[];
  risksLabel: string;
  risks: string[];
}

export interface ComparisonData {
  title: string;
  left: ComparisonSide;
  right: ComparisonSide;
}

export type ChatItem =
  | { id: string; kind: "user"; content: string }
  | {
      id: string;
      kind: "assistant";
      sender: AgentId;
      title?: string;
      content: MessageContent;
    }
  | {
      id: string;
      kind: "activity";
      communication: CommunicationType;
      from: AgentId;
      to: AgentId;
      /** Badge text override, e.g. "A2A Response". */
      title?: string;
      content: MessageContent;
    }
  | { id: string; kind: "comparison"; data: ComparisonData }
  /** Placeholder in the parent transcript for a sub-session (A2A / delegation). */
  | { id: string; kind: "session"; sessionId: string };

/* ------------------------------------------------------------------ */
/* Sessions (A2A / delegation sub-conversations)                       */
/* ------------------------------------------------------------------ */

export interface Session {
  id: string;
  communication: CommunicationType;
  from: AgentId;
  to: AgentId;
  parentId?: string;
  status: "open" | "closed";
  /** Badge override, e.g. "A2A Request". */
  title?: string;
  /** The natural-language request that opened the session. */
  request: MessageContent;
  /** Transcript inside the session (messages from `to`, nested sessions). */
  items: ChatItem[];
  /** Result handed back to the requester when the session closed. */
  resultTitle?: string;
  result?: MessageContent;
}

/* ------------------------------------------------------------------ */
/* Scenario events                                                     */
/* ------------------------------------------------------------------ */

interface BaseEvent {
  /** Milliseconds to wait after applying this event before the next one. */
  delay?: number;
}

/** Handed to a `live` event while it runs, for intermediate graph/chat updates. */
export interface LiveContext {
  emit: (event: ScenarioEvent) => void;
}

export type ScenarioEvent =
  /**
   * A real agent execution instead of mock playback. The runner awaits `run`
   * and then plays the returned events in place. If `run` throws, the runner
   * marks `agent` and the scenario as failed and stops.
   */
  | (BaseEvent & {
      type: "live";
      agent: AgentId;
      run: (ctx: LiveContext) => Promise<ScenarioEvent[]>;
    })
  | (BaseEvent & { type: "step"; step: number; title: string })
  | (BaseEvent & {
      type: "agent_start";
      agent: AgentId;
      from?: AgentId;
      communication?: CommunicationType;
      status?: string;
      /** Workflow Tool the agent selected for this request. */
      workflow?: string;
    })
  | (BaseEvent & { type: "agent_complete"; agent: AgentId; from?: AgentId })
  | (BaseEvent & { type: "agent_error"; agent: AgentId })
  /** Stops the scenario: `agent` goes to error and `message` lands in the current transcript. */
  | (BaseEvent & { type: "scenario_error"; agent: AgentId; message: string })
  | (BaseEvent & {
      type: "agent_status";
      agent: AgentId;
      status?: string;
      emphasized?: boolean;
    })
  | (BaseEvent & {
      type: "edge";
      from: AgentId;
      to: AgentId;
      state: EdgeState;
    })
  | (BaseEvent & {
      type: "message";
      sender: AgentId;
      title?: string;
      content: MessageContent;
    })
  | (BaseEvent & {
      type: "activity";
      communication: CommunicationType;
      from: AgentId;
      to: AgentId;
      title?: string;
      content: MessageContent;
    })
  | (BaseEvent & { type: "comparison"; data: ComparisonData })
  /**
   * Opens a sub-session: posts the request card into the current transcript
   * and routes subsequent message/activity events into the new session.
   */
  | (BaseEvent & {
      type: "session_open";
      communication: CommunicationType;
      from: AgentId;
      to: AgentId;
      title?: string;
      content: MessageContent;
    })
  /** Closes the innermost open session, optionally attaching its result. */
  | (BaseEvent & {
      type: "session_close";
      resultTitle?: string;
      result?: MessageContent;
    })
  | (BaseEvent & { type: "scenario_complete" });

export interface Scenario {
  id: string;
  title: string;
  totalSteps: number;
  /** Returns true when a user message should trigger this scenario. */
  matches: (text: string) => boolean;
  events: ScenarioEvent[];
}

/* ------------------------------------------------------------------ */
/* Runtime state produced by the scenario runner                       */
/* ------------------------------------------------------------------ */

export type ScenarioStatus = "idle" | "running" | "completed" | "error";

export interface NodeRuntime {
  state: AgentState;
  status?: string;
  emphasized?: boolean;
  /** Workflow Tool currently (or last) executed by a specialist agent. */
  workflow?: string;
}

export interface EdgeRuntime {
  state: EdgeState;
  direction: "forward" | "reverse";
}

export interface RunnerState {
  status: ScenarioStatus;
  currentStep: number;
  stepTitle: string;
  /** Why the scenario stopped (status === "error"). */
  error?: string;
  nodes: Record<AgentId, NodeRuntime>;
  edges: Record<string, EdgeRuntime>;
  /** Top-level transcript (Chairman ↔ Group Super). */
  messages: ChatItem[];
  sessions: Record<string, Session>;
  /** Ids of currently open sessions, innermost last. */
  sessionStack: string[];
}
