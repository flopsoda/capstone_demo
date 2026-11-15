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
  error?: string;
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
  if (result.error) return [{ kind: "text", text: result.error }];
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

/** One topic (event) of run_news_briefing (backend news_briefing.py). */
interface NewsTopic {
  importance: number;
  article_count: number;
  outlet_count: number;
  independent_outlet_count?: number;
  evidence?: { body: number; snippet: number; title: number };
  summary: string;
  key_facts: string[];
  sentiment: "positive" | "negative" | "neutral";
  risk?: { label: string; level: string } | null;
  articles: { title: string; outlet: string; date: string; url?: string; content_level?: string }[];
}

interface NewsBriefingResult {
  query: string;
  dataset?: "live" | "demo";
  collected: number;
  duplicates_removed?: number;
  content_levels?: Record<string, number>;
  trend: string;
  so_what: string;
  topics: NewsTopic[];
  negative_alerts: string[];
  outlet_tone?: { outlet: string; tone: string }[];
  freshness?: { fetched_now: number; discovered_now: number; reused_from_store: number; feeds_polled_now: number };
  coverage?: { status: string; stored_matches: number };
  error?: string;
}

const SENTIMENT_BADGE: Record<string, { text: string; tone: BadgeTone }> = {
  negative: { text: "부정", tone: "high" },
  positive: { text: "긍정", tone: "ok" },
  neutral: { text: "중립", tone: "info" },
};

const CONTENT_LEVEL_TEXT: Record<string, string> = { full: "본문", partial: "본문 일부", snippet: "요약", title: "제목만" };

function newsTopicBlocks(t: NewsTopic): MessageBlock[] {
  const mood = SENTIMENT_BADGE[t.sentiment] ?? { text: t.sentiment, tone: "info" as const };
  const risk = t.risk ? ` · ${t.risk.label} ${RISK_LEVEL_BADGE[t.risk.level]?.text ?? t.risk.level}` : "";
  const lead = t.articles[0];
  const sources = t.articles
    .slice(0, 3)
    .map((a) => `${a.date} ${a.outlet}${a.content_level ? ` (${CONTENT_LEVEL_TEXT[a.content_level] ?? a.content_level})` : ""}${a.url ? ` ${a.url}` : ""}`);
  return [
    { kind: "badge", text: `중요도 ${Math.round(t.importance)} · ${mood.text}${risk}`, tone: mood.tone },
    {
      kind: "label",
      text:
        `${lead ? `${lead.date} · ${lead.outlet} · ` : ""}기사 ${t.article_count}건 · 매체 ${t.outlet_count}곳` +
        (t.independent_outlet_count !== undefined && t.independent_outlet_count < t.outlet_count ? ` (독립 보도 ${t.independent_outlet_count}곳)` : "") +
        (t.evidence ? ` · 본문 확보 ${t.evidence.body}/${t.article_count}` : ""),
    },
    { kind: "text", text: t.summary },
    ...(t.key_facts.length ? [{ kind: "list" as const, items: t.key_facts }] : []),
    ...(sources.length ? [{ kind: "list" as const, items: sources }] : []),
  ];
}

function isNewsBriefingResult(v: unknown): v is NewsBriefingResult {
  return typeof v === "object" && v !== null && Array.isArray((v as { topics?: unknown }).topics);
}

