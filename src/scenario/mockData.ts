/**
 * Fixed mock data for the NeuroEdge M&A demo — used only by the remaining
 * mock steps (1–2, 6–8). Nothing here is real; all values are deterministic
 * fixtures. Steps 3–5 show what the backend actually returned.
 */

export const COMPANY = {
  name: "뉴로엣지",
  askingValuation: "2.8조 원",
  estimatedFairValue: "2.0 ~ 2.3조 원",
} as const;

export const DUE_DILIGENCE_CONDITIONS = [
  "인수 협상 기준가격 2.3조 원 이하",
  "창업자 CTO 및 핵심 설계팀 3년 근속 조건 확보",
  "특허소송 잠재손실 확인",
  "알파모바일 계약 유지 여부 확인",
] as const;
