/**
 * Maps backend lifecycle events (src/live/api.ts) to the scenario events the
 * reducer already understands. Nothing here is scheduled or invented: each
 * backend event becomes UI state the moment it arrives.
 *
 *   agent_start (super)      → node status text
 *   delegation_start         → delegation session opens, specialist node active, edge active
 *   agent_start (specialist) → node status text
 *   tool_start               → node shows the Workflow Tool it is running (session too)
 *   tool_end                 → Workflow result message in the session
 *   agent_end (specialist)   → final answer message, node completed
 *   delegation_end           → session closes, delegator status text
 *   error                    → thrown by the client as LiveRunError → scenario_error
 */

import { AGENTS, type AgentId } from "../types/agent";
import type { MessageBlock, ScenarioEvent } from "../types/scenario";
import { isAgentId, type AgentEvent } from "./api";

/* ------------------------------------------------------------------ */
/* Workflow Tool results → chat blocks                                 */
/* ------------------------------------------------------------------ */

/** Shape returned by run_meeting_context_retrieval (backend/app/workflows). */
interface MeetingContextResult {
  matched: number;
  meetings: {
    date: string;
    title: string;
    decisions: { speaker: string; statement: string }[];
    open_action_items: { text: string; owner?: string | null }[];
  }[];
}

function isMeetingContextResult(v: unknown): v is MeetingContextResult {
  return typeof v === "object" && v !== null && Array.isArray((v as { meetings?: unknown }).meetings);
}

function meetingContextBlocks(result: unknown): MessageBlock[] {
  if (!isMeetingContextResult(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  if (result.meetings.length === 0) return [{ kind: "text", text: "관련 회의 기록을 찾지 못했습니다." }];
  return result.meetings.flatMap((m): MessageBlock[] => [
    { kind: "label", text: `${m.date} · ${m.title}` },
    { kind: "kv", items: m.decisions.map((d) => ({ k: d.speaker, v: d.statement })) },
    { kind: "label", text: "미완료 Action Item" },
    {
      kind: "list",
      items: m.open_action_items.map((a) => (a.owner ? `${a.text} (${a.owner})` : a.text)),
    },
  ]);
}

/** Renders a tool's arguments + result. Add a case per Workflow Tool (Step 4: news). */
export function toolResultBlocks(tool: string, args: Record<string, unknown>, result: unknown): MessageBlock[] {
  const query = typeof args.query === "string" ? args.query : undefined;
  const head: MessageBlock[] = query
    ? [
        { kind: "label", text: "조회 질의" },
        { kind: "text", text: query },
      ]
    : [];
  switch (tool) {
    case "run_meeting_context_retrieval":
      return [...head, ...meetingContextBlocks(result)];
    default:
      return [...head, { kind: "text", text: JSON.stringify(result, null, 2) }];
  }
}

/** Title of a specialist agent's final answer in the chat. */
const RESULT_TITLE: Partial<Record<AgentId, string>> = {
  meeting: "회의 맥락",
};

/** Time the final answer stays readable before its delegation session collapses (UI only). */
const READ_MS = 4000;

/* ------------------------------------------------------------------ */
/* Backend events → scenario events                                    */
/* ------------------------------------------------------------------ */

/**
 * Returns a mapper that remembers who delegated to whom (needed to complete
 * the right edge when a specialist finishes). One mapper per run.
 */
export function createAgentEventMapper() {
  const delegatorOf = new Map<AgentId, AgentId>();
  const toolArgs = new Map<string, Record<string, unknown>>();

  return function mapAgentEvent(ev: AgentEvent): ScenarioEvent[] {
    switch (ev.type) {
      case "agent_start": {
        if (!isAgentId(ev.agent)) return [];
        // Specialists were already lit by delegation_start (or runDirectLive); this is the model starting work.
        return [{ type: "agent_status", agent: ev.agent, status: "요청 분석 중..." }];
      }

      case "delegation_start": {
        if (!isAgentId(ev.from) || !isAgentId(ev.to)) return [];
        delegatorOf.set(ev.to, ev.from);
        return [
          {
            type: "session_open",
            communication: "delegation",
            from: ev.from,
            to: ev.to,
            content: `"${ev.task}"`,
          },
          { type: "agent_start", agent: ev.to, from: ev.from, communication: "delegation", status: "요청 수신" },
          { type: "agent_status", agent: ev.from, status: `${AGENTS[ev.to].shortName} 위임 중...` },
        ];
      }

      case "tool_start": {
        if (!isAgentId(ev.agent)) return [];
        toolArgs.set(`${ev.agent}:${ev.tool}`, ev.args);
        return [{ type: "agent_status", agent: ev.agent, status: `${ev.tool} 실행 중`, workflow: ev.tool }];
      }

      case "tool_end": {
        if (!isAgentId(ev.agent)) return [];
        const args = toolArgs.get(`${ev.agent}:${ev.tool}`) ?? {};
        return [
          { type: "message", sender: ev.agent, title: ev.tool, content: toolResultBlocks(ev.tool, args, ev.result) },
          { type: "agent_status", agent: ev.agent, status: "결과 정리 중..." },
        ];
      }

      case "agent_end": {
        if (!isAgentId(ev.agent) || AGENTS[ev.agent].kind !== "shared") return [];
        return [
          { type: "message", sender: ev.agent, title: RESULT_TITLE[ev.agent] ?? "결과", content: ev.response },
          { type: "agent_complete", agent: ev.agent, from: delegatorOf.get(ev.agent), delay: READ_MS },
        ];
      }

      case "delegation_end": {
        if (!isAgentId(ev.from) || !isAgentId(ev.to)) return [];
        return [
          { type: "session_close" },
          { type: "agent_status", agent: ev.from, status: `${AGENTS[ev.to].shortName} 결과 수신` },
        ];
      }

      case "error":
      case "done":
        return [];
    }
  };
}
