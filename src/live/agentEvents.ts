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
  impact?: "positive" | "negative" | "mixed" | "neutral" | "unknown";
  implication?: string;
  divergent_coverage?: boolean;
  stance_by_outlet?: { outlet: string; stance: string }[];
  risk?: { label: string; level: string } | null;
  articles: { title: string; outlet: string; date: string; url?: string; content_level?: string }[];
}

interface NewsBriefingResult {
  query: string;
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
const IMPACT_BADGE: Record<string, { text: string; tone: BadgeTone }> = {
  positive: { text: "긍정", tone: "ok" },
  negative: { text: "부정", tone: "high" },
  mixed: { text: "엇갈림", tone: "warn" },
  neutral: { text: "중립", tone: "info" },
  unknown: { text: "판단 보류", tone: "info" },
};
const STANCE_TEXT: Record<string, string> = { favorable: "우호", critical: "비판", neutral: "중립", unknown: "판단 불가" };

function newsTopicBlocks(t: NewsTopic): MessageBlock[] {
  const mood = (t.impact ? IMPACT_BADGE[t.impact] : SENTIMENT_BADGE[t.sentiment]) ?? { text: t.sentiment, tone: "info" as const };
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
    ...(t.implication ? [{ kind: "text" as const, text: `시사점: ${t.implication}` }] : []),
    ...(t.divergent_coverage && t.stance_by_outlet
      ? [{ kind: "label" as const, text: `보도 관점 엇갈림: ${t.stance_by_outlet.map((s) => `${s.outlet} ${STANCE_TEXT[s.stance] ?? s.stance}`).join(" · ")}` }]
      : []),
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
      text: `기사 ${result.collected}건 (본문 확보 ${withBody}건) · 중복 ${result.duplicates_removed ?? 0}건 제거 · 주제 ${result.topics.length}개`,
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

/**
 * Shapes returned by the Advisory Tools (backend/app/library/workflows/advisory): run_mna_review gives facts and
 * computed figures (each with an id and a display string) but no recommendation; the recommendation is the advisory
 * agent's, recorded with record_advisory_opinion (accepted, or problems to fix) together with a report reference.
 */
interface MnaAnalysisResult {
  found: boolean;
  message?: string;
  price: { label: string; trillion_krw: number };
  figures: Record<string, string[]>; // group → "id — label: value (basis)"
  checks: { criterion: string; actual: string; met: boolean }[];
  scenarios: { id: string; name: string; notes: string[] }[];
}

interface AdvisoryRecordResult {
  accepted: boolean;
  problems?: string[];
  review_id?: string;
  recommendation?: string;
  recommendation_label?: string;
  evaluation_type?: string;
  artifacts?: { title: string; id: string }[];
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;

function isMnaAnalysis(v: unknown): v is MnaAnalysisResult {
  return isObject(v) && typeof v.found === "boolean" && (!v.found || (isObject(v.figures) && isObject(v.price)));
}

const RECOMMENDATION_TONE: Record<string, BadgeTone> = {
  PROCEED: "ok",
  CONDITIONAL_PROCEED: "info",
  DO_NOT_PROCEED: "high",
};

const KEY_FIGURES = ["dcf.equity_value", "price.premium_vs_dcf", "val.sensitivity_low", "val.sensitivity_high", "plan.break_even_year",
  "cust.largest_share", "cust.po_coverage", "tech.ttm_gain", "people.retention_open"];

function mnaReviewBlocks(result: unknown): MessageBlock[] {
  if (!isMnaAnalysis(result)) return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  if (!result.found) return [{ kind: "text", text: result.message ?? "기업 데이터를 찾지 못했습니다." }];
  const lines = Object.values(result.figures).flat();
  // "A1.dcf.equity_value — DCF 주주가치: 21,493억 원 (…)" → the base figure (no scenario segment) or a scenario's
  const figure = (key: string, scenario?: string) =>
    lines.find((l) => l.split(" — ")[0].endsWith(`.${key}`) && (scenario ? l.includes(`.${scenario}.`) : !/\.S\d+\./.test(l.split(" — ")[0])));
  const kv = (line: string) => {
    const text = line.split(" — ").slice(1).join(" — ");
    const at = text.indexOf(": ");
    return { k: text.slice(0, at), v: text.slice(at + 2) };
  };
  const blocks: MessageBlock[] = [
    { kind: "badge", text: `${result.price.label} ${result.price.trillion_krw}조 원 기준`, tone: "info" },
    { kind: "label", text: "주요 수치 (권고 없음 — 경영자문 에이전트가 판단)" },
    { kind: "kv", items: KEY_FIGURES.map((k) => figure(k)).filter((l): l is string => Boolean(l)).map(kv) },
  ];
  if (result.checks.length) {
    blocks.push({ kind: "label", text: "회의 기준 대조" },
      { kind: "list", items: result.checks.map((c) => `${c.met ? "충족" : "미충족"} — ${c.criterion} (평가 ${c.actual})`) });
  }
  for (const s of result.scenarios) {
    const extra = ["dcf.equity_value", "delta.equity_value"].map((k) => figure(k, s.id)).filter((l): l is string => Boolean(l))
      .map((l) => { const { k, v } = kv(l); return `${k}: ${v}`; });
    blocks.push({ kind: "label", text: `시나리오 · ${s.name}` }, { kind: "list", items: [...s.notes, ...extra] });
  }
  return blocks;
}

function advisoryRecordBlocks(result: unknown): MessageBlock[] {
  if (!isObject(result) || typeof result.accepted !== "boolean") return [{ kind: "text", text: JSON.stringify(result, null, 2) }];
  const r = result as unknown as AdvisoryRecordResult;
  if (!r.accepted) return [{ kind: "label", text: "판단 기록 보류 — 고칠 점" }, { kind: "list", items: r.problems ?? [] }];
  const blocks: MessageBlock[] = [
    { kind: "badge", text: r.recommendation_label ?? r.recommendation ?? "", tone: RECOMMENDATION_TONE[r.recommendation ?? ""] ?? "info" },
    { kind: "kv", items: [{ k: "판단 id", v: r.review_id ?? "" }, { k: "평가 종류", v: r.evaluation_type ?? "" }] },
  ];
  if (r.artifacts?.length) blocks.push({ kind: "list", items: r.artifacts.map((a) => `보고서: ${a.title} (문서번호 ${a.id})`) });
  return blocks;
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
    case "record_advisory_opinion":
    case "add_advisory_details":
      return advisoryRecordBlocks(result);
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

      case "delegation_skipped": {
        // Nothing ran: the user may not use `to` (principal.agents), or `to` is already working up the chain (reason "cycle").
        const from = id(ev, ev.from);
        const to = id(ev, ev.to);
        if (!isAgentId(from) || !isAgentId(to)) return [];
        const why = ev.reason === "cycle" ? "되돌아가는 위임" : "사용 권한 없음";
        return [{ type: "agent_status", agent: from, status: `${AGENTS[to].shortName} ${why} · 건너뜀` }];
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

      default:
        // An event type this UI does not know yet (a newer backend): show nothing rather than break the stream.
        return [];
    }
  };
}
