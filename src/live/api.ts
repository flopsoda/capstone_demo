/**
 * Client for the Python agent backend (backend/app/main.py).
 * Run endpoints stream the agent's lifecycle as NDJSON while it executes;
 * `/api` is proxied to the backend by the Vite dev server.
 */

import { AGENTS, type AgentId } from "../types/agent";
import { LiveRunError } from "../types/scenario";

/**
 * Run identity the backend stamps on every event (backend/app/runtime/events.py).
 * `system` is the agent system that emitted it; `relayed` marks a peer
 * system's event that the Group's A2A Tool re-emitted for observation.
 */
export interface EventStamp {
  run_id?: string;
  system?: string;
  origin?: "direct" | "a2a" | string;
  trace_id?: string;
  caller?: string;
  relayed?: boolean;
}

/** Lifecycle events emitted by the backend (backend/app/runtime/events.py). */
export type AgentEvent = EventStamp &
  (
    | { type: "agent_start"; agent: string; request: string }
    | { type: "delegation_start"; from: string; to: string; task: string }
    | { type: "a2a_start"; from: string; to: string; request: string }
    | { type: "tool_start"; agent: string; tool: string; args: Record<string, unknown> }
    | { type: "tool_end"; agent: string; tool: string; result: unknown }
    | { type: "agent_end"; agent: string; response: string }
    | { type: "delegation_end"; from: string; to: string; response: string }
    | { type: "a2a_end"; from: string; to: string; response: string }
    | { type: "error"; agent: string; message: string }
    | { type: "done" }
  );

export function isAgentId(id: string): id is AgentId {
  return id in AGENTS;
}

const CONNECT_ERROR = "Agent backend에 연결할 수 없습니다. (backend 서버가 실행 중인지 확인하세요)";

/**
 * POSTs `request` and calls `onEvent` for each event line as soon as it
 * arrives. Resolves after the backend's `done`; throws `LiveRunError` on the
 * backend's `error` event, or a plain Error for transport problems.
 */
export async function streamAgentRun(
  path: string,
  request: string,
  onEvent: (event: AgentEvent) => void,
  fallbackAgent: AgentId,
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/x-ndjson" },
      body: JSON.stringify({ request }),
    });
  } catch {
    throw new Error(CONNECT_ERROR);
  }
  if (!res.ok || !res.body) {
    const gateway = res.status === 502 || res.status === 503 || res.status === 504;
    throw new Error(gateway ? `${CONNECT_ERROR} (${res.status})` : `${res.status} ${res.statusText}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finished = false;

  const handleLine = (line: string) => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as AgentEvent;
    if (event.type === "error") {
      throw new LiveRunError(isAgentId(event.agent) ? event.agent : fallbackAgent, event.message, event.agent, event.system);
    }
    if (event.type === "done") {
      finished = true;
      return;
    }
    onEvent(event);
  };

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let nl: number;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        handleLine(buffer.slice(0, nl));
        buffer = buffer.slice(nl + 1);
      }
    }
    handleLine(buffer);
  } finally {
    reader.cancel().catch(() => {});
  }
  if (!finished) throw new Error("Agent backend 스트림이 완료 신호 없이 종료되었습니다.");
}

/**
 * Subscribes to a backend's observer channel (GET /api/events, Server-Sent
 * Events). Every event the process emits arrives here, whoever started the
 * run — including runs started by another system's A2A request. Returns the
 * unsubscribe function. The browser's EventSource reconnects by itself.
 */
export function subscribeAgentEvents(
  url: string,
  onEvent: (event: AgentEvent) => void,
  onStatus?: (connected: boolean) => void,
): () => void {
  const source = new EventSource(url);
  source.onopen = () => onStatus?.(true);
  source.onerror = () => onStatus?.(false);
  source.onmessage = (message) => {
    try {
      onEvent(JSON.parse(message.data) as AgentEvent);
    } catch {
      // keep-alive comments never reach onmessage; a malformed line is ignored
    }
  };
  return () => source.close();
}

/* K Electronics backend (Vite proxy: /api → :8000) */
export const MEETING_AGENT_RUN_PATH = "/api/agents/meeting/run";
export const NEWS_AGENT_RUN_PATH = "/api/agents/news/run";
export const ADVISORY_AGENT_RUN_PATH = "/api/agents/advisory/run";
export const K_SUPER_DIRECT_RUN_PATH = "/api/agents/k-super/run";
export const K_EVENTS_PATH = "/api/events";

/* Group backend (Vite proxy: /group-api → :8001/api) */
export const GROUP_SUPER_RUN_PATH = "/group-api/agents/group-super/run";
export const GROUP_MEETING_AGENT_RUN_PATH = "/group-api/agents/meeting/run";
export const GROUP_NEWS_AGENT_RUN_PATH = "/group-api/agents/news/run";
export const GROUP_ADVISORY_AGENT_RUN_PATH = "/group-api/agents/advisory/run";
export const GROUP_EVENTS_PATH = "/group-api/events";
