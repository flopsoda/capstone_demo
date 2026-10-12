import type { AgentId, AgentState, CommunicationType, EdgeState } from "./agent";

/* ------------------------------------------------------------------ */
/* Message content                                                     */
/* ------------------------------------------------------------------ */

export type BadgeTone = "high" | "medium" | "info" | "ok" | "warn";

/** Lightweight structured content so backend results render consistently. */
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
  /** Workflow Tool actually run by `to` inside this session (set when it starts). */
  workflow?: string;
  /** Transcript inside the session (messages from `to`, nested sessions). */
  items: ChatItem[];
  /** Result handed back to the requester when the session closed. */
  resultTitle?: string;
  result?: MessageContent;
}

/* ------------------------------------------------------------------ */
/* UI events (derived from backend lifecycle events, see src/live)     */
/* ------------------------------------------------------------------ */

interface BaseEvent {
  /** Milliseconds to hold back the *next* event after applying this one (readability only). */
  delay?: number;
}

/** Failure attributed to a specific agent by the backend (see src/live/api.ts). */
export class LiveRunError extends Error {
  agent: AgentId;
  /** Backend agent id as sent (before any per-surface resolution) and the system that blamed it. */
  rawAgent: string;
  system?: string;
  constructor(agent: AgentId, message: string, rawAgent?: string, system?: string) {
    super(message);
    this.name = "LiveRunError";
    this.agent = agent;
    this.rawAgent = rawAgent ?? agent;
    this.system = system;
  }
}

export type ScenarioEvent =
  /** Demo Step label (Demo Monitor only; the backends know no Steps). */
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
  /** Stops the run: `agent` goes to error, `message` lands in the current transcript, open sessions close. */
  | (BaseEvent & { type: "scenario_error"; agent: AgentId; message: string })
  | (BaseEvent & {
      type: "agent_status";
      agent: AgentId;
      status?: string;
      emphasized?: boolean;
      /** Workflow Tool the agent has (really) started running. */
      workflow?: string;
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

/* ------------------------------------------------------------------ */
/* Runtime state produced by the run reducer (src/state/runState.ts)   */
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
  /** Why the run stopped (status === "error"). */
  error?: string;
  nodes: Record<AgentId, NodeRuntime>;
  edges: Record<string, EdgeRuntime>;
  /** Top-level transcript (user ↔ the agent being talked to). */
  messages: ChatItem[];
  sessions: Record<string, Session>;
  /** Ids of currently open sessions, innermost last. */
  sessionStack: string[];
}
