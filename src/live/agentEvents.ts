/**
 * Maps backend lifecycle events (src/live/api.ts) to the scenario events the
 * reducer already understands. Nothing here is scheduled or invented: each
 * backend event becomes UI state the moment it arrives.
 *
 *   agent_start (super)      → node status text
 *   delegation_start         → delegation session opens, specialist node active, edge active
 *   agent_start (specialist) → node status text
 *   agent_token              → the agent's answer grows in place (message_delta); the final
 *                              agent_end message replaces that draft
 *   tool_start               → node shows the Workflow Tool it is running (session too)
 *   tool_end                 → Workflow result message in the session
 *   agent_end (specialist)   → final answer message, node completed
 *   delegation_end           → session closes, delegator status text
 *   agent_end (super)        → the Super Agent's own final answer as a message (its node stays
 *                              active: handing the answer back is the next scenario step)
 *   a2a_start                → A2A session opens (Group Super → peer Super), peer node active, A2A edge active
 *   a2a_end                  → A2A session closes with the peer's answer as its result, peer node completed
 *   error                    → thrown by the client as LiveRunError → scenario_error
 *
 * One Super Agent run contains as many handoffs as the model decides it needs
 * (the same mapping repeats for each one) and ends with its final answer.
 *
 * Backend agent ids are per system (`meeting` exists in both the Group and the
 * K Electronics system); `resolveAgent` maps `(system, agent)` to the AgentId a
 * surface shows them under. The default keeps ids as they are.
 */

import { AGENTS, type AgentId } from "../types/agent";
import type { BadgeTone, MessageBlock, ScenarioEvent } from "../types/scenario";
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

/** Shape returned by run_company_risk_news_analysis (backend/app/workflows). */
interface CompanyRiskNewsResult {
  matched: number;
  risks: {
    category: string;
    label: string;
    level: string;
    headline: string;
    summary: string;
    key_facts: string[];
    source: { id: string; date: string; name: string };
  }[];
}

function isCompanyRiskNewsResult(v: unknown): v is CompanyRiskNewsResult {
  return typeof v === "object" && v !== null && Array.isArray((v as { risks?: unknown }).risks);
}

const RISK_LEVEL_BADGE: Record<string, { text: string; tone: BadgeTone }> = {
  HIGH: { text: "높음", tone: "high" },
  MEDIUM: { text: "중간", tone: "medium" },
  LOW: { text: "낮음", tone: "ok" },
};

