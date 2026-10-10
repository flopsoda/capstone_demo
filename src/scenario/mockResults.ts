import type { MessageBlock } from "../types/scenario";
import { COMPANY, MEETING, NEWS } from "./mockData";

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
  { kind: "label", text: "Open Action Items" },
  { kind: "list", items: [...MEETING.actionItems] },
];

/** run_company_risk_news_analysis */
export const NEWS_RISK_ANALYSIS: MessageBlock[] = [
  { kind: "badge", text: "HIGH", tone: "high" },
  {
    kind: "text",
    text: `NeuroEdge 최대 고객사 ${COMPANY.largestCustomer}이\n차세대 NPU 공급업체 다변화를 검토 중입니다.\n\n현재 ${COMPANY.largestCustomer}은\nNeuroEdge 매출의 ${COMPANY.largestCustomerShare}를 차지합니다.`,
  },
  { kind: "badge", text: "MEDIUM", tone: "medium" },
  {
    kind: "text",
    text: "CoreNPU와 NPU 메모리 구조 관련\n특허침해 소송이 진행 중입니다.",
  },
  { kind: "badge", text: "HIGH", tone: "high" },
  {
    kind: "text",
    text: "NeuroEdge 창업자 CTO가\n인수 이후 연구조직의 독립성에 대한\n우려를 공개적으로 표현했습니다.\n\n핵심 설계인력 Retention Risk가 존재합니다.",
  },
];

/** run_company_risk_news_analysis — direct user request ("악재만 다시 보여줘") */
export const NEWS_RISK_BRIEF: MessageBlock[] = [
  { kind: "label", text: "NeuroEdge Risk Brief" },
  {
    kind: "list",
    ordered: true,
    items: [
      `AlphaMobile 공급업체 다변화 — Risk: ${NEWS[0].riskLevel}`,
      `CoreNPU 특허침해 소송 — Risk: ${NEWS[1].riskLevel}`,
      `창업자 CTO 및 핵심 설계인력 Retention — Risk: ${NEWS[2].riskLevel}`,
    ],
  },
];

/** run_mna_review */
export const ADVISORY_RESULT: MessageBlock[] = [
  {
    kind: "kv",
    items: [
      { k: "Asking Price", v: COMPANY.askingValuation },
      { k: "Estimated Fair Value", v: COMPANY.estimatedFairValue },
    ],
  },
  { kind: "label", text: "Time-to-Market" },
  {
    kind: "kv",
    items: [
      { k: "Internal Development", v: COMPANY.internalNpuDevelopment },
      { k: "Acquisition + Integration", v: COMPANY.integrationAfterAcquisition },
      { k: "Expected Gain", v: COMPANY.timeToMarketGain },
    ],
  },
  { kind: "label", text: "Strategic Fit" },
  { kind: "badge", text: "HIGH", tone: "ok" },
  { kind: "label", text: "Key Risks" },
  {
    kind: "kv",
    items: [
      { k: "Customer Concentration", v: "HIGH" },
      { k: "Key Talent Retention", v: "HIGH" },
      { k: "Patent Litigation", v: "MEDIUM" },
    ],
  },
  { kind: "label", text: "Recommendation" },
  { kind: "badge", text: "CONDITIONAL PROCEED", tone: "info" },
];
