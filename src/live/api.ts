/**
 * Thin client for the Python agent backend (see backend/app/main.py).
 * `/api` is proxied to the backend by the Vite dev server.
 */

export interface ToolCallRecord {
  name: string;
  args: Record<string, unknown>;
  result: unknown;
}

/** One agent's trace for one request: what it was asked, what it ran, what it answered. */
export interface AgentRun {
  agent: string;
  request: string;
  tool_calls: ToolCallRecord[];
  response: string;
}

export interface KSuperRun {
  delegations: AgentRun[];
}

async function post<T>(path: string, body: unknown): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    throw new Error("Agent backend에 연결할 수 없습니다. (backend 서버가 실행 중인지 확인하세요)");
  }
  if (!res.ok) {
    let detail: string | undefined;
    try {
      const data = (await res.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* non-JSON error body (e.g. the dev proxy's own 502 when the backend is down) */
    }
    if (!detail && (res.status === 502 || res.status === 503 || res.status === 504)) {
      throw new Error(
        `Agent backend에 연결할 수 없습니다. (${res.status}, backend 서버가 실행 중인지 확인하세요)`,
      );
    }
    throw new Error(detail ?? `${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

/** K Electronics Super receives an A2A request and delegates to its specialist agents. */
export function runKSuper(request: string): Promise<KSuperRun> {
  return post<KSuperRun>("/api/k-super/run", { request });
}

/** Direct request to the Meeting Agent (sidebar chat). */
export function runMeetingAgent(request: string): Promise<AgentRun> {
  return post<AgentRun>("/api/agents/meeting/run", { request });
}
