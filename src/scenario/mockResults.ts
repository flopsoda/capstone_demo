import type { MessageBlock } from "../types/scenario";
import { COMPANY, MEETING, NEWS, RISK_LABEL } from "./mockData";

/**
 * Structured mock results returned by each specialist agent's Workflow Tool.
 * Shared between the orchestrated scenario and the direct-chat tabs so that
 * the same agent core visibly produces the same output either way.
 */

/** run_meeting_context_retrieval */
export const MEETING_CONTEXT: MessageBlock[] = [
  { kind: "label", text: `${MEETING.date} · ${MEETING.title}` },
  {
    kind: "kv",
    items: MEETING.statements.map((s) => ({ k: s.speaker, v: s.text })),
  },
  { kind: "label", text: "미완료 Action Item" },
  { kind: "list", items: [...MEETING.actionItems] },
];

/** run_company_risk_news_analysis — fallback only; the live News Agent answers the News tab. */
export const NEWS_RISK_BRIEF: MessageBlock[] = [
  { kind: "label", text: `${COMPANY.name} 리스크 요약` },
  {
    kind: "list",
    ordered: true,
    items: [
      `${COMPANY.largestCustomer} 공급업체 다변화 — 리스크: ${RISK_LABEL[NEWS[0].riskLevel]}`,
      `${COMPANY.litigant} 특허침해 소송 — 리스크: ${RISK_LABEL[NEWS[1].riskLevel]}`,
      `창업자 CTO 및 핵심 설계인력 이탈 — 리스크: ${RISK_LABEL[NEWS[2].riskLevel]}`,
    ],
  },
];

/** run_mna_review */
export const ADVISORY_RESULT: MessageBlock[] = [
  {
    kind: "kv",
    items: [
      { k: "요구가격", v: COMPANY.askingValuation },
      { k: "추정 적정가치", v: COMPANY.estimatedFairValue },
    ],
  },
  { kind: "label", text: "시장 진입 시점" },
  {
    kind: "kv",
    items: [
      { k: "자체 개발", v: COMPANY.internalNpuDevelopment },
      { k: "인수 + 통합", v: COMPANY.integrationAfterAcquisition },
      { k: "기대 단축 기간", v: COMPANY.timeToMarketGain },
    ],
  },
  { kind: "label", text: "전략적 적합성" },
  { kind: "badge", text: "높음", tone: "ok" },
  { kind: "label", text: "주요 리스크" },
  {
    kind: "kv",
    items: [
      { k: "고객 집중도", v: "높음" },
      { k: "핵심인력 이탈", v: "높음" },
      { k: "특허 소송", v: "중간" },
    ],
  },
  { kind: "label", text: "권고" },
  { kind: "badge", text: "조건부 진행", tone: "info" },
];
