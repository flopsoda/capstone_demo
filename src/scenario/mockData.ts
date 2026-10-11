/**
 * Fixed mock data for the NeuroEdge M&A demo.
 * Nothing here is real; all values are deterministic fixtures.
 */

export const COMPANY = {
  name: "뉴로엣지",
  business: "AI NPU 설계",
  description: "가상의 AI 반도체(NPU) 설계 스타트업",
  revenue2025: "4,200억 원",
  revenueCagr3y: "31%",
  operatingMargin: "-8.2%",
  askingValuation: "2.8조 원",
  estimatedFairValue: "2.0 ~ 2.3조 원",
  largestCustomer: "알파모바일",
  largestCustomerShare: "46%",
  internalNpuDevelopment: "36 ~ 48개월",
  integrationAfterAcquisition: "12 ~ 18개월",
  timeToMarketGain: "약 24 ~ 30개월",
  litigant: "코어NPU",
} as const;

/** Workflow Tools exposed by each specialist agent (identifiers stay as-is). */
export const WORKFLOWS = {
  meeting: "run_meeting_context_retrieval",
  news: "run_company_risk_news_analysis",
  advisory: "run_mna_review",
} as const;

/** Internal stages encapsulated inside run_mna_review (shown as status text only). */
export const MNA_REVIEW_STAGES = [
  "재무 분석",
  "기업가치 산정",
  "전략적 적합성 평가",
  "리스크 분석",
  "권고안 도출",
] as const;

export type RiskLevel = "HIGH" | "MEDIUM";

export const RISK_LABEL: Record<RiskLevel, string> = {
  HIGH: "높음",
  MEDIUM: "중간",
};

export interface NewsItem {
  id: string;
  headline: string;
  risk: string;
  riskLevel: RiskLevel;
}

export const NEWS: NewsItem[] = [
  {
    id: "NEWS-01",
    headline: "뉴로엣지 최대 고객사 알파모바일, 차세대 NPU 공급업체 다변화 검토",
    risk: "고객 집중도",
    riskLevel: "HIGH",
  },
  {
    id: "NEWS-02",
    headline: "코어NPU, 뉴로엣지를 상대로 NPU 메모리 구조 관련 특허침해 소송 제기",
    risk: "특허 소송",
    riskLevel: "MEDIUM",
  },
  {
    id: "NEWS-03",
    headline: '뉴로엣지 창업자 CTO, "인수 이후에도 독립적인 연구환경이 중요"',
    risk: "핵심인력 이탈",
    riskLevel: "HIGH",
  },
];

export const MEETING = {
  date: "2026-09-15",
  title: "AI 반도체 전략회의",
  statements: [
    { speaker: "CFO", text: "2.5조 원을 초과할 경우 가격 타당성 재검토" },
    { speaker: "CTO", text: "핵심 설계팀 유지가 인수 성공의 핵심" },
    { speaker: "CEO", text: "자체 개발 대비 최소 2년 이상 시장 진입 시점 단축 필요" },
  ],
  actionItems: ["적정 기업가치 산정", "최대 고객사 의존도 확인", "핵심인력 유지 가능 여부 확인"],
} as const;

export const DUE_DILIGENCE_CONDITIONS = [
  "인수 협상 기준가격 2.3조 원 이하",
  "창업자 CTO 및 핵심 설계팀 3년 근속 조건 확보",
  "특허소송 잠재손실 확인",
  "알파모바일 계약 유지 여부 확인",
] as const;