function companyRiskNewsBlocks(result: unknown): MessageBlock[] {
  if (!isCompanyRiskNewsResult(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  if (result.risks.length === 0) return [{ kind: "text", text: "관련 리스크 기사를 찾지 못했습니다." }];
  return result.risks.flatMap((r): MessageBlock[] => {
    const level = RISK_LEVEL_BADGE[r.level] ?? { text: r.level, tone: "info" as const };
    return [
      { kind: "badge", text: `${r.label} · 리스크 ${level.text}`, tone: level.tone },
      { kind: "label", text: `${r.source.date} · ${r.source.name}` },
      { kind: "text", text: r.headline },
      { kind: "list", items: r.key_facts },
    ];
  });
}

/** One topic of run_news_briefing / run_personal_news_briefing (backend news_briefing.py). */
interface NewsTopic {
  importance: number;
  article_count: number;
  outlet_count: number;
  summary: string;
  key_facts: string[];
  sentiment: "positive" | "negative" | "neutral";
  risk?: { label: string; level: string } | null;
  interest?: string;
  articles: { title: string; outlet: string; date: string; url?: string }[];
}

interface NewsBriefingResult {
  query: string;
  collected: number;
  duplicates_removed: number;
  trend: string;
  so_what: string;
  topics: NewsTopic[];
  negative_alerts: string[];
  outlet_tone: { outlet: string; tone: string }[];
}

interface PersonalBriefingResult {
  subscriber: { name: string; interests: { query: string }[] };
  period: string;
  sections: { query: string; trend: string; so_what: string }[];
  topics: NewsTopic[];
  alerts: NewsTopic[];
  delivery?: { sent: string[]; failed: Record<string, string>; not_configured: string[] };
}

const SENTIMENT_BADGE: Record<string, { text: string; tone: BadgeTone }> = {
  negative: { text: "부정", tone: "high" },
  positive: { text: "긍정", tone: "ok" },
  neutral: { text: "중립", tone: "info" },
};

function newsTopicBlocks(t: NewsTopic): MessageBlock[] {
  const mood = SENTIMENT_BADGE[t.sentiment] ?? { text: t.sentiment, tone: "info" as const };
  const risk = t.risk ? ` · ${t.risk.label} ${RISK_LEVEL_BADGE[t.risk.level]?.text ?? t.risk.level}` : "";
  const lead = t.articles[0];
  return [
    { kind: "badge", text: `${t.interest ? `${t.interest} · ` : ""}중요도 ${Math.round(t.importance)} · ${mood.text}${risk}`, tone: mood.tone },
    { kind: "label", text: `${lead ? `${lead.date} · ${lead.outlet} · ` : ""}기사 ${t.article_count}건 · 매체 ${t.outlet_count}곳` },
    { kind: "text", text: t.summary },
    ...(t.key_facts.length ? [{ kind: "list" as const, items: t.key_facts }] : []),
  ];
}

function isNewsBriefingResult(v: unknown): v is NewsBriefingResult {
  return typeof v === "object" && v !== null && Array.isArray((v as { topics?: unknown }).topics) && "negative_alerts" in v;
}

function newsBriefingBlocks(result: unknown): MessageBlock[] {
  if (!isNewsBriefingResult(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  const head: MessageBlock[] = [
    { kind: "label", text: `기사 ${result.collected}건 수집 · 중복 ${result.duplicates_removed}건 제거 · 주제 ${result.topics.length}개` },
  ];
  if (result.topics.length === 0) return [...head, { kind: "text", text: "기간 안에 관련 기사를 찾지 못했습니다." }];
  if (result.trend) head.push({ kind: "label", text: "한눈에 보기" }, { kind: "text", text: result.trend });
  const tail: MessageBlock[] = [];
  if (result.so_what) tail.push({ kind: "label", text: "시사점 (So-What)" }, { kind: "text", text: result.so_what });
  const tones = result.outlet_tone.filter((o) => o.tone !== "판단 보류");
  if (tones.length) tail.push({ kind: "label", text: "매체별 논조" }, { kind: "kv", items: tones.map((o) => ({ k: o.outlet, v: o.tone })) });
  return [...head, { kind: "label", text: "주요 이슈" }, ...result.topics.flatMap(newsTopicBlocks), ...tail];
}

function isPersonalBriefingResult(v: unknown): v is PersonalBriefingResult {
  return typeof v === "object" && v !== null && Array.isArray((v as { sections?: unknown }).sections);
}

function personalBriefingBlocks(result: unknown): MessageBlock[] {
  if (!isPersonalBriefingResult(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  const blocks: MessageBlock[] = [
    { kind: "badge", text: `${result.subscriber.name} · ${result.period === "weekly" ? "주간" : "일간"} 브리핑`, tone: "info" },
    { kind: "label", text: `관심 주제: ${result.sections.map((s) => s.query).join(", ")}` },
  ];
  const trends = result.sections.filter((s) => s.trend);
  if (trends.length) blocks.push({ kind: "kv", items: trends.map((s) => ({ k: s.query, v: s.trend })) });
  blocks.push({ kind: "label", text: "주요 이슈" }, ...result.topics.flatMap(newsTopicBlocks));
  if (result.alerts.length) {
    blocks.push({ kind: "label", text: "부정 기사 알림" }, { kind: "list", items: result.alerts.map((t) => t.articles[0]?.title ?? t.summary) });
  }
  if (result.delivery) {
    const d = result.delivery;
    const failed = Object.keys(d.failed);
    blocks.push({ kind: "label", text: "발송" }, {
      kind: "kv",
      items: [
        { k: "보냄", v: d.sent.join(", ") || "없음" },
        ...(failed.length ? [{ k: "실패", v: failed.join(", ") }] : []),
        ...(d.not_configured.length ? [{ k: "설정 안 됨", v: d.not_configured.join(", ") }] : []),
      ],
    });
  }
  return blocks;
}

/** Shape returned by run_mna_review (backend/app/workflows/mna_review.py). */
interface MnaReviewResult {
  found: boolean;
  message?: string;
  persona_label: string;
  inputs: {
    financials: { revenue: string; revenue_cagr_3y: string; operating_margin: string };
    valuation: { asking: string; fair_value: string };
    customers: { largest: string; largest_share: string };
    technology: { internal_development: string; integration_after_acquisition: string; time_to_market_gain: string };
  };
  assessments: { label: string; verdict: string; level: string | null; finding: string }[];
  concerns: { label: string; level: string | null; finding: string }[];
  recommendation: string;
  recommendation_label: string;
  conditions: string[];
}

function isMnaReviewResult(v: unknown): v is MnaReviewResult {
  return typeof v === "object" && v !== null && typeof (v as { found?: unknown }).found === "boolean";
}

const RECOMMENDATION_TONE: Record<string, BadgeTone> = {
  PROCEED: "ok",
  CONDITIONAL_PROCEED: "info",
  DO_NOT_PROCEED: "high",
};

const VERDICT_TEXT: Record<string, string> = { positive: "긍정", concern: "우려", neutral: "검토" };

function assessmentText(a: { verdict: string; level: string | null; finding: string }): string {
  const level = a.level ? ` · ${RISK_LEVEL_BADGE[a.level]?.text ?? a.level}` : "";
  return `${VERDICT_TEXT[a.verdict] ?? a.verdict}${level} — ${a.finding}`;
}

function mnaReviewBlocks(result: unknown): MessageBlock[] {
  if (!isMnaReviewResult(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  if (!result.found) return [{ kind: "text", text: result.message ?? "기업 데이터를 찾지 못했습니다." }];
  const { inputs } = result;
  return [
    { kind: "badge", text: result.persona_label, tone: "info" },
    { kind: "label", text: "입력 정보" },
    {
      kind: "kv",
      items: [
        { k: "요구가격", v: inputs.valuation.asking },
        { k: "추정 적정가치", v: inputs.valuation.fair_value },
        { k: "매출 / CAGR / 영업이익률", v: `${inputs.financials.revenue} / ${inputs.financials.revenue_cagr_3y} / ${inputs.financials.operating_margin}` },
        { k: "최대 고객", v: `${inputs.customers.largest} (${inputs.customers.largest_share})` },
        { k: "자체 개발", v: inputs.technology.internal_development },
        { k: "인수 + 통합", v: inputs.technology.integration_after_acquisition },
        { k: "기대 단축 기간", v: inputs.technology.time_to_market_gain },
      ],
    },
    { kind: "label", text: `평가 항목 · ${result.persona_label} 우선순위` },
    { kind: "kv", items: result.assessments.map((a) => ({ k: a.label, v: assessmentText(a) })) },
    { kind: "label", text: "권고" },
    { kind: "badge", text: result.recommendation_label, tone: RECOMMENDATION_TONE[result.recommendation] ?? "info" },
    { kind: "label", text: "권고 조건" },
    { kind: "list", ordered: true, items: result.conditions },
  ];
}

/** Renders a tool's arguments + result. One case per Workflow Tool; unknown tools fall back to JSON. */
export function toolResultBlocks(tool: string, args: Record<string, unknown>, result: unknown): MessageBlock[] {
  const head: MessageBlock[] = [];
  if (typeof args.company === "string") {
    head.push({ kind: "label", text: "조사 대상" }, { kind: "text", text: args.company });
  }
  if (typeof args.persona === "string") {
    head.push({ kind: "label", text: "평가 관점" }, { kind: "text", text: args.persona });
  }
  if (typeof args.query === "string") {
    head.push({ kind: "label", text: "조회 질의" }, { kind: "text", text: args.query });
  }
  if (typeof args.context === "string" && args.context) {
    head.push({ kind: "label", text: "전달된 맥락" }, { kind: "text", text: args.context });
  }
  switch (tool) {
    case "run_meeting_context_retrieval":
      return [...head, ...meetingContextBlocks(result)];
    case "run_company_risk_news_analysis":
      return [...head, ...companyRiskNewsBlocks(result)];
    case "run_news_briefing":
      return [...head, ...newsBriefingBlocks(result)];
    case "run_personal_news_briefing":
      return [...head, ...personalBriefingBlocks(result)];
    case "run_mna_review":
      return [...head, ...mnaReviewBlocks(result)];
    default:
      return [...head, { kind: "text", text: JSON.stringify(result, null, 2) }];
  }
}

/** Title of an agent's final answer in the chat. */
const RESULT_TITLE: Partial<Record<AgentId, string>> = {
  meeting: "회의 맥락",
  news: "뉴스 브리핑",
  advisory: "M&A 예비 검토",
  k_super: "K전자 검토 결과",
  group_meeting: "그룹 회의 맥락",
  group_news: "그룹 뉴스 브리핑",
  group_advisory: "그룹 M&A 예비 검토",
  group_super: "그룹 최종 판단",
};

/** Maps a backend `(system, agent)` pair to the AgentId a surface shows it under. */
export type AgentResolver = (system: string | undefined, agent: string) => string;

/** The Group system's specialists get their own ids; every other id is kept. */
export const resolveForFullGraph: AgentResolver = (system, agent) =>
  system === "group" && (agent === "meeting" || agent === "news" || agent === "advisory") ? `group_${agent}` : agent;

/** Time the final answer stays readable before its delegation session collapses (UI only). */
const READ_MS = 4000;
/** Time for a closed session window to fly into its card before the next session opens (UI only). */
const COLLAPSE_SETTLE_MS = 2600;

/* ------------------------------------------------------------------ */
/* Backend events → scenario events                                    */
/* ------------------------------------------------------------------ */

/**
 * Returns a mapper that remembers who delegated to whom (needed to complete
 * the right edge when a specialist finishes). One mapper per run.
 */
export function createAgentEventMapper(resolveAgent: AgentResolver = (_system, agent) => agent) {
  const delegatorOf = new Map<AgentId, AgentId>();
  const toolArgs = new Map<string, Record<string, unknown>>();
  const id = (ev: AgentEvent, raw: string) => resolveAgent(ev.system, raw);

  return function mapAgentEvent(ev: AgentEvent): ScenarioEvent[] {
    switch (ev.type) {
      case "agent_start": {
        const agent = id(ev, ev.agent);
        if (!isAgentId(agent)) return [];
        // Specialists were already lit by delegation_start (or the chat's own root start); this is the model starting work.
        return [{ type: "agent_status", agent, status: "요청 분석 중..." }];
      }

      case "agent_token": {
        const agent = id(ev, ev.agent);
        if (!isAgentId(agent)) return [];
        // No title while it is being written: text the model writes before a tool call is only a
        // progress note and stays untitled; the final agent_end message brings the result title.
        return [{ type: "message_delta", sender: agent, text: ev.text }];
      }

      case "delegation_start": {
        const from = id(ev, ev.from);
        const to = id(ev, ev.to);
        if (!isAgentId(from) || !isAgentId(to)) return [];
        delegatorOf.set(to, from);
        return [
          { type: "message_commit", sender: from },
          { type: "session_open", communication: "delegation", from, to, content: `"${ev.task}"` },
          { type: "agent_start", agent: to, from, communication: "delegation", status: "요청 수신" },
          { type: "agent_status", agent: from, status: `${AGENTS[to].shortName} 위임 중...` },
        ];
      }

      case "a2a_start": {
        // The Group's A2A Tool asks a peer system's Super Agent. The peer keeps its own id.
        const from = id(ev, ev.from);
        const to = ev.to;
        if (!isAgentId(from) || !isAgentId(to)) return [];
        delegatorOf.set(to, from);
        return [
          { type: "message_commit", sender: from },
          { type: "session_open", communication: "a2a", title: "A2A 요청", from, to, content: ev.request },
          { type: "agent_start", agent: to, from, communication: "a2a", status: "A2A 요청 수신" },
          { type: "agent_status", agent: from, status: `${AGENTS[to].shortName} A2A 요청 · 응답 대기 중` },
        ];
      }

      case "tool_start": {
        const agent = id(ev, ev.agent);
        if (!isAgentId(agent)) return [];
        toolArgs.set(`${agent}:${ev.tool}`, ev.args);
        return [
          { type: "message_commit", sender: agent },
          { type: "agent_status", agent, status: `${ev.tool} 실행 중`, workflow: ev.tool },
        ];
      }

      case "tool_end": {
        const agent = id(ev, ev.agent);
        if (!isAgentId(agent)) return [];
        const args = toolArgs.get(`${agent}:${ev.tool}`) ?? {};
        return [
          { type: "message", sender: agent, title: ev.tool, content: toolResultBlocks(ev.tool, args, ev.result) },
          { type: "agent_status", agent, status: "결과 정리 중..." },
        ];
      }

      case "agent_end": {
        const agent = id(ev, ev.agent);
        if (!isAgentId(agent)) return [];
        const message: ScenarioEvent = {
          type: "message",
          sender: agent,
          title: RESULT_TITLE[agent] ?? "결과",
          content: ev.response,
          final: true,
        };
        if (AGENTS[agent].kind === "super") {
          // The Super Agent answered without another handoff: this is its final
          // answer. The node is not completed here — returning the answer to the
          // requester (A2A response, or the run's `done`) is what completes it.
          return [message, { type: "agent_status", agent, status: "최종 응답 작성 완료", delay: READ_MS }];
        }
        return [message, { type: "agent_complete", agent, from: delegatorOf.get(agent), delay: READ_MS }];
      }

      case "delegation_end": {
        const from = id(ev, ev.from);
        const to = id(ev, ev.to);
        if (!isAgentId(from) || !isAgentId(to)) return [];
        return [
          { type: "session_close", delay: COLLAPSE_SETTLE_MS },
          { type: "agent_status", agent: from, status: `${AGENTS[to].shortName} 결과 수신` },
        ];
      }

      case "a2a_end": {
        const from = id(ev, ev.from);
        const to = ev.to;
        if (!isAgentId(from) || !isAgentId(to)) return [];
        return [
          { type: "session_close", resultTitle: "A2A 응답", result: ev.response, delay: COLLAPSE_SETTLE_MS },
          { type: "agent_complete", agent: to, from },
          { type: "agent_status", agent: from, status: `${AGENTS[to].shortName} 응답 수신 · 최종 판단 중` },
        ];
      }

      case "error":
      case "done":
        return [];
    }
  };
}
