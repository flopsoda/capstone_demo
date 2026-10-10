/**
 * Fixed mock data for the NeuroEdge M&A demo.
 * Nothing here is real; all values are deterministic fixtures.
 */

export const COMPANY = {
  name: "NeuroEdge",
  business: "AI NPU Design",
  description: "가상의 AI 반도체(NPU) 설계 스타트업",
  revenue2025: "4,200억 원",
  revenueCagr3y: "31%",
  operatingMargin: "-8.2%",
  askingValuation: "2.8조 원",
  estimatedFairValue: "2.0 ~ 2.3조 원",
  largestCustomer: "AlphaMobile",
  largestCustomerShare: "46%",
  internalNpuDevelopment: "36 ~ 48 months",
  integrationAfterAcquisition: "12 ~ 18 months",
  timeToMarketGain: "약 24 ~ 30개월",
} as const;

/** Workflow Tools exposed by each specialist agent. */
export const WORKFLOWS = {
  meeting: "run_meeting_context_retrieval",
  news: "run_company_risk_news_analysis",
  advisory: "run_mna_review",
} as const;

/** Internal stages encapsulated inside run_mna_review (shown as status text only). */
export const MNA_REVIEW_STAGES = [
  "Financial Analysis",
  "Valuation",
  "Strategic Fit",
  "Risk Analysis",
  "Recommendation",
] as const;

export type RiskLevel = "HIGH" | "MEDIUM";

export interface NewsItem {
  id: string;
  headline: string;
  risk: string;
  riskLevel: RiskLevel;
}

export const NEWS: NewsItem[] = [
  {
    id: "NEWS-01",
    headline: "NeuroEdge 최대 고객사 AlphaMobile, 차세대 NPU 공급업체 다변화 검토",
    risk: "Customer concentration",
    riskLevel: "HIGH",
  },
  {
    id: "NEWS-02",
    headline: "CoreNPU, NeuroEdge를 상대로 NPU 메모리 구조 관련 특허침해 소송 제기",
    risk: "Patent litigation",
    riskLevel: "MEDIUM",
  },
  {
    id: "NEWS-03",
    headline: 'NeuroEdge 창업자 CTO, "인수 이후에도 독립적인 연구환경이 중요"',
    risk: "Key talent retention",
    riskLevel: "HIGH",
  },
];

export const MEETING = {
  date: "2026-09-15",
  title: "AI Semiconductor Strategy Meeting",
  statements: [
    { speaker: "CFO", text: "2.5조 원을 초과할 경우 가격 타당성 재검토" },
    { speaker: "CTO", text: "핵심 설계팀 유지가 인수 성공의 핵심" },
    { speaker: "CEO", text: "자체 개발 대비 최소 2년 이상 Time-to-Market 단축 필요" },
  ],
  actionItems: ["적정 기업가치 산정", "최대 고객사 의존도 확인", "핵심인력 Retention 확인"],
} as const;

export const DUE_DILIGENCE_CONDITIONS = [
  "인수 협상 기준가격 2.3조 원 이하",
  "창업자 CTO 및 핵심 설계팀 3년 Retention",
  "특허소송 잠재손실 확인",
  "AlphaMobile 계약 유지 여부 확인",
] as const;
