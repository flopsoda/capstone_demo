import { WORKFLOWS } from "../scenario/mockData";
import type { MessageBlock, ScenarioEvent } from "../types/scenario";
import type { AgentRun, ToolCallRecord } from "./api";

/* ------------------------------------------------------------------ */
/* run_meeting_context_retrieval result → chat blocks                  */
/* ------------------------------------------------------------------ */

/** Shape returned by the backend Workflow Tool (backend/app/workflows). */
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
  if (!isMeetingContextResult(result)) {
    return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  }
  if (result.meetings.length === 0) {
    return [{ kind: "text", text: "관련 회의 기록을 찾지 못했습니다." }];
  }
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

/** One block list per Workflow Tool call: the query the agent chose plus the data it got back. */
export function toolCallBlocks(call: ToolCallRecord): MessageBlock[] {
  const query = typeof call.args.query === "string" ? call.args.query : undefined;
  return [
    ...(query ? [{ kind: "label", text: "조회 질의" } as const, { kind: "text", text: query } as const] : []),
    ...meetingContextBlocks(call.result),
  ];
}

/* ------------------------------------------------------------------ */
/* Meeting Agent run → scenario events (Step 3)                        */
/* ------------------------------------------------------------------ */

export const MEETING_RESULT_TITLE = "회의 맥락";

/**
 * Replays a completed Meeting Agent delegation with the same rhythm as the
 * mock: open the delegation session, light the node with its Workflow Tool,
 * show what the tool returned, then the agent's own summary, then close.
 */
export function meetingDelegationEvents(
  run: AgentRun,
  timing: { hop: number; short: number; read: number; collapse: number },
): ScenarioEvent[] {
  return [
    {
      type: "session_open",
      communication: "delegation",
      from: "k_super",
      to: "meeting",
      content: `"${run.request}"`,
      delay: timing.hop,
    },
    {
      type: "agent_start",
      agent: "meeting",
      from: "k_super",
      communication: "delegation",
      workflow: WORKFLOWS.meeting,
      status: "회의 기록 조회 중...",
      delay: timing.short,
    },
    ...run.tool_calls.map(
      (call): ScenarioEvent => ({
        type: "message",
        sender: "meeting",
        title: call.name,
        content: toolCallBlocks(call),
        delay: timing.read,
      }),
    ),
    {
      type: "message",
      sender: "meeting",
      title: MEETING_RESULT_TITLE,
      content: run.response,
      delay: timing.read,
    },
    { type: "agent_complete", agent: "meeting", from: "k_super", delay: 0 },
    { type: "agent_status", agent: "k_super", status: "미팅 에이전트 결과 수신", delay: 0 },
    { type: "session_close", delay: timing.collapse },
  ];
}