function newsBriefingBlocks(result: unknown): MessageBlock[] {
  if (!isNewsBriefingResult(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  if (result.error) return [{ kind: "text", text: result.error }];
  const levels = result.content_levels ?? {};
  const withBody = (levels.full ?? 0) + (levels.partial ?? 0);
  const head: MessageBlock[] = [
    {
      kind: "label",
      text: `기사 ${result.collected}건 (본문 확보 ${withBody}건) · 중복 ${result.duplicates_removed ?? 0}건 제거 · 주제 ${result.topics.length}개${result.dataset === "demo" ? " · 데모 데이터" : ""}`,
    },
  ];
  if (result.freshness) {
    const f = result.freshness;
    head.push({ kind: "label", text: `이번 요청에서 새로 발견 ${f.discovered_now}건 · 본문 수집 ${f.fetched_now}건 · 저장된 기사 재사용 ${f.reused_from_store}건` });
  }
  if (result.topics.length === 0) return [...head, { kind: "text", text: "기간 안에 관련 기사를 찾지 못했습니다." }];
  if (result.trend) head.push({ kind: "label", text: "한눈에 보기" }, { kind: "text", text: result.trend });
  const tail: MessageBlock[] = [];
  if (result.so_what) tail.push({ kind: "label", text: "시사점 (So-What)" }, { kind: "text", text: result.so_what });
  const tones = (result.outlet_tone ?? []).filter((o) => o.tone !== "판단 보류");
  if (tones.length) tail.push({ kind: "label", text: "매체별 논조" }, { kind: "kv", items: tones.map((o) => ({ k: o.outlet, v: o.tone })) });
  return [...head, { kind: "label", text: "주요 이슈" }, ...result.topics.flatMap(newsTopicBlocks), ...tail];
}

interface MnaInputs {
  financials: { revenue: string; revenue_cagr_3y: string; operating_margin: string };
  valuation: { asking: string; fair_value: string };
  customers: { largest: string; largest_share: string };
  technology: { internal_development: string; integration_after_acquisition: string; time_to_market_gain: string };
}

interface MnaAssessment {
  label: string;
  verdict: string;
  level: string | null;
  finding: string;
}

/**
 * Shapes returned by run_mna_review (backend/app/library/workflows): a single-perspective review
 * (mna_review.py; What-if adds the same fields, mna_scenario.py) or, for persona "ALL", the
 * CEO·CFO·CTO summary (mna_executive_summary.py, mode "executive_summary").
 */
interface MnaReviewResult {
  found: boolean;
  message?: string;
  mode?: string;
  persona_label: string;
  inputs: MnaInputs;
  assessments: MnaAssessment[];
  recommendation: string;
  recommendation_label: string;
  conditions: string[];
}

interface MnaExecutiveSummaryResult {
  found: true;
  mode: "executive_summary";
  inputs: MnaInputs;
  executive_reviews: Record<string, { assessments: MnaAssessment[] }>;
  key_risks: { label: string; level: string | null; finding: string }[];
  recommendation: string;
  recommendation_label: string;
  conditions: string[];
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

function isMnaReviewResult(v: unknown): v is Record<string, unknown> & { found: boolean; message?: string } {
  return isObject(v) && typeof v.found === "boolean";
}

/** A found single-perspective (or What-if) review with every field the renderer reads. */
function isPersonaReview(v: Record<string, unknown>): v is Record<string, unknown> & MnaReviewResult {
  return isObject(v.inputs) && Array.isArray(v.assessments) && Array.isArray(v.conditions);
}

function isExecutiveSummary(v: Record<string, unknown>): v is Record<string, unknown> & MnaExecutiveSummaryResult {
  return (
    v.mode === "executive_summary" &&
    isObject(v.inputs) &&
    isObject(v.executive_reviews) &&
    Array.isArray(v.key_risks) &&
    Array.isArray(v.conditions)
  );
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

function mnaInputBlocks(inputs: MnaInputs): MessageBlock[] {
  return [
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
  ];
}

function mnaRecommendationBlocks(result: { recommendation: string; recommendation_label: string; conditions: string[] }): MessageBlock[] {
  return [
    { kind: "label", text: "권고" },
    { kind: "badge", text: result.recommendation_label, tone: RECOMMENDATION_TONE[result.recommendation] ?? "info" },
    { kind: "label", text: "권고 조건" },
    { kind: "list", ordered: true, items: result.conditions },
  ];
}

/** persona "ALL": each perspective's assessments, the merged key risks, one recommendation. */
function mnaExecutiveSummaryBlocks(result: MnaExecutiveSummaryResult): MessageBlock[] {
  const perspectives = Object.entries(result.executive_reviews).flatMap(([persona, review]): MessageBlock[] =>
    Array.isArray(review?.assessments)
      ? [
          { kind: "label", text: `${persona} 관점 평가` },
          { kind: "kv", items: review.assessments.map((a) => ({ k: a.label, v: assessmentText(a) })) },
        ]
      : [],
  );
  const risks = result.key_risks.map((r) => {
    const level = r.level ? `${RISK_LEVEL_BADGE[r.level]?.text ?? r.level} · ` : "";
    return `${level}${r.label} — ${r.finding}`;
  });
  const riskBlocks: MessageBlock[] = risks.length
    ? [
        { kind: "label", text: "핵심 위험" },
        { kind: "list", items: risks },
      ]
    : [];
  return [
    { kind: "badge", text: "CEO·CFO·CTO 종합", tone: "info" },
    ...mnaInputBlocks(result.inputs),
    ...perspectives,
    ...riskBlocks,
    ...mnaRecommendationBlocks(result),
  ];
}

function mnaReviewBlocks(result: unknown): MessageBlock[] {
  if (!isMnaReviewResult(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  if (!result.found) return [{ kind: "text", text: result.message ?? "기업 데이터를 찾지 못했습니다." }];
  if (isExecutiveSummary(result)) return mnaExecutiveSummaryBlocks(result);
  // A shape this renderer does not know (a newer mode, an error payload): show it as is.
  if (!isPersonaReview(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  return [
    { kind: "badge", text: result.persona_label, tone: "info" },
    ...mnaInputBlocks(result.inputs),
    { kind: "label", text: `평가 항목 · ${result.persona_label} 우선순위` },
    { kind: "kv", items: result.assessments.map((a) => ({ k: a.label, v: assessmentText(a) })) },
    ...mnaRecommendationBlocks(result),
  ];
}

/** Per-tool rendering of a result. Unknown tools fall back to JSON. */
function resultBlocks(tool: string, result: unknown): MessageBlock[] {
  switch (tool) {
    case "run_meeting_context_retrieval":
      return meetingContextBlocks(result);
    case "run_company_risk_news_analysis":
      return companyRiskNewsBlocks(result);
    case "run_news_briefing":
      return newsBriefingBlocks(result);
    case "run_mna_review":
      return mnaReviewBlocks(result);
    default:
      return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  }
}

/**
 * Renders a tool's arguments + result. A result whose shape a renderer does not expect is
 * shown as JSON instead of throwing: a rendering problem must not abort the whole run.
 */
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
  let body: MessageBlock[];
  try {
    body = resultBlocks(tool, result);
  } catch {
    body = [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  }
  return [...head, ...body];
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
