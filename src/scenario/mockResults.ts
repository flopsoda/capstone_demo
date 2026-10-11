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

/** run_company_risk_news_analysis */
export const NEWS_RISK_ANALYSIS: MessageBlock[] = [
  { kind: "badge", text: "리스크 높음", tone: "high" },
  {
    kind: "text",
    text: `${COMPANY.name} 최대 고객사 ${COMPANY.largestCustomer}이\n차세대 NPU 공급업체 다변화를 검토 중입니다.\n\n현재 ${COMPANY.largestCustomer}은\n${COMPANY.name} 매출의 ${COMPANY.largestCustomerShare}를 차지합니다.`,
  },
  { kind: "badge", text: "리스크 중간", tone: "medium" },
  {
    kind: "text",
    text: `${COMPANY.litigant}와 NPU 메모리 구조 관련\n특허침해 소송이 진행 중입니다.`,
  },
  { kind: "badge", text: "리스크 높음", tone: "high" },
  {
    kind: "text",
    text: `${COMPANY.name} 창업자 CTO가\n인수 이후 연구조직의 독립성에 대한\n우려를 공개적으로 표현했습니다.\n\n핵심 설계인력 이탈 리스크가 존재합니다.`,
  },
];

/** run_company_risk_news_analysis — direct user request ("악재만 다시 보여줘") */
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
